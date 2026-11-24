"""Evidence required before a discovered site is shown as a Brazilian store.

This is a conservative screening, not a fraud certificate or registry lookup.
Currency, language and a Brazilian domain alone never establish country.
"""
import json
import re
from urllib.parse import urlsplit

from .company import find_cnpjs
from .qualification import fold, valid_contact

VERSION = 2
GENERIC_NAMES = {'my store', 'your store', 'your store name', 'demo store', 'test store', 'loja teste', 'nome da sua loja'}
SYNONYMS = {
    'perfume': ('perfume', 'perfumaria', 'fragrancia', 'parfum'),
    'roupa': ('roupa', 'moda', 'vestido', 'camiseta', 'camisa', 'calca', 'blusa', 'legging', 'vestuario'),
    'cosmetico': ('cosmetico', 'skincare', 'maquiagem', 'beleza', 'dermocosmetico'),
    'calcado': ('calcado', 'sapato', 'tenis', 'sandalia', 'bota'),
}


def topic_matches(topic, text):
    words = [word for word in re.findall(r'[a-z0-9]+', fold(topic)) if word not in {'de', 'da', 'do', 'e', 'para', 'com'}]
    haystack = fold(text)
    for word in words:
        stem = word[:-1] if word.endswith('s') else word
        terms = SYNONYMS.get(stem, (stem,))
        if not any(re.search(r'\b' + re.escape(term), haystack) for term in terms):
            return False
    return bool(words)


def organization_countries(soup):
    found = set()
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            types = node.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(isinstance(t, str) and (t in ('Organization', 'LocalBusiness', 'Corporation') or t.endswith('Store')) for t in types):
                addresses = node.get('address', [])
                addresses = [addresses] if isinstance(addresses, dict) else addresses
                if isinstance(addresses, list):
                    for address in addresses:
                        country = address.get('addressCountry') if isinstance(address, dict) else None
                        if isinstance(country, dict):
                            country = country.get('name') or country.get('alternateName')
                        if isinstance(country, str):
                            value = country.upper().strip()
                            value = {'BRASIL': 'BR', 'BRAZIL': 'BR', 'UNITED STATES': 'US', 'USA': 'US', 'PORTUGAL': 'PT', 'PAKISTAN': 'PK'}.get(value, value)
                            if re.fullmatch('[A-Z]{2}', value):
                                found.add(value)
            for key, child in node.items():
                if key not in ('address', 'offers', 'shippingDetails', 'review', 'author') and isinstance(child, (dict, list)):
                    visit(child)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            visit(json.loads(script.get_text()))
        except (ValueError, TypeError, RecursionError):
            continue
    return found


def inspect_page(data, soup, visible, url, home=False):
    evidence = data.setdefault('country_evidence', [])
    countries = organization_countries(soup)
    for country in sorted(countries):
        item = {'country': country, 'kind': 'organization_address', 'url': url,
                'text': 'País no endereço estruturado da organização: ' + country}
        if item not in evidence:
            evidence.append(item)
    cnpjs = find_cnpjs(visible)
    local_phone = bool(re.search(r'\+55\s*\(?[1-9]\d', visible)) or any(
        re.search(r'(?:wa.me/|phone=)55[1-9]\d\d{8,9}(?:\D|$)', str(a.get('href', ''))) for a in soup.select('a[href]'))
    local_address = bool(re.search(r'\b\d{5}-\d{3}\b', visible)) and bool(re.search(r'\b(?:rua|avenida|av\.|rodovia|alameda|travessa)\b', fold(visible)))
    if len(cnpjs) == 1 and (local_address or local_phone or (urlsplit(url).hostname.endswith('.com.br') and 'R$' in visible)):
        item = {'country': 'BR', 'kind': 'cnpj_local', 'url': url,
                'text': 'CNPJ ' + cnpjs[0] + ' publicado com endereço/telefone brasileiro ou domínio brasileiro e preços em reais'}
        if item not in evidence:
            evidence.append(item)
    if home:
        meta = soup.select_one('meta[property="og:site_name"]')
        title = str(meta.get('content', '')) if meta else (soup.title.get_text(' ', strip=True) if soup.title else '')
        if title:
            data['name'] = title[:250]
        generic = fold(title).strip(' .!') in GENERIC_NAMES
        catalog = bool(soup.select('a[href*="/products/"], a[href*="/collections/"], form[action*="/cart/add"]'))
        locked = bool(soup.select('form[action*="/password"]')) or '/password' in urlsplit(url).path
        template = any(phrase in fold(visible) for phrase in ('example product title', 'nome do produto de exemplo', 'this store is currently unavailable'))
        data['store_active'] = catalog and not locked and not generic and not template and len(visible) > 100
        data['store_activity_source'] = url
        data['store_activity_note'] = 'Catálogo público identificado' if data['store_active'] else 'Sem catálogo ativo confirmado, página de teste ou loja protegida'
        # Match the topic on the actual storefront, never on the search snippet.
        data['topic_verified'] = topic_matches(data.get('search_topic', ''), visible)
        data['topic_source'] = url
    if not data.get('country_manual') and (data.get('search_topic') or evidence):
        values = {item['country'] for item in evidence}
        data['country'] = next(iter(values)) if len(values) == 1 else ''
        data['country_source'] = evidence[0]['url'] if len(values) == 1 else ''
        data['country_conflict'] = len(values) > 1


def reasons(data):
    problems = []
    if data.get('country') != 'BR' or not (data.get('country_manual') or data.get('country_evidence')) or data.get('country_conflict'):
        problems.append('Origem brasileira não confirmada' if data.get('country') in ('', None, 'BR') else 'Loja de outro país: ' + data['country'])
    if not data.get('shopify_verified'):
        problems.append('Shopify não confirmado')
    if not data.get('store_active'):
        problems.append(data.get('store_activity_note') or 'Loja ativa não confirmada')
    if not data.get('topic_verified'):
        problems.append('Tema não confirmado no site')
    if not any(valid_contact(c.get('type'), c.get('value', '')) and c.get('source') for c in data.get('contacts', [])):
        problems.append('Sem contato comercial público')
    if data.get('drop_signals') and not data.get('manual_stock'):
        problems.append('Indícios de dropshipping')
    if data.get('crawl_error') and not data.get('store_active'):
        problems.append('Leitura do site incompleta')
    return problems


def stamp(data):
    if data.get('search_topic'):
        data['screening_version'] = VERSION
        data['screening_reasons'] = reasons(data)
        data['screening_ok'] = not data['screening_reasons']
    return data


# Applied by every default list, including existing unverified search records.
# Explicit inspection of omitted records remains available without deleting data.
VISIBLE_SQL = "COALESCE(json_extract(data,'$.crm_excluded'),0)=0 AND (COALESCE(json_extract(data,'$.search_topic'),'')='' OR (json_extract(data,'$.screening_ok')=1 AND json_extract(data,'$.country')='BR'))"
