"""Public business contacts and observed checkout links; no inferred gateway."""
import json
import re
from urllib.parse import unquote, urljoin, urlsplit

from .network import canonical, hostname
from .qualification import valid_contact


def organization_contacts(soup):
    found = []
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            types = node.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(isinstance(t, str) and (t in ('Organization', 'LocalBusiness', 'Corporation') or t.endswith('Store')) for t in types):
                contacts = node.get('contactPoint', [])
                contacts = [contacts] if isinstance(contacts, dict) else contacts
                for contact in [node] + (contacts if isinstance(contacts, list) else []):
                    if not isinstance(contact, dict):
                        continue
                    for field, kind in (('email', 'email'), ('telephone', 'phone')):
                        values = contact.get(field, [])
                        for value in values if isinstance(values, list) else [values]:
                            if isinstance(value, str):
                                found.append((kind, value.removeprefix('mailto:').removeprefix('tel:')))
            for field, child in node.items():
                if field not in ('author', 'review', 'manufacturer', 'brand', 'seller', 'contactPoint') and isinstance(child, (dict, list)):
                    visit(child)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            visit(json.loads(script.get_text()))
        except (ValueError, TypeError, RecursionError):
            pass
    return found


def normalize_contact(kind, value):
    value = unquote(str(value)).strip()
    if kind == 'email':
        value = value.lower()
        # Common template/processor contacts are not a store's sales contact.
        if value.split('@')[0] in ('example', 'exemplo', 'your-email', 'seuemail', 'noreply', 'no-reply') or value.endswith(('@example.com', '@shopify.com')):
            return ''
        return value if valid_contact(kind, value) else ''
    digits = re.sub(r'\D', '', value)
    if not valid_contact(kind, digits):
        return ''
    # Avoid treating an unlabelled 14-digit CNPJ as a telephone.
    if len(digits) == 14 or len(set(digits)) <= 2:
        return ''
    return '+' + digits if value.startswith('+') else digits


def contacts_unique(contacts):
    result, seen = [], set()
    # Retain a real WhatsApp link before a duplicate plain phone, without
    # inferring that every phone supports WhatsApp.
    for contact in sorted(contacts, key=lambda c: 0 if c.get('type') == 'whatsapp' else 1):
        kind = contact.get('type')
        value = normalize_contact(kind, contact.get('value', ''))
        if not value:
            continue
        identity = value.lower() if kind == 'email' else re.sub(r'\D', '', value)
        if kind != 'email' and len(identity) in (12, 13) and identity.startswith('55'):
            identity = identity[2:]
        key = ('email' if kind == 'email' else 'phone', identity)
        if key not in seen:
            seen.add(key)
            result.append({**contact, 'value': value})
    return result[:20]


def checkout_links(soup, page_url):
    result = []
    for node in soup.select('a[href], form[action]'):
        raw = str(node.get('href') or node.get('action') or '')
        if not re.search(r'checkout|finalizar.*compra', raw + ' ' + node.get_text(' ', strip=True), re.I):
            continue
        target = urljoin(page_url, raw)
        try:
            host = hostname(target)
            if canonical(host) == canonical(page_url):
                continue
        except ValueError:
            continue
        # Show only the host, never tokens/cart IDs in a link or query string.
        if host not in {r['host'] for r in result}:
            result.append({'host': host, 'source': page_url, 'kind': 'external_checkout_link'})
    return result[:5]
