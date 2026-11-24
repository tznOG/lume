<img src="app/static/mark.svg" width="56" alt="Marca do Lume">

# Lume

Painel de prospecção para encontrar, organizar e revisar lojas virtuais antes de entrar em contato. Combina descoberta de sites, análise de sinais públicos, conferência com uma base de CRM e uma fila de revisão manual.

[Abrir aplicação](https://lume.tznog.com) · [Arquitetura](docs/ARQUITETURA.md) · [Configuração](docs/CONFIGURACAO.md)

## O que dá para fazer

- Buscar empresas por tema, colar resultados de busca ou importar domínios e CSV.
- Reunir sinais sobre plataforma, país, contatos públicos, estoque e possíveis operações de dropshipping, com fontes para revisão.
- Filtrar oportunidades, registrar observações e mover empresas pelas etapas de acompanhamento.
- Importar uma planilha CSV/XLSX do CRM para identificar empresas já cadastradas por domínio e CNPJ.
- Revisar critérios como tráfego, contato e disponibilidade no CRM antes da aprovação. Um sinal automático não substitui a conferência da pessoa que usa o painel.
- Consultar indicadores, histórico de execuções e exportar resultados em CSV.
- Usar contas separadas, com dados de trabalho por usuário, e administrar acesso e limites.

O projeto nasceu de uma necessidade prática: reduzir o trabalho de organizar candidatos à prospecção sem perder as evidências que justificam cada decisão. Ele roda em um homelab; este repositório contém a aplicação, sem a base usada na instalação.

## Tecnologias

Python, FastAPI, SQLite, HTML, CSS e JavaScript. HTTPX e Beautiful Soup participam das integrações e leitura de páginas; OpenPyXL trata planilhas. Playwright atende ao modo opcional de busca com navegador. As versões diretas estão fixadas em `requirements.txt`.

## Executar localmente

Use Python 3.12. Na raiz do projeto:

```bash
git clone https://github.com/tznOG/lume.git
cd lume
python -m venv .venv
```

Ative com `source .venv/bin/activate` no Linux/macOS ou `.venv\Scripts\Activate.ps1` no PowerShell. Depois:

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abra `http://127.0.0.1:8000`. No primeiro início, o terminal mostra um código de instalação para criar o administrador. Esse código é local e não deve aparecer em capturas de tela. Os bancos e as chaves são gerados em `data/`, ignorada pelo Git.

O painel pode ser conhecido antes de executar uma busca. Iniciar coleta ou processamento pode consultar sites externos; deixe o agendamento desligado durante a demonstração. A integração Store Leads é opcional e depende de uma assinatura própria. Nenhuma credencial é fornecida neste repositório.

As variáveis aceitas estão em [.env.example](.env.example). O comando acima lê o ambiente do processo; **não carrega `.env` automaticamente**. Veja exemplos e opções em [Configuração](docs/CONFIGURACAO.md).

## Organização

```text
app/
  main.py, auth_routes.py     Rotas e ciclo de vida
  accounts.py, identity.py    Contas, sessões e contexto do usuário
  database.py                SQLite e armazenamento por usuário
  discovery.py, service.py    Descoberta e execução dos trabalhos
  network.py, crawler.py      Acesso a URLs e leitura de páginas
  qualification.py           Critérios de qualificação
  crm.py                     Importação e cruzamento com CRM
  static/, templates/        Interface web
scripts/                     Utilitários locais e manutenção
tests/                       Regressões com dados sintéticos
```

## Verificações locais

```bash
python tests/security_regression.py
python tests/access_regression.py
```

Esses testes cobrem migração de hashes, expiração de sessão, autorização entre contas, revogação após logout/troca de senha, validação de destinos de rede e limites de requisição. Usam dados temporários e simulações de rede; não iniciam buscas reais. Não representam uma auditoria completa.

## Limites e próximos passos

As regras de qualificação são heurísticas: plataforma, tráfego e estoque podem exigir revisão manual. A disponibilidade dos mecanismos de busca depende dos fornecedores. A aplicação usa SQLite e execução de tarefas dentro do processo; distribuição de trabalho entre várias instâncias não está implementada.

Para publicar uma instalação própria, configure HTTPS, origem, proxy confiável, permissões e backups. Essas definições dependem da infraestrutura e não são reproduzidas apenas por clonar o código. A configuração privada do homelab ficou fora do repositório.

Capturas do painel devem usar uma instalação de demonstração e dados fictícios. A apresentação visual será ampliada com essas imagens. A licença do projeto ainda não foi definida nesta revisão privada.
