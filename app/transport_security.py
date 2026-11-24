"""HTTPS policy. Proxy peers must be pinned after the deployment boundary is checked."""
import ipaddress
import os
from urllib.parse import urlsplit
from starlette.responses import JSONResponse, RedirectResponse


class TransportSecurity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or os.getenv('LUME_ENFORCE_HTTPS') != 'true':
            return await self.app(scope, receive, send)
        public = os.environ['LUME_PUBLIC_URL'].rstrip('/')
        canonical = urlsplit(public)
        if canonical.scheme != 'https' or canonical.path or canonical.username or canonical.query or canonical.fragment:
            raise RuntimeError('LUME_PUBLIC_URL must be an HTTPS origin')
        headers = dict(scope.get('headers', []))
        peer = (scope.get('client') or ('', 0))[0]
        trusted = False
        try:
            trusted = any(ipaddress.ip_address(peer) in ipaddress.ip_network(c.strip())
                          for c in os.getenv('LUME_TRUSTED_PROXY_IPS', '').split(',') if c.strip())
        except ValueError:
            pass
        scheme = scope.get('scheme')
        if trusted:
            scheme = headers.get(b'x-forwarded-proto', b'').decode('ascii', errors='ignore')
        host = headers.get(b'host', b'').decode('ascii', errors='ignore').lower()
        # Docker's local health check does not expose application content.
        if scope['path'] == '/api/health' and peer in ('127.0.0.1', '::1'):
            return await self.app(scope, receive, send)
        if scheme != 'https' or host != canonical.netloc.lower():
            if scope['method'] not in ('GET', 'HEAD'):
                return await JSONResponse({'detail': 'Use o endereço HTTPS do projeto.'}, 400)(scope, receive, send)
            path = scope.get('raw_path', scope['path'].encode()).decode('ascii')
            query = scope.get('query_string', b'').decode('ascii')
            return await RedirectResponse(public + path + ('?' + query if query else ''), 308)(scope, receive, send)
        return await self.app(scope, receive, send)
