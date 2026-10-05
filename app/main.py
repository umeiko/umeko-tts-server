"""应用入口：装配配置、注册表、后端、引擎、路由与静态控制台。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .backends import BackendError, GSVBackend, MockBackend
from .config import Settings
from .engine import TTSEngine
from .errors import ApiError
from .registry import VoiceRegistry
from .routes_admin import router as admin_router
from .routes_tts import router as tts_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("umeko-tts")


def select_backend(settings: Settings):
    """auto: 优先 GPT-SoVITS，不可用时回退演示后端并在日志中明示。"""
    if settings.backend == "mock":
        logger.info("后端: mock（演示模式，输出正弦波测试音频）")
        return MockBackend()
    if settings.backend == "gsv":
        backend = GSVBackend(settings)
        backend.startup()  # 强制模式：失败直接抛错，不静默回退
        logger.info("后端: GPT-SoVITS 初始化完成")
        return backend
    try:
        backend = GSVBackend(settings)
        backend.startup()
        logger.info("后端: GPT-SoVITS 初始化完成（auto 模式）")
        return backend
    except BackendError as e:
        logger.warning("GPT-SoVITS 不可用，回退到演示后端: %s", e)
        return MockBackend()


def create_app() -> FastAPI:
    settings = Settings.from_env()
    registry = VoiceRegistry(settings.data_dir)
    backend = select_backend(settings)
    engine = TTSEngine(
        backend=backend,
        registry=registry,
        max_workers=settings.resolved_workers(),
        max_queue=settings.max_queue,
    )
    logger.info("线程池大小: %d，最大排队: %s", engine.max_workers, settings.max_queue or "不限")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.engine.shutdown()
        app.state.engine.backend.shutdown()

    app = FastAPI(title="Umeko TTS Server", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.registry = registry
    app.state.engine = engine

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse({"message": exc.message}, status_code=exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        # 统一错误格式为 {"message": ...}（覆盖 404、JSON 解析失败等框架级错误）
        detail = exc.detail
        if detail == "There was an error parsing the body":
            detail = "请求体不是合法的 JSON"
        return JSONResponse({"message": str(detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        parts = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []) if x != "body")
            msg = err.get("msg", "")
            parts.append(f"{loc}: {msg}" if loc else msg)
        return JSONResponse({"message": "请求参数错误: " + "; ".join(parts)}, status_code=400)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse({"message": f"服务器内部错误: {exc}"}, status_code=500)

    app.include_router(tts_router)
    app.include_router(admin_router)

    static_dir = Path(__file__).parent / "static"
    app.mount("/console", StaticFiles(directory=static_dir, html=True), name="console")

    @app.get("/")
    async def index():
        return RedirectResponse("/console/")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = Settings.from_env()
    # 直接传入已创建的 app 实例，避免字符串导入导致后端被初始化两次
    uvicorn.run(app, host=settings.host, port=settings.port)
