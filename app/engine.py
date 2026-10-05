"""推理引擎：线程池 + 全局串行锁，支撑并发请求安全排队。

- 每个请求投递到 ThreadPoolExecutor，在后台线程执行，不阻塞事件循环；
- 真正的推理（含权重切换）由全局串行锁保护，多请求自动排队，不会竞态；
- cut_punc 文本切分与 max_sec 长度截断在引擎层实现，对所有后端生效。
"""
from __future__ import annotations

import asyncio
import io
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

from .backends.base import BaseBackend, SynthesisJob
from .registry import Voice, VoiceRegistry
from .schemas import TTSRequest


class QueueFullError(Exception):
    """排队队列已满。"""


class TTSEngine:
    def __init__(
        self,
        backend: BaseBackend,
        registry: VoiceRegistry,
        max_workers: int,
        max_queue: int = 0,
    ):
        self.backend = backend
        self.registry = registry
        self.max_workers = max_workers
        self.max_queue = max_queue
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="tts-worker"
        )
        self._serial = threading.Lock()  # 推理串行锁
        self._stat_lock = threading.Lock()
        self._pending = 0  # 已受理未完成（含正在执行）
        self._running = 0  # 已进入执行阶段（含等待串行锁）
        self._completed = 0
        self._failed = 0
        self.started_at = time.time()

    async def synthesize(self, voice: Voice, params: TTSRequest) -> tuple[bytes, dict]:
        with self._stat_lock:
            if self.max_queue and self._pending >= self.max_queue:
                raise QueueFullError()
            self._pending += 1
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        try:
            wav = await loop.run_in_executor(self._executor, self._run, voice, params)
        except Exception:
            with self._stat_lock:
                self._pending -= 1
                self._failed += 1
            raise
        ms = (time.perf_counter() - t0) * 1000
        with self._stat_lock:
            self._pending -= 1
            self._completed += 1
        return wav, {"voice": voice.name, "ms": round(ms, 1)}

    def _run(self, voice: Voice, params: TTSRequest) -> bytes:
        segments = self._split_text(params.text, params.cut_punc)
        pcm = bytearray()
        sr = 0
        with self._stat_lock:
            self._running += 1
        try:
            with self._serial:
                for seg in segments:
                    job = self._build_job(voice, params, seg)
                    chunk, sr = self.backend.synthesize(job)
                    pcm += chunk
        finally:
            with self._stat_lock:
                self._running -= 1
        if sr > 0 and params.max_sec > 0:
            limit = int(params.max_sec * sr) * 2  # 16bit 单声道
            if len(pcm) > limit:
                del pcm[limit:]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr or 32000)
            w.writeframes(bytes(pcm))
        return buf.getvalue()

    def _build_job(self, voice: Voice, params: TTSRequest, text: str) -> SynthesisJob:
        def abs_or_empty(rel: str) -> str:
            p = self.registry.abs_path(rel)
            return str(p) if p else ""

        return SynthesisJob(
            text=text,
            text_lang=params.text_lang,
            speed_factor=params.speed_factor,
            top_k=params.top_k,
            top_p=params.top_p,
            temperature=params.temperature,
            max_sec=params.max_sec,
            voice_name=voice.name,
            gpt_weights=abs_or_empty(voice.gpt_weights),
            sovits_weights=abs_or_empty(voice.sovits_weights),
            ref_audio_path=abs_or_empty(voice.ref_audio),
            ref_text=voice.ref_text,
            ref_lang=voice.ref_lang,
        )

    @staticmethod
    def _split_text(text: str, cut_punc: str) -> list[str]:
        """cut_punc 为空：整段交给后端自动切分；否则按指定标点手动切分后逐段合成。"""
        if not cut_punc:
            return [text]
        marks = set(cut_punc)
        parts, buf = [], []
        for ch in text:
            buf.append(ch)
            if ch in marks:
                seg = "".join(buf).strip()
                if seg:
                    parts.append(seg)
                buf.clear()
        tail = "".join(buf).strip()
        if tail:
            parts.append(tail)
        return parts or [text]

    def stats(self) -> dict:
        with self._stat_lock:
            return {
                "pending": self._pending,
                "running": self._running,
                "completed": self._completed,
                "failed": self._failed,
            }

    def status(self) -> dict:
        return {
            "backend": self.backend.status(),
            "workers": self.max_workers,
            "max_queue": self.max_queue,
            "queue": self.stats(),
            "uptime_sec": int(time.time() - self.started_at),
        }

    def shutdown(self):
        self._executor.shutdown(wait=False)
