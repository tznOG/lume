"""Account and tenant SQLite snapshots plus keys. Keep this archive private."""
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = Path(os.getenv("SDR_DATA_DIR", str(ROOT / "data"))).resolve()
target = Path(os.getenv('LUME_BACKUP_DIR', str(ROOT / 'backups')))
target.mkdir(parents=True, exist_ok=True, mode=0o700)
databases = list(data.rglob('*.sqlite3'))
if not databases:
    raise SystemExit("Ainda não há um banco de dados para copiar.")
filename = target / ("lume-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip")
with tempfile.TemporaryDirectory() as temp:
    with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as archive:
        for database in databases:
            if not database.resolve().is_relative_to(data):
                raise SystemExit('Caminho fora do diretório de dados.')
            relative = database.relative_to(data)
            copy_path = Path(temp) / relative
            copy_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(database) as source, sqlite3.connect(copy_path) as copy:
                source.backup(copy)
            archive.write(copy_path, relative.as_posix())
        for key in data.rglob("*.key"):
            if key.resolve().is_relative_to(data):
                archive.write(key, key.relative_to(data).as_posix())
filename.chmod(0o600)
print(f"Backup criado: {filename}\nO arquivo inclui as chaves do painel. Guarde-o em local privado.")
