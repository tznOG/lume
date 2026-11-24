import re
import json
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from .network import fetch, canonical, hostname, resolve_storefront, USER_AGENT, FetchError
from .qualification import EMAIL_RE, valid_contact, stock_signals, drop_signals
from .company import find_cnpjs
from . import screening, business


def inspect_store(data, fetcher=fetch, pause=time.sleep, resolver=resolve_storefront):
    data = {**data, "contacts": [c for c in data.get("contacts", []) if c.get('via') != 'Site público' or data.get('contacts_manual')], "evidence": [], "stock_signals": [], "drop_signals": drop_signals(data.get("apps", [])), "checkout_links": []}
    data.pop("crawl_error", None)
    data.update(country_evidence=[], country_conflict=False, store_active=False, topic_verified=False, catalog_terms=[])
    if data.get('search_topic') and not data.get('country_manual'):
        data.update(country='', country_source='')
    if data.get('search_topic') and not data.get('manual_shopify'):
        data.update(shopify_verified=False, platform_source='')
    if not data.get('cnpj_manual'):
        data.update(cnpj='', cnpj_source='')
    base = "https://" + data["domain"]
    if data["domain"].endswith(".myshopify.com"):
        try:
            target = resolver(base)
            target_host = hostname(target)
            if target_host in ("shopify.com", "www.shopify.com", "accounts.shopify.com", "admin.shopify.com"):
                raise FetchError("A loja redirecionou para uma página da plataforma; revise manualmente.")
            base = "https://" + target_host
            data["storefront_url"] = base
            data["aliases"] = list(set(data.get("aliases", []) + [target_host]))
        except (ValueError, OSError) as exc:
            data["crawl_error"] = str(exc)[:250]
            return data
    root = canonical(base)
    robots = RobotFileParser()
    try:
        text, _ = fetcher(base + "/robots.txt", root)
        robots.parse(text.splitlines())
    except (ValueError, OSError) as exc:
        # A missing robots file allows crawling; access failures do not.
        if "HTTP 404" not in str(exc):
            data["crawl_error"] = "Não foi possível verificar robots.txt; revise a loja manualmente."
            return data
        robots.parse([])
    urls = [base + "/"]
    delay = robots.crawl_delay(USER_AGENT) or 1
    if delay > 10:
        data["crawl_error"] = "O site pede intervalo de leitura superior a 10 segundos; revise manualmente."
        return data
    seen_contacts = {(c["type"], c["value"].lower()) for c in data["contacts"]}
    cnpjs = []
    visited = set()
    for _ in range(4):
        if not urls:
            break
        url = urls.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if not robots.can_fetch(USER_AGENT, url):
            data["crawl_error"] = "Página bloqueada por robots.txt; revise manualmente."
            continue
        pause(max(delay, 1))
        try:
            html, final_url = fetcher(url, root)
        except (ValueError, OSError, TimeoutError) as exc:
            data["crawl_error"] = str(exc)[:250]
            continue
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script"):
            text = script.get_text()
            match = re.search(r'Shopify\.shop\s*=\s*["\x27]([a-z0-9-]+\.myshopify\.com)', text, re.I)
            if match:
                data["shop_id"] = match[1].lower()
                data["shopify_verified"] = True
                data["platform_source"] = final_url
        if sum(bool(re.search(pattern, html, re.I)) for pattern in (r'cdn\.shopify\.com/', r'Shopify\.theme\s*=', r'shopify-section')) >= 2:
            data["shopify_verified"] = True
            data["platform_source"] = final_url
        links = [(str(a.get("href", "")), a.get_text(" ", strip=True)) for a in soup.find_all("a", href=True)]
        structured_contacts = business.organization_contacts(soup)
        for item in business.checkout_links(soup, final_url):
            if item['host'] not in {r['host'] for r in data['checkout_links']}:
                data['checkout_links'].append(item)
        screening.inspect_page(data, soup, soup.get_text(' ', strip=True), final_url, home=(url == base + '/'))
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        visible = soup.get_text(" ", strip=True)
        if url == base + '/':
            data['catalog_terms'] = list(dict.fromkeys(re.findall(r'[a-z0-9]+', screening.fold(visible))))[:3000]
        for value in find_cnpjs(visible):
            if value not in {item['value'] for item in cnpjs}:
                cnpjs.append({'value': value, 'source': final_url})
        stock = stock_signals(visible)
        drops = drop_signals([], visible)
        data["stock_signals"].extend({"text": s, "url": final_url} for s in stock)
        data["drop_signals"].extend(drops)
        data["evidence"].append({"url": final_url, "stock": stock, "dropshipping": drops})
        found = structured_contacts + [("email", e) for e in EMAIL_RE.findall(visible)]
        # Formatted Brazilian phone numbers also appear as plain text in footers.
        phone_pattern = r"(?<![\w+])(?:\+55[\s.-]*)?(?:\([1-9][0-9]\)\s*|[1-9][0-9][\s.-]+)[2-9][0-9]{3,4}[\s.-]?[0-9]{4}(?![0-9])"
        found.extend(("phone", m[0]) for m in re.finditer(phone_pattern, visible))
        for href, label in links:
            if href.startswith("mailto:"):
                found.extend(("email", e) for e in EMAIL_RE.findall(href.split("?")[0]))
            elif href.startswith("tel:"):
                found.append(("phone", href[4:]))
            elif re.match(r"https://(?:api\.)?whatsapp\.com/|https://wa\.me/", href):
                match = re.search(r"(?:phone=|wa.me/)(\+?\d{10,15})(?:\D|$)", href)
                if match:
                    found.append(("whatsapp", match[1]))
            next_url = urljoin(final_url, href).split("#")[0].split("?")[0]
            try:
                if canonical(next_url) == root and urlsplit(next_url).scheme in ("http", "https") and re.search(r"contat|contact|sobre|about|envio|shipping|quem-somos|privacidade|privacy|termos|terms|policies", href + " " + label, re.I):
                    if next_url not in visited and next_url not in urls:
                        urls.append(next_url)
            except ValueError:
                pass
        # Contact pages are more useful than several legal pages when the
        # public crawl budget is limited. Keep the four-page cap.
        urls.sort(key=lambda value: 0 if re.search(r'contat|contact|atendimento', value, re.I) else 1 if re.search(r'sobre|about|quem-somos', value, re.I) else 2)
        for kind, value in found:
            if valid_contact(kind, value) and (kind, value.lower()) not in seen_contacts:
                seen_contacts.add((kind, value.lower()))
                data["contacts"].append({"type": kind, "value": value, "source": final_url, "via": "Site público"})
        data["contacts"] = business.contacts_unique(data['contacts'])
        if url == base + '/' and data.get('search_topic') and (not data.get('shopify_verified') or not data.get('store_active') or data.get('country') not in ('', None, 'BR')):
            # Do not spend three more requests on a non-Shopify site, locked
            # catalog or explicitly foreign organization. Keep the evidence
            # so omitted records still explain what was found.
            break
    data['cnpjs'] = cnpjs[:10]
    # Multiple identifiers may belong to a payment processor or other company.
    # Leave the choice to review instead of assigning the first one to the store.
    if not data.get('cnpj_manual') and cnpjs:
        data['cnpj'] = cnpjs[0]['value'] if len(cnpjs) == 1 else ''
        data['cnpj_source'] = cnpjs[0]['source'] if len(cnpjs) == 1 else ''
    return data
