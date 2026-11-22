"""Double-submit CSRF protection for cookie-authenticated writes.

Bearer-authenticated requests are exempt (the token itself is the CSRF
defence), as are the token-issuing auth endpoints and safe HTTP methods.
"""

import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.services.auth import COOKIE_NAME, CSRF_COOKIE_NAME

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
EXEMPT_PATHS = frozenset({"/health", "/login", "/api/auth/login", "/api/auth/register", "/api/auth/logout", "/api/pay/sync"})


class CSRFMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path
        has_bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
        has_cookie = bool(request.cookies.get(COOKIE_NAME))

        if (
            request.method.upper() not in SAFE_METHODS
            and has_cookie
            and not has_bearer
            and path not in EXEMPT_PATHS
        ):
            submitted = request.headers.get("x-csrf-token", "")
            stored = request.cookies.get(CSRF_COOKIE_NAME, "")
            if not submitted or not stored or not secrets.compare_digest(submitted, stored):
                await JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing or invalid"},
                )(scope, receive, send)
                return

        await self.app(scope, receive, send)
