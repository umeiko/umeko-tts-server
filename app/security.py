"""Admin sessions, CSRF protection, request limits and explicit external URL handling."""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote, urlsplit

from fastapi import Request
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Match

from .access import Principal, digest
from .errors import ApiError

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def cookie_name(settings):
    return "umeko_tts_" + digest(settings.base_path)[:8]


def csrf_token(settings, session):
    return hmac.new(
        settings.admin_token.encode(), session.encode(), hashlib.sha256
    ).hexdigest()


def check_origin(request: Request):
    origin = request.headers.get("origin")
    if not origin:
        return
    configured = urlsplit(request.app.state.settings.base_url)
    expected = (
        f"{configured.scheme}://{configured.netloc}"
        if configured.netloc
        else f"{request.url.scheme}://{request.url.netloc}"
    )
    if origin.rstrip("/") != expected:
        raise ApiError(403, "管理操作必须来自本站页面")


def verify_admin(request: Request):
    settings, store = request.app.state.settings, request.app.state.access
    if not settings.admin_token:
        raise ApiError(
            503, "管理员尚未配置，请设置至少 24 字符的 TTS_ADMIN_TOKEN 后重启"
        )
    header = request.headers.get("x-admin-token")
    if header is not None:
        if not hmac.compare_digest(header.encode(), settings.admin_token.encode()):
            raise ApiError(401, "管理员凭证无效")
        return
    session = request.cookies.get(cookie_name(settings), "")
    if not store.valid_session(session, digest(settings.admin_token)):
        raise ApiError(401, "管理员会话已失效，请重新登录")
    if request.method not in SAFE_METHODS:
        check_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(supplied, csrf_token(settings, session)):
            raise ApiError(403, "CSRF 校验失败，请刷新页面")


def verify_tts(request: Request) -> Principal:
    raw = request.headers.get("x-api-key")
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value:
            raise ApiError(401, "请使用 Authorization: Bearer <API_KEY>")
        if raw and raw != value:
            raise ApiError(401, "请只提供一个 API Key")
        raw = value
    if raw:
        return request.app.state.access.authenticate(raw)
    if request.headers.get("x-admin-token") or request.cookies.get(
        cookie_name(request.app.state.settings)
    ):
        verify_admin(request)
        return Principal.admin()
    raise ApiError(
        401,
        "请提供 Authorization: Bearer <API_KEY> 或 X-API-Key",
        {"WWW-Authenticate": "Bearer"},
    )


class PrefixMiddleware:
    """Normalize both preserved and stripped proxy paths to one ASGI root_path.

    Route matching disambiguates prefixes such as /api from internal /api/* routes.
    Header-supplied prefixes never influence routing or generated URLs.
    """

    def __init__(self, app, settings, routes):
        self.app, self.prefix, self.routes = app, settings.base_path, routes

    def known(self, scope, path):
        candidate = {**scope, "root_path": "", "path": path}
        return any(r.matches(candidate)[0] == Match.FULL for r in self.routes)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope = dict(scope)
        path = scope["path"]
        if self.prefix and (path == self.prefix or path.startswith(self.prefix + "/")):
            candidate = path[len(self.prefix) :] or "/"
            if self.known(scope, candidate) or not self.known(scope, path):
                path = candidate
        scope["umeko.path"] = path
        scope["root_path"] = self.prefix
        scope["path"] = self.prefix + path
        scope["raw_path"] = quote(scope["path"], safe="/").encode("ascii")
        await self.app(scope, receive, send)


class SecurityMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = scope.get("umeko.path", scope["path"])
        headers = {
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
            "x-frame-options": "DENY",
            "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
            "media-src 'self' blob:; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        }

        async def secure_send(message):
            if message["type"] == "http.response.start":
                values = dict(message.get("headers", []))
                values.update({k.encode(): v.encode() for k, v in headers.items()})
                message = {**message, "headers": list(values.items())}
            await send(message)

        try:
            # Authenticate before FastAPI reads/spools multipart or JSON bodies.
            if path.startswith("/api/") and path not in (
                "/api/auth/login",
                "/api/auth/session",
            ):
                verify_admin(request)
            elif path == "/tts" or path.startswith("/v1/"):
                request.state.principal = verify_tts(request)
            upload = path.startswith("/api/voices") and request.method in (
                "POST",
                "PUT",
            )
            limit = self.settings.max_upload_mb * 1024**2 if upload else 256 * 1024
            length = request.headers.get("content-length")
            if length is not None:
                try:
                    length = int(length)
                except ValueError:
                    raise ApiError(400, "Content-Length 无效") from None
                if length < 0:
                    raise ApiError(400, "Content-Length 无效")
                if length > limit:
                    raise ApiError(413, "请求体超过大小上限")
        except ApiError as exc:
            return await JSONResponse(
                {"message": exc.message}, exc.status_code, headers=exc.headers
            )(scope, receive, secure_send)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "请求体超过大小上限")
            return message

        await self.app(scope, limited_receive, secure_send)
