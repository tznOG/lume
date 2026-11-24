"""Read public Yahoo search results over HTTP, without starting a browser.

Only organic result cards are collected. Challenges, unknown markup and HTTP
errors are failures, never evidence that a topic has no more results.
"""
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
import time

from bs4 import BeautifulSoup
import httpx

from .discovery import DiscoveryError, SearchLimited

URL = 'https://search.yahoo.com/search'
PAGE_SIZE = 7
MAX_BYTES = 2_000_000


def result_url(value):
    parsed = urlsplit(value)
    if parsed.hostname == 'r.search.yahoo.com' and '/RU=' in parsed.path:
        value = unquote(parsed.path.split('/RU=', 1)[1].split('/RK=', 1)[0].split('/RS=', 1)[0])
        parsed = urlsplit(value)
    return value if parsed.scheme in ('http', 'https') and parsed.hostname else ''


def parse_results(content, page):
    soup = BeautifulSoup(content, 'html.parser')
    text = soup.get_text(' ', strip=True).lower()
    if soup.select_one('form[action*="captcha"], #captcha, .g-recaptcha') or any(
        phrase in text for phrase in ('unusual traffic', 'verify you are human', 'confirm you are not a robot', 'consent.yahoo.com')
    ):
        raise SearchLimited('O buscador solicitou uma verificação e não liberou os resultados.')
    scope = soup.select_one('#web')
    rows = []
    if scope:
        for card in scope.select('.algo'):
            heading = card.select_one('h3')
            if heading is None:
                continue
            anchor = heading.find_parent('a', href=True) or heading.select_one('a[href]')
            if anchor is None:
                continue
            try:
                url = result_url(anchor['href'])
            except ValueError:
                continue
            if url:
                snippet = card.select_one('.compText, .compAbstract')
                rows.append({'url': url, 'title': heading.get_text(' ', strip=True),
                             'content': snippet.get_text(' ', strip=True) if snippet else ''})
    # A pagination link must really move to the next offset on Yahoo. Do not
    # follow result-provided URLs or accept a repeated/changed paging protocol.
    next_link = soup.select_one('a.next[href]')
    if next_link:
        target = urlsplit(urljoin(URL, next_link['href']))
        offset = parse_qs(target.query).get('b', [''])[0]
        if target.hostname != 'search.yahoo.com' or offset != str(page * PAGE_SIZE + 1):
            raise DiscoveryError('A paginação do buscador mudou. A posição foi preservada.')
    if rows:
        return rows, bool(next_link)
    if not next_link and any(phrase in text for phrase in (
        'we did not find results for', 'no results found for', 'não encontramos resultados para',
        'não foram encontrados resultados para',
    )):
        return [], False
    raise DiscoveryError('O buscador respondeu em um formato que o Lume não conseguiu ler.')


def search(query, page):
    if not isinstance(page, int) or not 1 <= page <= 100:
        raise DiscoveryError('Página de pesquisa inválida.')
    url = str(httpx.URL(URL, params={'p': query, 'b': 1 + (page - 1) * PAGE_SIZE, 'pz': PAGE_SIZE, 'vl': 'lang_pt'}))
    try:
        with httpx.Client(timeout=20, trust_env=False, follow_redirects=False,
                          headers={'Accept': 'text/html,application/xhtml+xml', 'User-Agent': 'Lume-SDR/1.0'}) as client:
            retried = False
            for _ in range(5):
                with client.stream('GET', url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get('location', ''))
                        target = urlsplit(url)
                        if target.scheme != 'https' or target.hostname != 'search.yahoo.com' or target.port not in (None, 443) or target.username or target.password:
                            raise DiscoveryError('O buscador redirecionou para uma confirmação ou endereço inesperado.')
                        continue
                    # Retry one transient server failure. Access restrictions
                    # (202/403/429) are never retried or challenged by the app.
                    if response.status_code in (500, 502, 503, 504) and not retried:
                        retried = True
                        time.sleep(2)
                        continue
                    if response.status_code != 200:
                        error = SearchLimited if response.status_code in (202, 403, 429) else DiscoveryError
                        raise error(f'O buscador não liberou a pesquisa (HTTP {response.status_code}).')
                    if 'text/html' not in response.headers.get('content-type', '').lower():
                        raise DiscoveryError('O buscador não retornou uma página de resultados.')
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise DiscoveryError('Resposta do buscador excedeu o limite de leitura.')
                        chunks.append(chunk)
                    return parse_results(b''.join(chunks), page)
            raise DiscoveryError('O buscador não concluiu o redirecionamento da pesquisa.')
    except httpx.HTTPError as exc:
        raise DiscoveryError('Não foi possível conectar ao buscador nesta tentativa.') from exc
