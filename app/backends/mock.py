"""演示后端：不依赖任何模型与第三方库，生成确定性的正弦波测试音频。

用途：在未安装 GPT-SoVITS 的环境（如开发机）验证 API、控制台与并发行为。
接入真实模型后由 TTS_BACKEND=gsv / auto 切换到真实推理。
"""
from __future__ import annotations

import hashlib
import math
from array import array

from .base import BaseBackend, SynthesisJob

SR = 32000
_PUNCS = set("，。！？；：、,.!?;:… \t\r\n")


class MockBackend(BaseBackend):
    name = "mock"
    requires_files = False

    def synthesize(self, job: SynthesisJob) -> tuple[bytes, int]:
        text = job.text or ""
        speed = max(0.5, min(2.0, job.speed_factor))
        max_samples = int(job.max_sec * SR)
        samples = array("h")
        for i, ch in enumerate(text):
            if len(samples) >= max_samples:
                break
            if ch in _PUNCS:
                samples.extend([0] * int(SR * 0.18 / speed))
                continue
            digest = hashlib.sha256(f"{job.voice_name}|{ch}|{i}".encode()).digest()
            duration = (0.16 if ord(ch) > 0x2E7F else 0.06) / speed
            n = max(1, int(SR * duration))
            f0 = 180 + (digest[0] / 255) * 220
            for t in range(n):
                x = t / SR
                envelope = min(1.0, t / (SR * 0.01), (n - t) / (SR * 0.02))
                wave = math.sin(2 * math.pi * f0 * x) + 0.3 * math.sin(4 * math.pi * f0 * x)
                samples.append(int(wave * envelope * 12000))
        if not samples:
            samples.extend([0] * int(SR * 0.3))
        return samples.tobytes(), SR

    def status(self) -> dict:
        return {
            "name": self.name,
            "demo": True,
            "detail": "演示后端：未接入 GPT-SoVITS，输出为正弦波测试音频",
        }
