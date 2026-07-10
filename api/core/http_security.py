from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject baseline security headers (CSP, anti clickjacking, referrer policy)."""

    def __init__(self, app, *, enforce_hsts: bool) -> None:
        super().__init__(app)
        self._enforce_hsts = enforce_hsts

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com https://cdn.jsdelivr.net; "
            "connect-src 'self' https://cloudflareinsights.com https://static.cloudflareinsights.com",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer-when-downgrade")
        if self._enforce_hsts:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
