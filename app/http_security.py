"""Bound requests before parsers and attach protections to every HTTP response."""
import asyncio
import logging
from starlette.responses import JSONResponse


class SecurityEnvelope:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        started = False

        async def secured_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                headers = dict(message.get('headers', []))
                headers.update({
                    b'x-content-type-options': b'nosniff',
                    b'referrer-policy': b'no-referrer',
                    b'x-frame-options': b'DENY',
                    b'strict-transport-security': b'max-age=86400',
                    b'cache-control': b'no-store',
                    b'content-security-policy': b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
                })
                message['headers'] = list(headers.items())
            await send(message)

        if scope['method'] in ('POST', 'PUT', 'PATCH', 'DELETE'):
            limit = 21_000_000 if scope.get('path') in ('/api/crm', '/api/crm/preview') else 6_000_000
            body = bytearray()
            try:
                async with asyncio.timeout(15):
                    while True:
                        message = await receive()
                        if message['type'] == 'http.disconnect':
                            return
                        chunk = message.get('body', b'')
                        if len(body) + len(chunk) > limit:
                            return await JSONResponse({'detail': 'Arquivo muito grande. Limite: ' + ('20 MB.' if limit == 21_000_000 else '5 MB.')}, 413)(scope, receive, secured_send)
                        body.extend(chunk)
                        if not message.get('more_body', False):
                            break
            except TimeoutError:
                return await JSONResponse({'detail': 'Tempo de envio excedido.'}, 408)(scope, receive, secured_send)
            replayed = False

            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()
            upstream_receive = replay
        else:
            upstream_receive = receive
        try:
            await self.app(scope, upstream_receive, secured_send)
        except Exception as exc:
            # Do not log request bodies, URLs with tokens, or exception messages.
            logging.error('HTTP request failed: %s', type(exc).__name__)
            if started:
                raise
            await JSONResponse({'detail': 'Erro interno. Tente novamente.'}, 500)(scope, receive, secured_send)
