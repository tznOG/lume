"""Prepare the Windows search dependency, then launch the local Python app."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from scripts.prepare_search import prepare
from app.discovery import backend

DOCKER_GUIDE = 'https://docs.docker.com/desktop/setup/install/windows-install/'


class StartupError(ValueError):
    pass


def search_url():
    value = os.getenv('SEARXNG_URL', 'http://127.0.0.1:8888').rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise StartupError('SEARXNG_URL inválido. Remova a configuração para usar o buscador local padrão.')
    return value


def search_ready(url):
    """Check service identity without querying Google or any other engine."""
    try:
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = client.get(url + '/config')
        if response.status_code != 200 or len(response.content) > 2_000_000:
            return False
        data = response.json()
        return isinstance(data, dict) and isinstance(data.get('engines'), list) and bool(data.get('version'))
    except (httpx.HTTPError, ValueError):
        return False


def docker_roots():
    roots = [Path(os.getenv('ProgramFiles', 'C:/Program Files')) / 'Docker' / 'Docker']
    if os.getenv('LOCALAPPDATA'):
        local = Path(os.environ['LOCALAPPDATA'])
        roots += [local / 'Programs' / 'DockerDesktop', local / 'Docker']
    return roots


def find_docker():
    installed = shutil.which('docker')
    if installed:
        return installed
    for root in docker_roots():
        candidate = root / 'resources' / 'bin' / 'docker.exe'
        if candidate.is_file():
            return str(candidate)
    return None


def command(args, capture=True, timeout=20):
    return subprocess.run(args, cwd=ROOT, capture_output=capture, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def docker_system(executable):
    try:
        result = command([executable, 'info', '--format', '{{.OSType}}'], timeout=8)
        return result.stdout.strip().lower() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def open_desktop():
    for root in docker_roots():
        desktop = root / 'Docker Desktop.exe'
        if desktop.is_file():
            startup = subprocess.STARTUPINFO() if os.name == 'nt' else None
            if startup:
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
            subprocess.Popen([str(desktop)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, startupinfo=startup,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            return True
    return False


def ensure_local_context(executable):
    # Do not accidentally create containers on an administrator's remote context.
    host = os.getenv('DOCKER_HOST', '')
    if not host:
        result = command([executable, 'context', 'inspect', '--format', '{{json .Endpoints.docker.Host}}'])
        if result.returncode != 0:
            raise StartupError('Não consegui verificar o contexto do Docker. Abra o Docker Desktop e selecione o mecanismo local.')
        try:
            host = json.loads(result.stdout.strip())
        except ValueError as exc:
            raise StartupError('O contexto do Docker retornou um endereço inválido.') from exc
    if not isinstance(host, str) or not host.startswith(('npipe://', 'unix://')):
        raise StartupError('Seu Docker está apontando para outra máquina. Selecione o contexto local do Docker Desktop antes de iniciar o Lume.')


def ensure_search():
    if backend() == 'browser':
        from app.browser_search import ensure_browser, BrowserSearchError
        try:
            ensure_browser()
        except BrowserSearchError as exc:
            raise StartupError(str(exc)) from exc
        print('Busca automática pelo navegador pronta. Docker não é necessário.', flush=True)
        return
    if backend() == 'direct':
        try:
            from ddgs import DDGS
            import primp
            import lxml.etree
        except ImportError as exc:
            raise StartupError('Falta atualizar as dependências. Execute "Instalar Lume.bat" e depois "Iniciar Lume.bat". Não é necessário instalar Docker.') from exc
        print('Busca direta em Python pronta. Docker não é necessário.', flush=True)
        return
    url = search_url()
    print('Verificando o buscador do Lume…', flush=True)
    if search_ready(url):
        print('Buscador disponível.', flush=True)
        return
    if url not in ('http://127.0.0.1:8888', 'http://localhost:8888'):
        raise StartupError('O buscador configurado em SEARXNG_URL não respondeu. Verifique essa instalação; não vou substituir sua configuração por outro serviço.')
    executable = find_docker()
    if not executable:
        raise StartupError('O Docker Desktop ainda não está instalado ou não foi encontrado.\n'
                           'Instale e abra o Docker Desktop uma vez, conclua a configuração inicial e use o mecanismo Linux/WSL 2.\n'
                           f'Instalação oficial: {DOCKER_GUIDE}\n'
                           'Depois, abra novamente "Iniciar Lume.bat". Não é necessário configurar SEARXNG_URL.')
    system = docker_system(executable)
    if not system:
        print('Iniciando o Docker Desktop. Aguarde a inicialização…', flush=True)
        open_desktop()
        for attempt in range(30):
            time.sleep(2)
            system = docker_system(executable)
            if system:
                break
            if attempt % 5 == 4:
                print('Ainda aguardando o Docker. Se houver uma tela de configuração, conclua-a no Docker Desktop.', flush=True)
    if system != 'linux':
        raise StartupError('O mecanismo Linux do Docker não ficou disponível. Abra o Docker Desktop, conclua a configuração do WSL 2 se for solicitada e tente iniciar o Lume novamente.')
    ensure_local_context(executable)
    result = command([executable, 'compose', 'version'])
    if result.returncode != 0:
        raise StartupError('O Docker Compose não está disponível. Atualize o Docker Desktop e tente novamente.')
    prepare(ROOT / 'data' / 'searxng')
    print('Preparando o buscador. A primeira execução baixa a imagem e pode levar alguns minutos…', flush=True)
    result = command([executable, 'compose', '-p', 'lume-windows', '-f', str(ROOT / 'compose.windows.yaml'),
                      'up', '-d', 'searxng'], capture=False, timeout=600)
    if result.returncode != 0:
        raise StartupError('Não consegui iniciar o buscador. Veja a mensagem do Docker acima: verifique conexão, espaço em disco e se a porta 8888 está livre.')
    print('Aguardando o buscador ficar pronto…', flush=True)
    for attempt in range(45):
        if search_ready(url):
            print('Busca pronta. Abrindo o painel do Lume…', flush=True)
            return
        if attempt % 10 == 9:
            print('O buscador ainda está iniciando…', flush=True)
        time.sleep(2)
    raise StartupError('O container iniciou, mas o buscador não respondeu a tempo. Aguarde um pouco e abra "Iniciar Lume.bat" novamente. A janela do Docker mostra os logs do serviço lume-windows/searxng.')


def main(argv=None):
    try:
        ensure_search()
        if '--prepare-only' in (argv if argv is not None else sys.argv[1:]):
            return 0
        return subprocess.call([sys.executable, str(ROOT / 'run.py')], cwd=ROOT)
    except (StartupError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'\nO Lume precisa de uma etapa antes de abrir:\n{exc}\n', flush=True)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
