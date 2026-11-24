"""Request/job tenant context. IDs are issued by the account database, never by URLs."""
from contextvars import ContextVar
from contextlib import contextmanager

USER_ID = ContextVar('lume_user_id', default=None)


@contextmanager
def tenant(user_id):
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 1:
        raise ValueError('Invalid account id')
    token = USER_ID.set(user_id)
    try:
        yield
    finally:
        USER_ID.reset(token)
