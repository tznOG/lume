"""Generate private SearXNG configuration once, without a shipped secret."""
import os
from pathlib import Path
import secrets


def prepare(directory):
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'settings.yml'
    if target.exists():
        return target
    settings = f'''use_default_settings: true
general:
  instance_name: Lume Search
search:
  formats:
    - html
    - json
  default_lang: pt-BR
server:
  secret_key: "{secrets.token_hex(32)}"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
  image_proxy: false
engines:
  - name: google
    disabled: false
  - name: duckduckgo
    disabled: false
'''
    try:
        with target.open('x', encoding='utf-8') as handle:
            handle.write(settings)
        target.chmod(0o644)
    except FileExistsError:
        pass
    return target


if __name__ == '__main__':
    target = prepare(os.getenv('SEARXNG_CONFIG_DIR', '/search-config'))
    print(f'Configuração de busca pronta: {target}')
