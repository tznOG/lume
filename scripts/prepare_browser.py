"""Reuse installed Edge/Chrome; download Chromium only when neither is present."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.browser_search import ensure_browser, BrowserSearchError


def main():
    try:
        options = ensure_browser()
        print('Navegador pronto para a busca automática:', options.get('executable_path', 'Chromium do Lume'))
        return 0
    except BrowserSearchError:
        print('Preparando o navegador da busca automática…', flush=True)
        return subprocess.call([sys.executable, '-m', 'playwright', 'install', 'chromium', '--no-shell'])


if __name__ == '__main__':
    raise SystemExit(main())
