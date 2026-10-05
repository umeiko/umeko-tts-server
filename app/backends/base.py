"""推理后端接口定义。"""
from __future__ import annotations

from dataclasses import dataclass


class BackendError(Exception):
    """后端初始化或推理失败。"""


@dataclass
class SynthesisJob:
    text: str
    text_lang: str
    speed_factor: float
    top_k: int
    top_p: float
    temperature: float
    max_sec: float
    voice_name: str
    gpt_weights: str = ""
    sovits_weights: str = ""
    ref_audio_path: str = ""
    ref_text: str = ""
    ref_lang: str = "zh"


class BaseBackend:
    """所有后端返回 (pcm16_bytes, sample_rate)，WAV 封装由引擎统一完成。"""

    name = "base"
    requires_files = True  # mock 后端不需要权重文件

    def startup(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def synthesize(self, job: SynthesisJob) -> tuple[bytes, int]:
        raise NotImplementedError

    def status(self) -> dict:
        return {"name": self.name}
