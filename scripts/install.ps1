$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
Write-Host 'Preparando seu Lume...' -ForegroundColor Green
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv .venv
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv .venv
} else {
    Write-Host 'Instale Python 3.12 ou superior: https://www.python.org/downloads/' -ForegroundColor Yellow
    exit 1
}
if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel preparar Python. Instale Python 3.12 ou superior.' }
& .\.venv\Scripts\python.exe -c "import sys; assert sys.version_info >= (3,12), 'Use Python 3.12 ou superior.'"
if ($LASTEXITCODE -ne 0) { throw 'Versao do Python incompativel.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'A instalacao nao terminou. Confira sua conexao e tente novamente.' }
Write-Host 'Tudo pronto! Abra "Iniciar Lume.bat".' -ForegroundColor Green
Write-Host 'A pesquisa roda em segundo plano pelo painel, sem abrir janelas de busca. Nao precisa instalar Docker.' -ForegroundColor Green
