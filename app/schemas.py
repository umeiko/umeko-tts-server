"""请求 / 响应数据模型。/tts 参数与 Mambo TTS API 完全兼容。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator

SUPPORTED_LANGS = ("zh", "en", "ja", "ko", "yue", "auto")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class TTSRequest(BaseModel):
    text: str
    text_lang: str = "zh"
    speed_factor: float = 1.0
    top_k: int = 15
    top_p: float = 1.0
    temperature: float = 1.0
    cut_punc: str = ""
    max_sec: float = 10.0
    # 扩展参数（可选）：指定音色名称，缺省使用默认音色。不影响 Mambo 兼容性。
    voice: Optional[str] = None

    @field_validator("text_lang")
    @classmethod
    def _check_lang(cls, v: str) -> str:
        v = (v or "zh").strip().lower()
        if v not in SUPPORTED_LANGS:
            raise ValueError(
                f"不支持的语种: {v}，可选: {'/'.join(SUPPORTED_LANGS)}"
            )
        return v

    def normalized(self) -> "TTSRequest":
        """把数值参数收敛到安全范围（越界不报错，直接钳位）。"""
        return self.model_copy(
            update={
                "speed_factor": _clamp(float(self.speed_factor), 0.5, 2.0),
                "top_k": int(_clamp(int(self.top_k), 1, 100)),
                "top_p": _clamp(float(self.top_p), 0.0, 1.0),
                "temperature": _clamp(float(self.temperature), 0.1, 2.0),
                "max_sec": _clamp(float(self.max_sec), 0.5, 60.0),
            }
        )
