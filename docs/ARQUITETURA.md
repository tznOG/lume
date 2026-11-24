# Arquitetura

A interface estática faz requisições à API FastAPI na mesma origem. A autenticação resolve uma sessão persistida e define o contexto da conta antes de acessar os dados do trabalho.

`accounts.sqlite3` concentra contas, sessões, opções e métricas de uso. Cada conta tem sua base SQLite dentro de `users/<id>/`. `identity.py` transporta esse contexto; `database.py` escolhe a base correspondente. Os arquivos reais não acompanham o código.

Uma busca passa pelo mecanismo de descoberta e pela execução coordenada em `service.py`. A leitura de páginas reúne sinais públicos. `qualification.py` produz motivos para revisão e bloqueios de aprovação. A interface permite corrigir e confirmar informações; a exportação usa os registros revisados. O cruzamento com a planilha CRM usa domínio/CNPJ e pode excluir empresas já presentes.

O agendador roda no processo web e verifica os trabalhos habilitados. Limites de busca e concorrência reduzem o uso por conta e por instância; não constituem uma fila distribuída.

`network.py` valida destinos públicos e redirecionamentos no fluxo de leitura que o utiliza. Isso não significa que todas as integrações opcionais compartilhem exatamente o mesmo transporte. `http_security.py` aplica cabeçalhos e limites de corpo; `transport_security.py` trata a origem HTTPS configurada.

A publicação inicial preserva os arquivos da versão em operação. As adições são documentação, exemplos de ambiente e testes locais adaptados da revisão. Nenhuma configuração ou base de produção é publicada.
