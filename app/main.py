"""Application factory; model initialization and scheduler ownership follow lifespan."""

from __future__ import annotations

import asyncio
import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .access import AccessStore
from .backends import GSVBackend, MockBackend
from .config import Settings
from .engine import TTSEngine
from .errors import ApiError
from .process_lock import ProcessLock
from .registry import VoiceRegistry
from .routes_access import router as access_router
from .routes_admin import router as admin_router
from .routes_tts import router as tts_router
from .security import PrefixMiddleware, SecurityMiddleware, verify_admin

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("umeko-tts")


def create_app(settings: Settings | None = None, backend=None) -> FastAPI:
    settings = (settings or Settings.from_env()).validate()
    registry = VoiceRegistry(settings.data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lock = ProcessLock(settings.data_dir)
        lock.acquire()
        store, engine, selected = None, None, backend
        try:
            store = AccessStore(settings.data_dir)
            store.recover()
            app.state.access = store
            selected = selected or (
                MockBackend() if settings.backend == "mock" else GSVBackend(settings)
            )
            await run_in_threadpool(selected.startup)
            engine = TTSEngine(
                selected, registry, settings.resolved_workers(), store, settings
            )
            app.state.engine = engine
            app.state.voice_lock = asyncio.Lock()
            engine.start()
            logger.info(
                "TTS ready: backend=%s concurrency=%d capacity=%d prefix=%s",
                selected.name,
                engine.config["concurrency"],
                engine.capacity,
                settings.base_path or "/",
            )
            yield
        finally:
            try:
                if engine:
                    await engine.shutdown()
                if selected:
                    await run_in_threadpool(selected.shutdown)
            finally:
                if store:
                    store.close()
                lock.release()

    app = FastAPI(
        title="Umeko TTS Server",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        servers=[{"url": settings.base_url or "/"}],
    )
    app.state.settings, app.state.registry = settings, registry

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return JSONResponse(
            {"message": exc.message}, exc.status_code, headers=exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            {"message": str(exc.detail)}, exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        parts = [
            ".".join(str(x) for x in e["loc"] if x != "body") + ": " + e["msg"]
            for e in exc.errors()
        ]
        return JSONResponse({"message": "请求参数错误: " + "; ".join(parts)}, 400)

    @app.exception_handler(Exception)
    async def unhandled(request, exc):
        logger.exception("Unhandled error: %s %s", request.method, request.url.path)
        return JSONResponse(
            {"message": "服务器内部错误，请联系管理员"},
            500,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    app.include_router(tts_router)
    app.include_router(access_router)
    app.include_router(admin_router)
    static = Path(__file__).parent / "static"

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/console/", methods=["GET", "HEAD"], include_in_schema=False)
    async def console():
        page = (static / "index.html").read_text(encoding="utf-8")
        page = page.replace(
            "__BASE_PATH__", html.escape(settings.base_path, quote=True)
        )
        page = page.replace("__BASE_URL__", html.escape(settings.base_url, quote=True))
        return HTMLResponse(page)

    @app.get("/console", include_in_schema=False)
    async def console_redirect():
        return RedirectResponse(settings.base_path + "/console/")

    @app.get("/console/app.js", include_in_schema=False)
    async def script():
        return FileResponse(static / "app.js", media_type="text/javascript")

    @app.get("/console/style.css", include_in_schema=False)
    async def stylesheet():
        return FileResponse(static / "style.css", media_type="text/css")

    @app.get("/healthz", include_in_schema=False)
    async def health():
        engine = app.state.engine
        if engine._closed or (engine._dispatcher and engine._dispatcher.done()):
            return JSONResponse({"status": "unavailable"}, 503)
        return {"status": "ok"}

    @app.get(
        "/api/openapi", dependencies=[Depends(verify_admin)], include_in_schema=False
    )
    async def schema():
        result = app.openapi()
        result.setdefault("components", {}).setdefault("securitySchemes", {})[
            "ApiKey"
        ] = {
            "type": "http",
            "scheme": "bearer",
            "description": "Administrator-issued API key",
        }
        for path, methods in result["paths"].items():
            if path == "/tts" or path.startswith("/v1/"):
                for operation in methods.values():
                    operation["security"] = [{"ApiKey": []}]
        return result

    app.add_middleware(SecurityMiddleware, settings=settings)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "Authorization", "X-API-Key"],
            expose_headers=["X-Voice", "X-Inference-Ms", "Retry-After", "Location"],
        )
    app.add_middleware(PrefixMiddleware, settings=settings, routes=app.router.routes)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = app.state.settings
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )
