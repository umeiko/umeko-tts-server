"""服务配置：所有项均可通过环境变量覆盖。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import re

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 9880
    data_dir: Path = BASE_DIR / "data"
    backend: str = "gsv"  # Production fails closed; mock must be selected explicitly.
    gsv_root: Path = BASE_DIR / "GPT-SoVITS"
    gsv_config: Path | None = None
    gsv_device: str = "auto"  # auto | cuda | cpu
    gsv_is_half: str = "auto"  # auto | true | false
    workers: int = 0  # 0 = 按硬件自动适配
    max_queue: int = 64
    admin_token: str = ""
    cors_origins: str = ""
    base_url: str = ""
    queue_timeout: int = 120
    aging_seconds: int = 15
    concurrency: int = 1
    session_seconds: int = 28800
    result_ttl: int = 600
    result_max_mb: int = 64
    max_text_chars: int = 4000
    max_upload_mb: int = 512
    forwarded_allow_ips: str = "127.0.0.1"

    @property
    def base_path(self) -> str:
        return urlsplit(self.base_url).path.rstrip("/")

    @property
    def secure_cookie(self) -> bool:
        return self.base_url.startswith("https://")

    def validate(self):
        value = self.base_url.strip().rstrip("/")
        parts = urlsplit(value)
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError(
                "UMEKO_TTS_BASE_URL cannot contain credentials, query or fragment"
            )
        if parts.scheme and (parts.scheme not in ("http", "https") or not parts.netloc):
            raise ValueError(
                "UMEKO_TTS_BASE_URL must be an http(s) URL or an absolute path"
            )
        if not parts.scheme and (parts.netloc or (value and not value.startswith("/"))):
            raise ValueError("UMEKO_TTS_BASE_URL must start with / or http(s)://")
        if parts.path and (
            not re.fullmatch(r"(?:/[A-Za-z0-9_.~-]+)*/?", parts.path)
            or any(p in (".", "..") for p in parts.path.split("/"))
        ):
            raise ValueError("Invalid UMEKO_TTS_BASE_URL path")
        self.base_url = value
        if self.admin_token and len(self.admin_token) < 24:
            raise ValueError("TTS_ADMIN_TOKEN must contain at least 24 characters")
        if not 1 <= self.concurrency <= 32 or not 1 <= self.max_queue <= 1000:
            raise ValueError(
                "TTS_CONCURRENCY must be 1..32; TTS_MAX_QUEUE must be 1..1000"
            )
        if not 1 <= self.queue_timeout <= 3600 or not 1 <= self.aging_seconds <= 600:
            raise ValueError("Invalid queue timeout or priority aging interval")
        if not 60 <= self.result_ttl <= 3600 or not 1 <= self.result_max_mb <= 1024:
            raise ValueError("Invalid result retention limits")
        if (
            not 60 <= self.session_seconds <= 86400
            or not 1 <= self.max_text_chars <= 10000
        ):
            raise ValueError("Invalid session lifetime or text limit")
        if not 1 <= self.max_upload_mb <= 2048:
            raise ValueError("TTS_MAX_UPLOAD_MB must be 1..2048")
        if _int("WEB_CONCURRENCY", 1) > 1:
            raise ValueError(
                "Use one ASGI worker per data directory; the model scheduler is process-local"
            )
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.host = os.environ.get("TTS_HOST", s.host)
        s.port = _int("TTS_PORT", s.port)
        s.data_dir = Path(os.environ.get("TTS_DATA_DIR", str(s.data_dir))).resolve()
        s.backend = os.environ.get("TTS_BACKEND", s.backend).strip().lower()
        s.gsv_root = Path(os.environ.get("GSV_ROOT", str(s.gsv_root))).resolve()
        cfg = os.environ.get("GSV_CONFIG")
        s.gsv_config = (
            Path(cfg).resolve()
            if cfg
            else s.gsv_root / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        )
        s.gsv_device = os.environ.get("GSV_DEVICE", s.gsv_device).strip().lower()
        s.gsv_is_half = os.environ.get("GSV_IS_HALF", s.gsv_is_half).strip().lower()
        s.workers = _int("TTS_WORKERS", s.workers)
        s.max_queue = _int("TTS_MAX_QUEUE", s.max_queue)
        s.admin_token = os.environ.get("TTS_ADMIN_TOKEN", "")
        s.cors_origins = os.environ.get("TTS_CORS_ORIGINS", "")
        s.base_url = os.environ.get("UMEKO_TTS_BASE_URL", "")
        s.forwarded_allow_ips = os.environ.get(
            "TTS_FORWARDED_ALLOW_IPS", s.forwarded_allow_ips
        )
        for field, env in {
            "concurrency": "TTS_CONCURRENCY",
            "queue_timeout": "TTS_QUEUE_TIMEOUT",
            "aging_seconds": "TTS_PRIORITY_AGING_SECONDS",
            "session_seconds": "TTS_SESSION_SECONDS",
            "result_ttl": "TTS_RESULT_TTL",
            "result_max_mb": "TTS_RESULT_MAX_MB",
            "max_text_chars": "TTS_MAX_TEXT_CHARS",
            "max_upload_mb": "TTS_MAX_UPLOAD_MB",
        }.items():
            setattr(s, field, _int(env, getattr(s, field)))
        if s.backend not in ("auto", "gsv", "mock"):
            s.backend = "auto"
        return s.validate()

    def resolved_workers(self) -> int:
        """线程池大小按硬件自动适配：CPU 核数 + 4，再按可用内存收缩。"""
        if self.workers > 0:
            return self.workers
        cpu = os.cpu_count() or 4
        n = cpu + 4
        try:
            import psutil

            mem_gb = psutil.virtual_memory().available / (1024**3)
            n = min(n, max(2, int(mem_gb // 2)))
        except Exception:
            pass
        return max(2, min(32, n))
