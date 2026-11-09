"""Request guard: Host check, cross-site write protection, optional LAN access token.

Applies to every HTTP request and WebSocket handshake, before the routes:

- Host: the Host header must name this machine (localhost, 127.0.0.1, ::1) or
  a host given explicitly for LAN access. Blocks DNS rebinding, where a web page
  re-points its own domain at 127.0.0.1 to read or drive the API.
- Writes (any method but GET / HEAD / OPTIONS) must carry X-Devialet-Request: 1.
  A form or a cross-site fetch cannot add that header without a CORS preflight,
  which this server never answers: plain cross-site POSTs are refused even from
  localhost. A present Origin must be one of the allowed hosts, and a present
  Sec-Fetch-Site must be same-origin or none.
- WebSockets: a present Origin must be an allowed host (the agent and command
  line tools send none; the agent route checks loopback itself).
- LAN mode (access_token set): every request needs the access cookie, obtained
  once by opening /?access=<token>. The agent route is exempt (loopback only).
- Responses forbid framing (clickjacking) and MIME sniffing.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.websockets import WebSocketClose

CSRF_HEADER = "x-devialet-request"
ACCESS_COOKIE = "devialet_access"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
AGENT_PATH = "/api/spectrum/agent"
MIN_TOKEN_LENGTH = 32
SECURITY_HEADERS = [
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


@dataclass(frozen=True)
class SecurityPolicy:
    allowed_hosts: frozenset[str] = LOCAL_HOSTS
    access_token: str | None = None          # None: local use, no token

    @classmethod
    def lan(cls, extra_hosts, access_token: str) -> "SecurityPolicy":
        if len(access_token or "") < MIN_TOKEN_LENGTH:
            raise ValueError(f"jeton d'accès trop court (au moins {MIN_TOKEN_LENGTH} caractères)")
        hosts = {h for h in (host_name(x) for x in extra_hosts) if h}
        if not hosts:
            raise ValueError("indiquez au moins un --allowed-host (nom ou IP utilisé par les autres appareils)")
        return cls(LOCAL_HOSTS | hosts, access_token)


def host_name(value: str | None) -> str | None:
    """'localhost:8000' -> 'localhost', '[::1]:8000' -> '::1'; lower case."""
    if not value:
        return None
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else None
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value or None


def origin_host(origin: str) -> str | None:
    try:
        parts = urlsplit(origin)
    except ValueError:
        return None
    return parts.hostname if parts.scheme in ("http", "https") else None


@dataclass
class _Request:
    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    query: str = ""


class RequestGuard:
    """Pure ASGI middleware, so that WebSocket handshakes are covered too."""

    def __init__(self, app, policy: SecurityPolicy | None = None):
        self.app = app
        self.policy = policy or SecurityPolicy()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        req = _Request(scope.get("method", "GET"), scope.get("path", ""), headers,
                       scope.get("query_string", b"").decode("latin-1"))
        websocket = scope["type"] == "websocket"
        refusal = self.check(req, websocket)
        if refusal == "login":
            return await self._login(req, scope, receive, send)
        if refusal:
            if websocket:
                return await WebSocketClose(code=1008)(scope, receive, send)
            status = 401 if refusal.startswith("Accès") else 400 if refusal.startswith("Hôte") else 403
            return await self._secured(PlainTextResponse(refusal, status_code=status), scope, receive, send)
        if websocket:
            return await self.app(scope, receive, send)
        return await self._secured(self.app, scope, receive, send)

    def check(self, req: _Request, websocket: bool) -> str | None:
        """None if allowed, else the refusal message ("login" for a valid /?access=)."""
        p = self.policy
        if host_name(req.headers.get("host")) not in p.allowed_hosts:
            return "Hôte non autorisé"
        origin = req.headers.get("origin")
        if origin is not None and origin_host(origin) not in p.allowed_hosts:
            return "Origine non autorisée"
        if p.access_token and not (websocket and req.path == AGENT_PATH):
            if not self._has_access(req):
                if req.method == "GET" and req.path == "/" and self._query_token_ok(req):
                    return "login"
                return "Accès réservé : ouvrez l'adresse donnée au démarrage du serveur (/?access=…)"
        if websocket or req.method in SAFE_METHODS:
            return None
        if origin is not None and urlsplit(origin).netloc.lower() != req.headers.get("host", "").lower():
            return "Origine non autorisée"           # same host, other port: another local site
        if req.headers.get(CSRF_HEADER) != "1":
            return "Requête refusée : en-tête X-Devialet-Request manquant"
        if req.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return "Requête intersite refusée"
        return None

    def _has_access(self, req: _Request) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(req.headers.get("cookie", ""))
        except Exception:  # noqa: BLE001 - malformed cookie header: no access
            return False
        value = cookie[ACCESS_COOKIE].value if ACCESS_COOKIE in cookie else ""
        return hmac.compare_digest(value.encode(), self.policy.access_token.encode())

    def _query_token_ok(self, req: _Request) -> bool:
        given = (parse_qs(req.query).get("access") or [""])[0]
        return hmac.compare_digest(given.encode(), self.policy.access_token.encode())

    async def _login(self, req, scope, receive, send):
        # the token leaves the address bar: it is kept in an HttpOnly, SameSite=Strict cookie
        res = RedirectResponse("/", status_code=303)
        res.set_cookie(ACCESS_COOKIE, self.policy.access_token, max_age=30 * 24 * 3600,
                       httponly=True, samesite="strict", path="/")
        await self._secured(res, scope, receive, send)

    @staticmethod
    async def _secured(app, scope, receive, send):
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + \
                    [(k, v) for k, v in SECURITY_HEADERS if k not in names]
            await send(message)
        await app(scope, receive, send_with_headers)
