# Configuração

O início documentado no README usa apenas o ambiente Python e mantém o servidor em loopback. Os scripts de Windows e preparação de buscadores foram preservados, mas podem instalar dependências ou iniciar serviços auxiliares; não são necessários para explorar a interface.

Exemplo no PowerShell, antes de executar Uvicorn:

```powershell
$env:SDR_DATA_DIR = 'data'
$env:LUME_REGISTRATION_OPEN = 'false'
```

Exemplo no Linux/macOS:

```bash
export SDR_DATA_DIR=data
export LUME_REGISTRATION_OPEN=false
```

Fechar novos cadastros não impede o primeiro acesso com o código de instalação. Não exponha a primeira instalação na rede antes de criar o administrador.

| Opção | Efeito |
| --- | --- |
| `SDR_DATA_DIR` | Raiz dos bancos e das chaves locais. |
| `LUME_REGISTRATION_OPEN` | Estado inicial da abertura de cadastros; o painel pode manter uma configuração persistida. |
| `LUME_MAX_USERS` | Limite de contas. |
| `LUME_MAX_DAILY_SEARCHES` | Limite diário de buscas por conta. |
| `LUME_MAX_CONCURRENT_JOBS` | Limite de trabalhos simultâneos. |
| `LUME_SEARCH_BACKEND` | `direct`, `browser` ou `searxng`; dependências e disponibilidade variam. |
| `STORELEADS_API_KEY` | Integração opcional; chaves também podem ser configuradas por conta na interface. |
| `COOKIE_SECURE` | Exige HTTPS para envio do cookie quando `true`. |
| `LUME_ENFORCE_HTTPS` | Ativa a política de transporte HTTPS. |
| `LUME_PUBLIC_URL` | Origem pública canônica da instalação. |
| `LUME_ALLOWED_HOSTS` | Hosts aceitos, separados por vírgula. |
| `LUME_TRUSTED_PROXY_IPS` | IPs/redes dos proxies autorizados a informar o protocolo original. |

Use as opções HTTPS em conjunto com um proxy configurado e restrição de acesso à origem. Não copie IPs de outra instalação nem confie indiscriminadamente em cabeçalhos enviados pelo cliente.

Faça backup consistente dos bancos e das chaves de `data/` em armazenamento privado. A chave de criptografia é necessária para recuperar integrações salvas. Não coloque backups no Git.
