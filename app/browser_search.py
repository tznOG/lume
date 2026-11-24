"""Read Google results with a local browser and a separate Lume profile.

Verification challenges are never solved by the app. The user can complete one
in the visible search window; collection then resumes without copying results.
"""
import os
from pathlib import Path
import threading
import time
import unicodedata
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from . import database as store
from .discovery import DiscoveryError, build_query, google_url, normalize_results


class BrowserSearchError(DiscoveryError):
    pass


def installed_browser():
    roots = [Path(os.getenv('ProgramFiles(x86)', 'C:/Program Files (x86)')),
             Path(os.getenv('ProgramFiles', 'C:/Program Files'))]
    if os.getenv('LOCALAPPDATA'):
        roots.append(Path(os.environ['LOCALAPPDATA']))
    for product in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'):
        for root in roots:
            executable = root / product
            if executable.is_file():
                return executable
    return None


def ensure_browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserSearchError('Execute “Instalar Lume.bat” para preparar a busca automática pelo navegador. Docker não é necessário.') from exc
    executable = installed_browser()
    if executable:
        return {'executable_path': str(executable)}
    with sync_playwright() as playwright:
        if Path(playwright.chromium.executable_path).is_file():
            return {'channel': 'chromium'}
    raise BrowserSearchError('Não encontrei Edge, Chrome ou o navegador do Lume. Execute “Instalar Lume.bat” para preparar a busca automática.')


def result_url(href):
    url = urljoin('https://www.google.com', href)
    parsed = urlsplit(url)
    if parsed.hostname in {'www.google.com', 'google.com', 'www.google.com.br', 'google.com.br'}:
        if parsed.path != '/url':
            return ''
        params = parse_qs(parsed.query)
        url = (params.get('q') or params.get('url') or [''])[0]
    return url if urlsplit(url).scheme in ('http', 'https') else ''


def parse_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ', strip=True)
    folded = ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))
    if '/sorry/' in url or soup.select_one('#captcha-form, .g-recaptcha, iframe[src*="recaptcha"]') or any(
        phrase in folded for phrase in ('unusual traffic', 'trafego incomum', 'verify you are human', 'confirme que voce e humano')
    ):
        return 'verification', [], False
    if urlsplit(url).hostname == 'consent.google.com':
        return 'consent', [], False
    scope = soup.select_one('#search, #rso')
    items = []
    if scope:
        for heading in scope.select('h3'):
            anchor = heading.find_parent('a', href=True)
            if not anchor:
                continue
            try:
                target = result_url(anchor['href'])
            except ValueError:
                continue
            if target:
                items.append({'url': target, 'title': heading.get_text(' ', strip=True), 'content': ''})
    if items:
        return 'results', items, bool(soup.select_one('#pnnext, a[aria-label="Next page"], a[aria-label="Próxima página"]'))
    if any(phrase in folded for phrase in ('nao encontrou nenhum documento', 'nenhum resultado encontrado', 'nao ha resultados para', 'nao foram encontrados resultados', 'did not match any documents', 'no results found')):
        return 'empty', [], False
    return 'loading', [], False


class BrowserSearch:
    def __init__(self, progress=None, stop=None, wait_seconds=180):
        self.progress = progress or (lambda message, waiting=False: None)
        self.stop = stop or threading.Event()
        self.wait_seconds = wait_seconds
        self.playwright = self.context = self.page = None
        self.headless = os.getenv('LUME_BROWSER_HEADLESS', '0' if os.name == 'nt' else '1') == '1'

    def __enter__(self):
        options = ensure_browser()
        from playwright.sync_api import sync_playwright
        try:
            self.playwright = sync_playwright().start()
            self.context = self.playwright.chromium.launch_persistent_context(
                str(store.data_dir() / 'search-browser'), headless=self.headless,
                locale='pt-BR', viewport={'width': 1200, 'height': 850}, accept_downloads=False,
                **options,
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            return self
        except Exception as exc:
            self.__exit__(None, None, None)
            raise BrowserSearchError('Não consegui abrir o navegador de busca do Lume. Feche uma janela de busca antiga, confira se Edge/Chrome pode ser executado e tente novamente.') from exc

    def __exit__(self, *args):
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        finally:
            if self.playwright:
                self.playwright.stop()
                self.playwright = None

    def _wait_page(self):
        deadline = time.monotonic() + 15
        waiting = False
        while time.monotonic() < deadline:
            if self.stop.is_set():
                raise BrowserSearchError('Execução interrompida. A posição foi preservada.')
            if self.page.is_closed():
                raise BrowserSearchError('A janela de busca foi fechada antes de terminar. Clique em Buscar lojas para continuar. A posição foi preservada.')
            from playwright.sync_api import Error as PlaywrightError
            try:
                state, items, more = parse_page(self.page.content(), self.page.url)
            except PlaywrightError:
                # Completing a verification can navigate while content is read.
                if self.page.is_closed():
                    raise BrowserSearchError('A janela de busca foi fechada. A posição foi preservada.')
                self.page.wait_for_timeout(500)
                continue
            if state in ('verification', 'consent'):
                if self.headless:
                    raise BrowserSearchError('O Google pediu uma verificação. Use o Lume no Windows com a janela de busca visível para confirmá-la; a busca continua automaticamente depois. A posição foi preservada.')
                if not waiting:
                    deadline = time.monotonic() + self.wait_seconds
                    waiting = True
                    self.progress('O Google pediu uma confirmação. Conclua a verificação na janela de busca aberta; o Lume continuará automaticamente.', True)
                    self.page.bring_to_front()
                if state == 'consent':
                    for label in ('Rejeitar tudo', 'Reject all', 'Recusar tudo'):
                        button = self.page.get_by_role('button', name=label, exact=True)
                        if button.count():
                            button.first.click(timeout=2000)
                            break
            elif state in ('results', 'empty'):
                self.progress('Resultados recebidos. Separando as lojas…', False)
                return items, more
            elif waiting:
                waiting = False
                deadline = time.monotonic() + 15
                self.progress('Confirmação concluída. Aguardando os resultados…', False)
            self.page.wait_for_timeout(500)
        raise BrowserSearchError('A busca aguardou a resposta ou confirmação do Google e não pôde concluir. Clique em Buscar lojas para tentar novamente. A posição foi preservada.')

    def search(self, topic, page):
        query = build_query(topic)
        self.progress(f'Pesquisando no Google: {topic}, página {page}…', False)
        try:
            self.page.goto(google_url(topic, page), wait_until='domcontentloaded', timeout=30000)
            items, more = self._wait_page()
            found = normalize_results(items, topic, page)
            for data in found:
                data.update(search_engine='google-browser', search_query=query)
            return found, more, []
        except BrowserSearchError:
            raise
        except Exception as exc:
            raise BrowserSearchError('O navegador não conseguiu concluir a busca. Confira a conexão e tente novamente. A posição foi preservada.') from exc
