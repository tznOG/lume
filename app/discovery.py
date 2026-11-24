"""Topic discovery with direct Python search or optional private SearXNG.

The instance address is an administrator-controlled environment variable, never
a URL supplied by search results or the browser. Public storefront fetching
retains separate DNS pinning and private-network restrictions.
"""
import os
import re
import hashlib
import unicodedata
from urllib.parse import unquote, urlencode, urlsplit

import httpx

from .network import hostname, canonical

EXCLUSIONS = '-inurl:products -inurl:collections -inurl:pages -inurl:blogs -inurl:policies -inurl:search -help -admin -accounts'
EXCLUDED_PATHS = {'products', 'collections', 'pages', 'blogs', 'policies', 'search', 'admin', 'accounts', 'account'}


class DiscoveryError(ValueError):
    pass


class SearchLimited(DiscoveryError):
    """The search source explicitly denied access or requested a challenge."""
    pass


def backend():
    selected = os.getenv('LUME_SEARCH_BACKEND', '').strip().lower()
    selected = selected or ('searxng' if os.getenv('SEARXNG_URL') else 'direct')
    if selected not in ('browser', 'direct', 'searxng'):
        raise DiscoveryError('Modo de busca inválido. Use browser, direct ou searxng em LUME_SEARCH_BACKEND.')
    return selected


def direct_engines():
    engines = os.getenv('DIRECT_SEARCH_ENGINES', 'yahoo').lower().replace(' ', '')
    if not engines or any(e not in {'yahoo', 'google', 'bing', 'duckduckgo', 'brave'} for e in engines.split(',')):
        raise DiscoveryError('Configure mecanismos válidos em DIRECT_SEARCH_ENGINES: yahoo, google, bing, duckduckgo ou brave.')
    return engines


def state_key(topic):
    engines = 'br-v4|' + direct_engines() if backend() == 'direct' else ('google-browser-v1' if backend() == 'browser' else os.getenv('SEARXNG_ENGINES', 'google,duckduckgo'))
    return 'search:' + hashlib.sha256((build_query(topic) + '|' + backend() + '|' + engines).encode()).hexdigest()[:20]


def normalize_topic(topic):
    topic = ' '.join(unicodedata.normalize('NFKC', topic).split())
    if not 2 <= len(topic) <= 80 or any(not (c.isalnum() or c in " -'&") for c in topic):
        raise ValueError('Informe um tema de 2 a 80 caracteres, como perfumes, roupas ou cosméticos.')
    return topic.casefold()


def build_query(topic):
    return f'site:myshopify.com "R$" {normalize_topic(topic)} {EXCLUSIONS}'


def brazil_queries(topic):
    topic = normalize_topic(topic)
    return [f'site:com.br {topic} "Shopify" -agência -template -tema -curso',
            f'site:com.br {topic} "Powered by Shopify"',
            f'site:myshopify.com {topic} "CNPJ" Brasil']


def google_url(topic, page=1):
    params = {'q': build_query(topic), 'hl': 'pt-BR'}
    if page > 1:
        params['start'] = (page - 1) * 10
    return 'https://www.google.com/search?' + urlencode(params)


def pasted_results(text, topic):
    """Extract storefront domains from pasted search text, without retaining it."""
    topic = normalize_topic(topic)
    domains = list(dict.fromkeys(re.findall(r'(?<![a-z0-9.\-])([a-z0-9][a-z0-9\-]{0,62}\.myshopify\.com)(?![a-z0-9.\-])', text, re.I)))
    if len(domains) > 500:
        raise ValueError('Cole até 500 lojas por vez.')
    found = normalize_results([{'url': 'https://' + domain.lower()} for domain in domains], topic, 1)
    if not found:
        raise ValueError('Não encontrei endereços de lojas myshopify.com no texto. No Google, selecione os resultados da pesquisa, copie e cole aqui, ou cole os links das lojas.')
    for data in found:
        data.update(source='Resultados colados pela operadora', search_page=None)
    return found


def endpoint():
    base = os.getenv('SEARXNG_URL', 'http://127.0.0.1:8888').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DiscoveryError('O endereço SEARXNG_URL da instalação está inválido.')
    return base + '/search'


def candidate(item, topic, page, brazil=False, custom_domains=False):
    url = item.get('url', '')
    host = hostname(url)
    labels = host.split('.')
    shopify_host = len(labels) == 3 and labels[-2:] == ['myshopify', 'com'] and labels[0] not in {'help', 'admin', 'accounts', 'account', 'www'}
    custom_host = brazil and (host.endswith('.com.br') or custom_domains) and canonical(host) not in {'shopify.com', 'myshopify.com', 'shopify.com.br', 'informecadastral.com.br', 'econodata.com.br', 'casadosdados.com.br', 'cadastroempresa.com.br', 'monitorcnpj.com.br', 'shopee.com.br', 'mercadolivre.com.br', 'amazon.com.br', 'lojadetemas.com.br', 'brasiltemas.com.br', 'reidostemplates.com.br', 'facebook.com', 'instagram.com', 'youtube.com', 'tiktok.com', 'pinterest.com', 'linkedin.com', 'reddit.com', 'reclameaqui.com.br', 'google.com', 'yahoo.com', 'wikipedia.org', 'aliexpress.com', 'amazon.com', 'builtwith.com', 'storeleads.app', 'myip.ms'}
    custom_host = custom_host and not host.endswith('.myshopify.com') and canonical(host) not in {'eachspy.com', 'cnpj.biz', 'cnpja.com', 'fontedata.com', 'meuecommerce.com.br', 'ecomstart.io', 'shopplaza.io', 'omnisend.com', 'printify.com', 'templatemonster.com', 'appsecommerce.com.br'}
    if not shopify_host and not custom_host:
        return None
    path = unquote(urlsplit(url).path).lower()
    # Product hits can identify a custom-domain shop; inspect its homepage,
    # never treat one product or article as a separate store.
    if any(segment in (EXCLUDED_PATHS - {'products', 'collections', 'pages'} if brazil else EXCLUDED_PATHS) for segment in path.split('/')):
        return None
    return {
        'domain': host, 'name': (item.get('title') or host)[:250], 'country': '',
        'country_hint': 'Candidata de busca; origem brasileira depende das evidências no site',
        'shopify_verified': False, 'platform_source': '', 'shop_id': host if shopify_host else '',
        'visits': None, 'traffic_source': '', 'traffic_metric': '', 'traffic_checked_at': '',
        'traffic_period': '', 'contacts': [], 'apps': [], 'stock_signals': [], 'drop_signals': [],
        'notes': '', 'source': 'Busca por tema', 'aliases': [], 'category': topic,
        'search_topic': topic, 'search_query': build_query(topic), 'search_page': page,
        'discovery_url': url, 'search_snippet': (item.get('content') or '')[:1000],
        'crm_status': 'unknown',
    }


def search_page(topic, page):
    if backend() == 'browser':
        from .browser_search import BrowserSearch
        with BrowserSearch() as browser:
            return browser.search(topic, page)
    return search_direct(topic, page) if backend() == 'direct' else search_searxng(topic, page)


def search_direct(topic, page):
    try:
        from ddgs import DDGS
        from ddgs.engines import ENGINES
        import certifi
    except ImportError as exc:
        raise DiscoveryError('Falta atualizar as dependências da busca direta. Feche o Lume, execute "Instalar Lume.bat" e depois "Iniciar Lume.bat". Docker não é necessário nesse modo.') from exc
    requested = list(dict.fromkeys(direct_engines().split(',')))
    # DDGS silently switches to 'auto' when a requested engine is disabled.
    # Check its actual registry before each explicitly selected backend call.
    engines = [name for name in requested if name == 'yahoo' or name in ENGINES.get('text', {})]
    if not engines:
        raise DiscoveryError('Os mecanismos configurados não estão disponíveis nesta versão da busca. Configure DIRECT_SEARCH_ENGINES=yahoo,duckduckgo,brave. A posição foi preservada.')
    warnings = ['Mecanismos desativados na biblioteca foram ignorados: ' + ', '.join(name for name in requested if name not in engines)] if engines != requested else []
    queries = [build_query(topic), f'site:myshopify.com {normalize_topic(topic)} "R$"']
    had_rows = False
    failures = []
    for query in queries:
        for engine in engines:
            if engine == 'yahoo':
                # Read current organic result cards over HTTP. No browser.
                if query != queries[0]:
                    continue
                from . import yahoo_search
                try:
                    items, more = [], False
                    for brazil_query in brazil_queries(topic):
                        batch, batch_more = yahoo_search.search(brazil_query, page)
                        items.extend({**item, '_query': brazil_query} for item in batch)
                        more = more or batch_more
                except DiscoveryError as exc:
                    failures.append('Yahoo: ' + str(exc))
                    continue
                found = normalize_results(items, topic, page, brazil=True)
                for data in found:
                    data.update(search_engine='yahoo-http')
                if items and not found:
                    warnings.append('Esta página respondeu, mas não trouxe endereços de lojas aceitos pelos filtros.')
                return found, more, warnings
            try:
                with DDGS(timeout=12, verify=certifi.where()) as client:
                    rows = client.text(query, region='br-pt', safesearch='moderate', max_results=10, page=page, backend=engine)
            except Exception:
                continue
            if not isinstance(rows, list) or not rows:
                continue
            had_rows = True
            items = [{'url': row.get('href') or row.get('url') or '', 'title': row.get('title', ''), 'content': row.get('body', '')}
                     for row in rows[:30] if isinstance(row, dict)]
            found = normalize_results(items, topic, page)
            if not found:
                continue
            for data in found:
                data.update(search_query=query, search_engine=engine)
            if engine != engines[0] or query != queries[0]:
                warnings.append('Foi usada uma alternativa de busca (' + engine + '). Os resultados podem diferir do Google; as exclusões de páginas continuam aplicadas pelo Lume.')
            return found, True, warnings
    if had_rows:
        return [], True, warnings + ['O mecanismo respondeu, mas não trouxe lojas válidas nesta página.']
    # No results and access restrictions are indistinguishable in DDGS. Even an
    # empty list must not mark the query as exhausted or advance its cursor.
    detail = failures[0] if failures else ''
    raise DiscoveryError('A pesquisa automática está indisponível nesta tentativa. ' + detail + ' Tente novamente mais tarde. Nenhuma janela foi aberta e a posição foi preservada.')


def normalize_results(items, topic, page, brazil=False, custom_domains=False):
    found, seen = [], set()
    for item in items[:100]:
        try:
            data = candidate(item, topic, page, brazil=brazil, custom_domains=custom_domains)
        except (ValueError, TypeError, AttributeError):
            continue
        identity = canonical(data['domain']) if data else ''
        if data and identity not in seen:
            data['search_query'] = item.get('_query') or data['search_query']
            seen.add(identity)
            found.append(data)
    return found


def search_searxng(topic, page):
    query = build_query(topic)
    params = {'q': query, 'format': 'json', 'pageno': page, 'language': 'pt-BR', 'categories': 'general',
              'engines': os.getenv('SEARXNG_ENGINES', 'google,duckduckgo'), 'safesearch': 1}
    try:
        with httpx.Client(timeout=50, trust_env=False, follow_redirects=False) as client:
            with client.stream('GET', endpoint(), params=params) as response:
                if response.status_code in (403, 429):
                    raise DiscoveryError('A busca foi bloqueada ou limitada. Aguarde e tente novamente; nenhum bloqueio será contornado.')
                if response.status_code != 200:
                    raise DiscoveryError(f'O buscador retornou HTTP {response.status_code}. Confira o serviço SearXNG da instalação.')
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise DiscoveryError('Resposta do buscador excedeu o limite de leitura.')
                    chunks.append(chunk)
                import json
                payload = json.loads(b''.join(chunks))
    except httpx.HTTPError as exc:
        message = ('O buscador não está disponível. No Windows, feche o Lume e abra "Iniciar Lume.bat" da versão atualizada: '
                   'ele verifica o Docker Desktop e inicia a busca automaticamente. Se o Docker ainda não estiver instalado, siga o guia do Windows no README. '
                   'No homelab, verifique o serviço SearXNG do Compose.')
        raise DiscoveryError(message) from exc
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, DiscoveryError):
            raise
        raise DiscoveryError('O buscador não retornou JSON válido. Habilite o formato json no SearXNG.') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
        raise DiscoveryError('Formato de resposta do buscador inesperado.')
    failures = payload.get('unresponsive_engines') or []
    if not payload['results'] and failures:
        raise DiscoveryError('Os mecanismos de busca estão indisponíveis ou bloquearam a consulta. Tente mais tarde. A posição da busca foi preservada.')
    found = normalize_results(payload['results'], topic, page)
    warnings = ['Parte dos mecanismos não respondeu; os resultados podem estar incompletos.'] if failures else []
    # An empty valid result page ends this query, not all stores on the web.
    return found, bool(payload['results']), warnings
