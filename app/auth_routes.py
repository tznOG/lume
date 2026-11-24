import hmac
import os
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired
from pydantic import BaseModel, Field

from . import accounts, service
from .security import sessions, bootstrap_token

router = APIRouter()
ATTEMPTS = {}


def current_user(request):
    try:
        return accounts.resolve_session(sessions().loads(request.cookies.get('lume_session', ''), max_age=43200))
    except (BadSignature, SignatureExpired):
        return None


def throttle(request, username='', registration=False):
    # The account limit remains effective behind a proxy without trusting client-supplied IP headers.
    address = request.client.host if request.client else 'local'
    keys = [(('ip', address), 100), (('account', username.strip().lower()), 8)]
    if registration:
        keys = [(('signup', address), 10)]
    current = time.monotonic()
    for key in list(ATTEMPTS):
        ATTEMPTS[key] = [t for t in ATTEMPTS[key] if current-t < 300]
        if not ATTEMPTS[key]:
            del ATTEMPTS[key]
    for key, limit in keys:
        if len(ATTEMPTS.get(key, [])) >= limit:
            raise HTTPException(429, 'Muitas tentativas. Aguarde 5 minutos.')
    for key, _ in keys:
        ATTEMPTS.setdefault(key, []).append(current)


def secure_cookie(request):
    public = urlsplit(os.getenv('LUME_PUBLIC_URL', '')).netloc.lower()
    return os.getenv('COOKIE_SECURE', 'false').lower() == 'true' or bool(public and request.headers.get('host', '').lower() == public)


def login_response(request, user):
    response = JSONResponse({'ok': True, 'user': accounts.public(user)})
    response.set_cookie('lume_session', sessions().dumps(accounts.new_session(user)), max_age=43200,
                        httponly=True, secure=secure_cookie(request), samesite='strict')
    accounts.touch(user['id'])
    return response


@router.get('/api/session')
def session(request: Request):
    user = current_user(request)
    return {'authenticated': bool(user), 'user': accounts.public(user) if user else None,
            'needs_setup': not accounts.has_users(), 'registration_open': accounts.has_users() and accounts.registration_open(),
            'default_admin_username': os.getenv('LUME_ADMIN_USERNAME', 'admin') if not accounts.has_users() else ''}


class Credentials(BaseModel):
    username: str = Field('admin', min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)
    setup_token: str = Field('', max_length=200)
    display_name: str = Field('', max_length=80)


@router.post('/api/setup')
def setup(body: Credentials, request: Request):
    throttle(request, body.username)
    with service.LOCK:
        if accounts.has_users():
            raise ValueError('O painel já está configurado.')
        if not hmac.compare_digest(body.setup_token, bootstrap_token()):
            raise HTTPException(403, 'Código de instalação incorreto. Veja o terminal onde iniciou o Lume.')
        user = accounts.create_user(body.username, body.display_name or 'Administrador', body.password, role='admin', migrate_legacy=True)
    accounts.usage('login', user['id'])
    return login_response(request, user)


@router.post('/api/register')
def register(body: Credentials, request: Request):
    throttle(request, registration=True)
    with service.LOCK:
        if not accounts.has_users() or not accounts.registration_open():
            raise HTTPException(403, 'Novos cadastros estão fechados.')
        user = accounts.create_user(body.username, body.display_name or body.username, body.password, role='user')
    accounts.usage('login', user['id'])
    return login_response(request, user)


@router.post('/api/login')
def login(body: Credentials, request: Request):
    throttle(request, body.username)
    user = accounts.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(401, 'Usuário ou senha incorretos.')
    accounts.usage('login', user['id'])
    return login_response(request, user)


@router.post('/api/logout')
def logout(request: Request):
    try:
        payload = sessions().loads(request.cookies.get('lume_session', ''), max_age=43200)
        accounts.revoke_session(payload.get('sid'))
    except (BadSignature, SignatureExpired):
        pass
    response = JSONResponse({'ok': True})
    response.delete_cookie('lume_session', secure=secure_cookie(request), httponly=True, samesite='strict')
    return response


class PasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


@router.put('/api/account/password')
def password_change(body: PasswordBody, request: Request):
    throttle(request, request.state.user['username'])
    if not accounts.check_password(request.state.user, body.current_password):
        raise HTTPException(400, 'Senha atual incorreta.')
    accounts.change_password(request.state.user['id'], body.new_password)
    return login_response(request, accounts.get_user(request.state.user['id']))


def require_admin(request):
    if request.state.user['role'] != 'admin':
        raise HTTPException(403, 'Acesso exclusivo do administrador.')


@router.get('/api/admin/stats')
def admin_stats(request: Request, days: int = 30):
    require_admin(request)
    return accounts.statistics(days if days in (7, 30, 90) else 30)


class UserUpdate(BaseModel):
    active: bool | None = None
    role: str | None = None


@router.patch('/api/admin/users/{uid}')
def admin_user(uid: int, body: UserUpdate, request: Request):
    require_admin(request)
    changes = body.model_dump(exclude_none=True)
    if changes.get('role', 'user') not in ('admin', 'user'):
        raise ValueError('Perfil inválido.')
    result = accounts.update_user(uid, request.state.user['id'], changes)
    if not result['active']:
        service.stop_user(uid)
    return result


class RegistrationBody(BaseModel):
    enabled: bool


@router.put('/api/admin/registration')
def registration_settings(body: RegistrationBody, request: Request):
    require_admin(request)
    with accounts.connect() as db:
        db.execute("INSERT INTO options VALUES('registration_open',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('true' if body.enabled else 'false',))
    return {'registration_open': body.enabled}
