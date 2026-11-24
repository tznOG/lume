"""Bounded public-web requests; pin validated DNS addresses to the connection.

No proxy inheritance, no private-network requests, TLS certificate validation
and SNI against the original hostname, and validation on every redirect.
"""
import http.client
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

import certifi
import tldextract

extract = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)
USER_AGENT = "LumeSDR/1.0 (+public business contact research)"
MAX_BYTES = 5_000_000


class FetchError(ValueError):
    pass


def hostname(value):
    parsed = urlsplit(value if "://" in value else "https://" + value.strip())
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise FetchError("Use um endereço público http ou https, sem credenciais.")
    try:
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode().lower()
        if parsed.port and parsed.port not in (80, 443):
            raise FetchError("Porta de destino não permitida.")
    except (UnicodeError, ValueError) as exc:
        raise FetchError("Endereço inválido.") from exc
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise FetchError("Domínio inválido.")
    parts = extract(host)
    if not parts.suffix or not parts.domain or host.endswith(".local"):
        raise FetchError("Use um domínio público válido.")
    return host


def canonical(value):
    host = hostname(value)
    return extract(host).top_domain_under_public_suffix


def public_address(host, port):
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not records or any(not ipaddress.ip_address(r[4][0]).is_global for r in records):
        raise FetchError("Destino de rede privado ou reservado bloqueado.")
    return records[0][4][0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, port):
        super().__init__(host, port, timeout=10, context=ssl.create_default_context(cafile=certifi.where()))
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def fetch(url, allowed_domain=None, method="GET"):
    if method not in ("GET", "HEAD"):
        raise FetchError("Método de leitura inválido.")
    for _ in range(5):
        host = hostname(url)
        if allowed_domain and canonical(host) != allowed_domain:
            raise FetchError("Redirecionamento para outra loja; requer revisão.")
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address = public_address(host, port)
        conn = PinnedHTTPS(host, address, port) if parsed.scheme == "https" else http.client.HTTPConnection(address, port, timeout=10)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            conn.request(method, path, headers={"Host": host, "User-Agent": USER_AGENT, "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise FetchError("Redirecionamento inválido.")
                url = urljoin(url, location)
                continue
            if response.status >= 400:
                raise FetchError(f"Site retornou HTTP {response.status}.")
            if method == "HEAD":
                return "", url
            content_type = response.getheader("Content-Type", "text/html")
            if not any(t in content_type for t in ("text/", "application/xhtml")):
                raise FetchError("Conteúdo não textual ignorado.")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise FetchError("Compressão inesperada; requer revisão.")
            deadline = time.monotonic() + 20
            chunks, length = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise FetchError("Tempo total de leitura excedido; requer revisão.")
                chunk = response.read1(min(65536, MAX_BYTES + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
                if length > MAX_BYTES:
                    raise FetchError("Página excede limite de leitura.")
            body = b"".join(chunks)
            charset = re.search(r"charset=([\w-]+)", content_type)
            encoding = charset[1] if charset else "utf-8"
            try:
                return body.decode(encoding, errors="replace"), url
            except LookupError:
                return body.decode("utf-8", errors="replace"), url
        finally:
            conn.close()
    raise FetchError("Redirecionamentos em excesso.")


def resolve_storefront(url):
    # Resolve public Shopify redirects before reading the target's robots.txt.
    _, target = fetch(url, method="HEAD")
    return target
