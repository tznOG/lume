import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os
os.chdir(ROOT)
from app import database as store, accounts

store.initialize()
accounts.initialize()
username = input('Usuário do Lume: ').strip().lower()
with accounts.connect() as db:
    user = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
if not user:
    raise SystemExit('Conta não encontrada.')

password = getpass.getpass("Nova senha do Lume (mínimo 10 caracteres): ")
if len(password) < 10:
    raise SystemExit("A senha deve ter pelo menos 10 caracteres.")
if password != getpass.getpass("Repita a senha: "):
    raise SystemExit("As senhas não conferem.")
accounts.change_password(user['id'], password)
print("Senha atualizada. Os acessos anteriores foram encerrados.")
