"""服务配置：所有项均可通过环境变量覆盖。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
    backend: str = "auto"  # auto | gsv | mock
    gsv_root: Path = BASE_DIR / "GPT-SoVITS"
    gsv_config: Path | None = None
    gsv_device: str = "auto"  # auto | cuda | cpu
    gsv_is_half: str = "auto"  # auto | true | false
    workers: int = 0  # 0 = 按硬件自动适配
    max_queue: int = 0  # 0 = 不限排队长度
    admin_token: str = ""
    cors_origins: str = "*"

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
        s.cors_origins = os.environ.get("TTS_CORS_ORIGINS", "*")
        if s.backend not in ("auto", "gsv", "mock"):
            s.backend = "auto"
        return s

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
