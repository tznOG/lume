import hashlib
import hmac
import secrets
import threading

from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer

from . import database as store


def secret_file(name, factory):
    directory = store.DATA_DIR if name in ('session.key', 'setup.key') else store.data_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / name
    if not path.exists():
        try:
            with path.open("xb") as handle:
                handle.write(factory())
            path.chmod(0o600)
        except FileExistsError:
            pass
    return path.read_bytes()


def cipher():
    return Fernet(secret_file("encryption.key", Fernet.generate_key))


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    return cipher().decrypt(value.encode()).decode() if value else ""


_HASH_SLOTS = threading.BoundedSemaphore(2)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    with _HASH_SLOTS:
        result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=131072,
                                r=8, p=1, maxmem=192 * 1024 * 1024).hex()
    return f"scrypt$131072$8$1${salt}${result}"


def verify_password(password, saved):
    try:
        if saved.startswith('scrypt$'):
            algorithm, n, r, p, salt, expected = saved.split('$')
            if (n, r, p) != ('131072', '8', '1'):
                return False
            actual = password_hash(password, salt).split('$')[-1]
        else:
            salt, expected = saved.split(':')
            with _HASH_SLOTS:
                actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                        n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def password_ok(password):
    saved = store.get_setting("password_hash", "")
    return bool(saved) and verify_password(password, saved)


def sessions():
    return URLSafeTimedSerializer(secret_file("session.key", lambda: secrets.token_bytes(32)), salt="lume-login")


def bootstrap_token():
    return secret_file("setup.key", lambda: secrets.token_urlsafe(18).encode()).decode()
