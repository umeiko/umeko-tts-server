"""音色注册表：JSON 文件持久化，线程安全，管理音色元数据与权重文件路径。"""
from __future__ import annotations

import json
import re
import shutil
import threading
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path

# 允许中英文、数字、下划线、短横线，1-64 字
NAME_RE = re.compile(r"^[\w\-一-鿿]{1,64}$")

GPT_EXTS = {".ckpt", ".safetensors"}
SOVITS_EXTS = {".pth", ".ckpt", ".safetensors"}
AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}

# 上传文件 kind -> Voice 字段名
FILE_FIELD = {"gpt": "gpt_weights", "sovits": "sovits_weights", "ref_audio": "ref_audio"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Voice:
    name: str
    description: str = ""
    ref_text: str = ""
    ref_lang: str = "zh"
    gpt_weights: str = ""  # 相对 data_dir 的路径
    sovits_weights: str = ""
    ref_audio: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


class VoiceError(Exception):
    """音色相关的业务错误。"""


class VoiceRegistry:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.voices_dir = self.data_dir / "voices"
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.data_dir / "voices.json"
        self._lock = threading.RLock()
        self._default: str | None = None
        self._voices: dict[str, Voice] = {}
        self._load()

    # ---------- 持久化 ----------

    def _load(self):
        if not self._file.is_file():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._default = data.get("default")
            known = {f.name for f in fields(Voice)} - {"name"}
            for name, item in (data.get("voices") or {}).items():
                kwargs = {k: v for k, v in item.items() if k in known}
                self._voices[name] = Voice(name=name, **kwargs)
        except Exception:
            # 文件损坏时备份而不是覆盖，避免丢音色
            shutil.copy2(self._file, self._file.with_suffix(".broken.json"))

    def _save(self):
        data = {
            "default": self._default,
            "voices": {
                n: {k: v for k, v in vars(voice).items() if k != "name"}
                for n, voice in self._voices.items()
            },
        }
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._file)

    # ---------- 查询 ----------

    def list(self) -> list[Voice]:
        with self._lock:
            return sorted(self._voices.values(), key=lambda v: v.created_at)

    def get(self, name: str) -> Voice | None:
        with self._lock:
            return self._voices.get(name)

    @property
    def default(self) -> str | None:
        with self._lock:
            return self._default

    def resolve(self, name: str | None) -> Voice:
        """按名称取音色；name 为空时取默认音色，再退化为第一个音色。"""
        with self._lock:
            if name:
                v = self._voices.get(name)
                if v is None:
                    raise VoiceError(f"音色「{name}」不存在")
                return v
            if self._default and self._default in self._voices:
                return self._voices[self._default]
            if self._voices:
                return next(iter(self._voices.values()))
            raise VoiceError("还没有可用音色，请先在控制台新增音色")

    # ---------- 变更 ----------

    @staticmethod
    def validate_name(name: str):
        if not NAME_RE.match(name or ""):
            raise VoiceError("音色名称只能包含中英文、数字、下划线和短横线，长度 1-64")

    def create(self, name: str, description="", ref_text="", ref_lang="zh") -> Voice:
        self.validate_name(name)
        with self._lock:
            if name in self._voices:
                raise VoiceError(f"音色「{name}」已存在")
            v = Voice(name=name, description=description, ref_text=ref_text, ref_lang=ref_lang)
            self._voices[name] = v
            self.voice_dir(name).mkdir(parents=True, exist_ok=True)
            if not self._default:
                self._default = name
            self._save()
            return v

    def update_meta(self, name: str, description=None, ref_text=None, ref_lang=None) -> Voice:
        with self._lock:
            v = self._require(name)
            if description is not None:
                v.description = description
            if ref_text is not None:
                v.ref_text = ref_text
            if ref_lang is not None:
                v.ref_lang = ref_lang
            v.updated_at = _now()
            self._save()
            return v

    def set_file(self, name: str, kind: str, rel_path: str) -> Voice:
        with self._lock:
            v = self._require(name)
            setattr(v, FILE_FIELD[kind], rel_path)
            v.updated_at = _now()
            self._save()
            return v

    def delete(self, name: str):
        with self._lock:
            self._require(name)
            del self._voices[name]
            if self._default == name:
                self._default = None
            self._save()
        shutil.rmtree(self.voice_dir(name), ignore_errors=True)

    def set_default(self, name: str):
        with self._lock:
            self._require(name)
            self._default = name
            self._save()

    # ---------- 路径与状态 ----------

    def voice_dir(self, name: str) -> Path:
        return self.voices_dir / name

    def abs_path(self, rel: str) -> Path | None:
        if not rel:
            return None
        return self.data_dir / rel

    def file_exists(self, v: Voice, kind: str) -> bool:
        p = self.abs_path(getattr(v, FILE_FIELD[kind]))
        return bool(p) and p.is_file()

    def is_complete(self, v: Voice) -> bool:
        return all(self.file_exists(v, k) for k in ("gpt", "sovits", "ref_audio"))

    def to_dict(self, v: Voice) -> dict:
        return {
            "name": v.name,
            "description": v.description,
            "ref_text": v.ref_text,
            "ref_lang": v.ref_lang,
            "has_gpt": self.file_exists(v, "gpt"),
            "has_sovits": self.file_exists(v, "sovits"),
            "has_ref_audio": self.file_exists(v, "ref_audio"),
            "is_complete": self.is_complete(v),
            "is_default": v.name == self._default,
            "created_at": v.created_at,
            "updated_at": v.updated_at,
        }

    def _require(self, name: str) -> Voice:
        v = self._voices.get(name)
        if v is None:
            raise VoiceError(f"音色「{name}」不存在")
        return v
