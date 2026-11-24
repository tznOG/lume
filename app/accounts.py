"""Account directory and content-free usage metrics, separate from every tenant DB."""
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import database as store
from .identity import USER_ID, tenant
from .security import password_hash, verify_password

PUBLIC_FIELDS = ('id', 'username', 'display_name', 'role', 'active', 'created_at', 'last_seen', 'must_change_password')
USAGE_ACTIONS = {'login', 'search', 'scheduled', 'review', 'approve', 'export', 'import', 'stop'}
DUMMY_HASH = password_hash('invalid-login-placeholder', '0'*32)


@contextmanager
def connect():
    store.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(store.DATA_DIR / 'accounts.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
            active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage (
            user_id INTEGER NOT NULL REFERENCES users(id), day TEXT NOT NULL,
            action TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id,day,action)
        );
        CREATE TABLE IF NOT EXISTS jobs (
            user_id INTEGER NOT NULL REFERENCES users(id), run_id INTEGER NOT NULL,
            started_at TEXT NOT NULL, status TEXT NOT NULL,
            checked INTEGER NOT NULL DEFAULT 0, discovered INTEGER NOT NULL DEFAULT 0,
            queries INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,run_id)
        );
        CREATE TABLE IF NOT EXISTS options (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        if 'last_seen' not in {r[1] for r in db.execute('PRAGMA table_info(sessions)')}:
            db.execute('ALTER TABLE sessions ADD COLUMN last_seen TEXT')
            db.execute('UPDATE sessions SET last_seen=?', (store.now(),))
    # Upgrade the old single-user installation once. Never copy its data to signups.
    if not has_users():
        legacy = store.DATA_DIR / 'lume.sqlite3'
        if legacy.exists():
            with sqlite3.connect(legacy) as db:
                row = db.execute("SELECT value FROM settings WHERE key='password_hash'").fetchone()
            if row:
                import json
                create_user(os.getenv('LUME_ADMIN_USERNAME', 'admin'), 'Administrador',
                            saved_hash=json.loads(row[0]), role='admin', migrate_legacy=True)


def has_users():
    with connect() as db:
        return bool(db.execute('SELECT 1 FROM users LIMIT 1').fetchone())


def normalize_username(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,39}', value):
        raise ValueError('Usuário: use de 3 a 40 letras sem acentos, números, ponto, hífen ou sublinhado.')
    return value


def public(user):
    return {k: user[k] for k in PUBLIC_FIELDS}


def get_user(user_id):
    with connect() as db:
        row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    return dict(row) if row else None


def create_user(username, display_name, password=None, role='user', saved_hash=None, migrate_legacy=False):
    username = normalize_username(username)
    if saved_hash is None:
        if not password or len(password) < 10:
            raise ValueError('Escolha uma senha com pelo menos 10 caracteres.')
        saved_hash = password_hash(password)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        count = db.execute('SELECT count(*) FROM users').fetchone()[0]
        if count >= int(os.getenv('LUME_MAX_USERS', '100')):
            raise ValueError('Limite de contas atingido. Fale com o administrador.')
        try:
            uid = db.execute('INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                             (username, display_name.strip()[:80] or username, saved_hash, role, store.now())).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError('Este nome de usuário não está disponível.') from exc
        with tenant(uid):
            directory = store.data_dir()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if migrate_legacy and (store.DATA_DIR / 'lume.sqlite3').exists():
                with sqlite3.connect(store.DATA_DIR / 'lume.sqlite3') as source, sqlite3.connect(directory / 'lume.sqlite3') as destination:
                    source.backup(destination)
                if (store.DATA_DIR / 'encryption.key').exists():
                    shutil.copy2(store.DATA_DIR / 'encryption.key', directory / 'encryption.key')
            store.initialize()
            with store.connect() as private:
                private.execute("DELETE FROM settings WHERE key='password_hash'")
    return get_user(uid)


def check_password(user, password):
    saved = user['password_hash'] if user else DUMMY_HASH
    valid = verify_password(password, saved)
    return bool(user and user['active'] and valid)


def authenticate(username, password):
    with connect() as db:
        row = db.execute('SELECT * FROM users WHERE username=?', (username.strip().lower(),)).fetchone()
    user = dict(row) if row else None
    if not check_password(user, password):
        return None
    if not user['password_hash'].startswith('scrypt$'):
        upgraded = password_hash(password)
        with connect() as db:
            changed = db.execute('UPDATE users SET password_hash=? WHERE id=? AND password_hash=? AND version=?',
                                 (upgraded, user['id'], user['password_hash'], user['version'])).rowcount
        if not changed:
            return None
        user['password_hash'] = upgraded
    return user


def new_session(user):
    sid = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE expires_at<?', (store.now(),))
        inserted = db.execute('''INSERT INTO sessions(id,user_id,expires_at,last_seen)
            SELECT ?,id,?,? FROM users WHERE id=? AND version=? AND active=1''',
            (sid, expires, store.now(), user['id'], user['version'])).rowcount
        if not inserted:
            raise ValueError('Entre novamente com sua senha atual.')
    return {'uid': user['id'], 'version': user['version'], 'sid': sid}


def resolve_session(payload):
    if not isinstance(payload, dict) or type(payload.get('uid')) is not int:
        return None
    with connect() as db:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=1)).isoformat()
        row = db.execute('''SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
            WHERE u.id=? AND s.id=? AND u.version=? AND u.active=1 AND s.expires_at>? AND s.last_seen>?''',
            (payload['uid'], payload.get('sid'), payload.get('version'), now.isoformat(), cutoff)).fetchone()
        if row:
            db.execute('UPDATE sessions SET last_seen=? WHERE id=? AND last_seen<?',
                       (now.isoformat(), payload.get('sid'), (now - timedelta(seconds=60)).isoformat()))
    return dict(row) if row else None


def revoke_session(sid):
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE id=?', (sid,))


def touch(user_id):
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    with connect() as db:
        db.execute('UPDATE users SET last_seen=? WHERE id=? AND (last_seen IS NULL OR last_seen<?)', (store.now(), user_id, cutoff))


def usage(action, user_id=None):
    user_id = user_id or USER_ID.get()
    if user_id is None or action not in USAGE_ACTIONS:
        return
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo('America/Sao_Paulo')).date().isoformat()
    with connect() as db:
        db.execute('INSERT INTO usage VALUES(?,?,?,1) ON CONFLICT(user_id,day,action) DO UPDATE SET count=count+1', (user_id, day, action))


def job(run_id, status, stats):
    uid = USER_ID.get()
    if uid is None:
        return
    with connect() as db:
        db.execute('''INSERT INTO jobs VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,run_id) DO UPDATE SET
            status=excluded.status, checked=excluded.checked, discovered=excluded.discovered, queries=excluded.queries''',
            (uid, run_id, store.now(), status, int(stats.get('checked', 0)), int(stats.get('discovered', 0)), int(stats.get('queries', 0))))


def registration_open():
    with connect() as db:
        row = db.execute("SELECT value FROM options WHERE key='registration_open'").fetchone()
    return (row[0] != 'false') if row else os.getenv('LUME_REGISTRATION_OPEN', 'true').lower() == 'true'


def check_search_limit():
    uid = USER_ID.get()
    if uid is None:
        return
    user = get_user(uid)
    if not user or not user['active']:
        raise ValueError('Conta inativa.')
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo('America/Sao_Paulo')).date().isoformat()
    with connect() as db:
        count = db.execute('SELECT count(*) FROM jobs WHERE user_id=? AND started_at>=?', (uid, day)).fetchone()[0]
    if count >= int(os.getenv('LUME_MAX_DAILY_SEARCHES', '50')):
        raise ValueError('Limite diário de buscas atingido. Tente novamente amanhã.')


def update_user(uid, actor_id, changes):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not row:
            raise ValueError('Conta não encontrada.')
        if uid == actor_id and (changes.get('active') is False or changes.get('role') == 'user'):
            raise ValueError('Você não pode desativar ou remover sua própria administração.')
        active = int(changes.get('active', row['active']))
        role = changes.get('role', row['role'])
        if row['role'] == 'admin' and row['active'] and (not active or role != 'admin'):
            if db.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                raise ValueError('Mantenha pelo menos um administrador ativo.')
        db.execute('UPDATE users SET active=?,role=?,version=version+1 WHERE id=?', (active, role, uid))
        db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
    return public(get_user(uid))


def change_password(uid, new_password):
    if len(new_password) < 10:
        raise ValueError('Escolha uma senha com pelo menos 10 caracteres.')
    saved = password_hash(new_password)
    with connect() as db:
        db.execute('UPDATE users SET password_hash=?,version=version+1,must_change_password=0 WHERE id=?', (saved, uid))
        db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))


def statistics(days=30):
    from zoneinfo import ZoneInfo
    now = datetime.now(timezone.utc)
    since = (now.astimezone(ZoneInfo('America/Sao_Paulo')).date() - timedelta(days=days-1)).isoformat()
    cutoff = (now - timedelta(minutes=5)).isoformat()
    with connect() as db:
        users = [public(dict(row)) for row in db.execute('SELECT * FROM users ORDER BY id')]
        for user in users:
            user['online'] = bool(user['active'] and user['last_seen'] and user['last_seen'] >= cutoff)
            user['usage'] = {r['action']: r['total'] for r in db.execute('SELECT action,sum(count) total FROM usage WHERE user_id=? AND day>=? GROUP BY action', (user['id'], since))}
            totals = db.execute('SELECT count(*) runs,coalesce(sum(checked),0) checked,coalesce(sum(discovered),0) discovered FROM jobs WHERE user_id=? AND started_at>=?', (user['id'], since)).fetchone()
            user['jobs'] = dict(totals)
        daily = [dict(r) for r in db.execute('SELECT day,action,sum(count) count FROM usage WHERE day>=? GROUP BY day,action ORDER BY day', (since,))]
    return {'users': users, 'daily': daily, 'days': days, 'total_users': len(users), 'online_users': sum(u['online'] for u in users),
            'active_users': sum(bool(u['usage']) for u in users), 'searches': sum(u['usage'].get('search', 0) for u in users),
            'registration_open': registration_open()}
