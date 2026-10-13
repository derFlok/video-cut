"""Session token, cookie exchange, Host and Origin checks (see the package docstring)."""

import secrets

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "videocut_session"
TOKEN_HEADER = "x-token"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Content-Security-Policy for every response: the UI is self-contained (no CDN, no inline
# script), media and stills come from this origin.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
FORBIDDEN_PAGE = (
    "Forbidden. Open the link printed in the terminal where Video Cut runs "
    "(Verboten. Öffne den Link aus dem Terminal, in dem Video Cut läuft)."
)


class Guard:
    """ASGI middleware: every HTTP request passes the Host check, the session check and,
    for writes, the Origin check; `/?t=<token>` exchanges the token for the cookie."""

    def __init__(self, app: ASGIApp, token: str, port: int, hosts: set[str] | None = None):
        self.app = app
        self.token = token
        self.session = secrets.token_urlsafe(32)  # the cookie value; never in a URL
        self.hosts = hosts or {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        req = Request(scope)
        response = self.check(req)
        if response is not None:
            await response(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                names = {k.lower() for k, _ in headers}
                extra = {
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"no-referrer",
                    b"x-frame-options": b"DENY",
                    b"content-security-policy": CSP.encode(),
                    b"cross-origin-resource-policy": b"same-origin",
                }
                if req.url.path.startswith("/api/"):
                    extra.setdefault(b"cache-control", b"no-store")
                headers += [(k, v) for k, v in extra.items() if k not in names]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def check(self, req: Request) -> Response | None:
        if req.headers.get("host") not in self.hosts:  # DNS-rebinding guard
            return PlainTextResponse("forbidden host", status_code=403)
        query_token = req.query_params.get("t")
        if query_token is not None and req.method == "GET":
            if not _same(query_token, self.token):
                return PlainTextResponse(FORBIDDEN_PAGE, status_code=403)
            # The link is opened from the terminal or by webbrowser.open (a navigation without
            # an initiating site, so the Strict cookie is sent on the redirect target).
            to = req.url.path if not req.url.path.startswith("/api/") else "/"
            page = RedirectResponse(to, status_code=303, headers={"Cache-Control": "no-store"})
            page.set_cookie(COOKIE, self.session, httponly=True, samesite="strict", path="/")
            return page
        by_header = _same(req.headers.get(TOKEN_HEADER, ""), self.token)
        by_cookie = _same(req.cookies.get(COOKIE, ""), self.session)
        if not (by_header or by_cookie):
            return PlainTextResponse(FORBIDDEN_PAGE, status_code=403)
        if req.method in WRITE_METHODS and not by_header:
            # A browser always sends Origin with a POST/PUT/DELETE fetch; a missing or
            # foreign one is a cross-site request riding on the cookie.
            if req.headers.get("origin") not in self.origins:
                return PlainTextResponse("cross-origin write refused", status_code=403)
        return None


def _same(given: str, secret: str) -> bool:
    """Constant-time comparison that also takes non-ASCII input (a mismatch, not a 500)."""
    return secrets.compare_digest(given.encode("utf-8", "replace"), secret.encode())
