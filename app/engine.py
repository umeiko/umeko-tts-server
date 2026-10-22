"""Bounded user-aware priority scheduler; only actual inference occupies a slot."""

from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from .access import AccessStore, Principal
from .backends.base import BaseBackend, SynthesisJob
from .errors import ApiError
from .registry import Voice, VoiceRegistry
from .schemas import TTSRequest

logger = logging.getLogger("umeko-tts")
TERMINAL = {"completed", "failed", "cancelled", "expired"}


class QueueFullError(ApiError):
    def __init__(self, message="服务排队已满，请稍后重试"):
        super().__init__(429, message, {"Retry-After": "3"})


@dataclass
class Job:
    id: str
    principal: Principal
    voice: Voice
    params: TTSRequest | None
    chars: int
    done: asyncio.Future
    created: float = field(default_factory=time.time)
    queued_at: float = field(default_factory=time.monotonic)
    state: str = "queued"
    started: float | None = None
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    wav: bytes | None = None
    meta: dict = field(default_factory=dict)
    error: str = ""
    error_status: int = 500


class TTSEngine:
    def __init__(
        self,
        backend: BaseBackend,
        registry: VoiceRegistry,
        max_workers: int,
        store: AccessStore,
        settings,
    ):
        self.backend, self.registry, self.store, self.settings = (
            backend,
            registry,
            store,
            settings,
        )
        self.capacity = max(1, min(max_workers, backend.max_concurrency))
        self.max_workers = self.capacity
        self.config = store.load_config(
            {
                "concurrency": settings.concurrency,
                "max_queue": settings.max_queue,
                "queue_timeout": settings.queue_timeout,
                "aging_seconds": settings.aging_seconds,
            }
        )
        self.config["concurrency"] = min(self.capacity, self.config["concurrency"])
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="tts"
        )
        self.jobs: dict[str, Job] = {}
        self._running: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()
        self._event = asyncio.Event()
        self._dispatcher: asyncio.Task | None = None
        self._closed = False
        self._editing: set[str] = set()
        self._counts = {"completed": 0, "failed": 0, "cancelled": 0, "expired": 0}
        self.started_at = time.time()

    def start(self):
        self._dispatcher = asyncio.create_task(
            self._dispatch(), name="tts-priority-scheduler"
        )

    def configure(self, values: dict):
        if values["concurrency"] > self.capacity:
            raise ApiError(400, f"当前后端安全推理并行上限为 {self.capacity}")
        self.store.save_config(values)
        self.config = values
        self._event.set()

    def submit(self, voice: Voice, params: TTSRequest, principal: Principal) -> Job:
        if voice.name in self._editing:
            raise ApiError(409, "音色正在更新，请稍后重试")
        if self._closed or (self._dispatcher and self._dispatcher.done()):
            raise ApiError(503, "服务正在停止", {"Retry-After": "5"})
        self._prune()
        queued = [j for j in self.jobs.values() if j.state == "queued"]
        if len(queued) >= self.config["max_queue"]:
            raise QueueFullError()
        p = self.store.policy(principal.user_id, principal.key_id)
        if sum(j.principal.user_id == p.user_id for j in queued) >= p.max_queue:
            raise QueueFullError("该用户的排队请求数已达上限")
        jid = uuid.uuid4().hex
        p = self.store.reserve(jid, p, len(params.text))
        # No yield between admission, quota reservation and queue insertion.
        job = Job(
            jid,
            p,
            replace(voice),
            params,
            len(params.text),
            asyncio.get_running_loop().create_future(),
        )
        self.jobs[jid] = job
        self._event.set()
        return job

    def begin_voice_edit(self, name):
        if any(
            j.voice.name == name and j.state not in TERMINAL for j in self.jobs.values()
        ):
            raise ApiError(409, "此音色仍有排队或合成任务，请等待完成或取消任务")
        self._editing.add(name)

    def end_voice_edit(self, name):
        self._editing.discard(name)
        self.backend.invalidate_cache()

    async def wait(self, job: Job):
        await asyncio.shield(job.done)
        if job.state != "completed":
            raise ApiError(
                job.error_status, job.error or "任务已取消", {"Retry-After": "3"}
            )
        if job.wav is None:
            raise ApiError(410, "音频结果已过期，请重新提交")
        return job.wav, job.meta

    async def synthesize(self, voice: Voice, params: TTSRequest, principal: Principal):
        job = self.submit(voice, params, principal)
        try:
            return await self.wait(job)
        except asyncio.CancelledError:
            self.cancel_job(job.id)
            raise

    def _rank(self, job: Job):
        effective = job.principal.priority + int(
            (time.monotonic() - job.queued_at) / self.config["aging_seconds"]
        )
        return (-effective, job.queued_at)

    def _refresh_waiting(self):
        policies = {}
        for job in list(self.jobs.values()):
            if job.state != "queued":
                continue
            try:
                key = (job.principal.user_id, job.principal.key_id)
                if key not in policies:
                    policies[key] = self.store.policy(*key)
                job.principal = policies[key]
            except ApiError:
                self._finish(job, "cancelled", "用户或密钥已停用", 401)
                continue
            if time.monotonic() - job.queued_at >= self.config["queue_timeout"]:
                self._finish(job, "expired", "排队超时，请稍后重试", 503)

    async def _dispatch(self):
        try:
            await self._dispatch_loop()
        except Exception:
            self._unavailable()

    def _unavailable(self, exclude=None):
        logger.exception("Scheduler control state unavailable; rejecting new work")
        self._closed = True
        self._event.set()
        for job in self.jobs.values():
            if job.state == "queued" and job.id != exclude:
                job.state, job.error_status = "failed", 503
                job.error, job.finished = "调度服务不可用，请稍后重试", time.time()
                job.params = None
                self._counts["failed"] += 1
                if not job.done.done():
                    job.done.set_result(None)

    async def _dispatch_loop(self):
        while not self._closed:
            self._event.clear()
            self._refresh_waiting()
            self._prune()
            while len(self._running) < self.config["concurrency"]:
                waiting = sorted(
                    (j for j in self.jobs.values() if j.state == "queued"),
                    key=self._rank,
                )
                job = next(
                    (
                        j
                        for j in waiting
                        if sum(
                            r.principal.user_id == j.principal.user_id
                            for r in self._running.values()
                        )
                        < j.principal.max_concurrent
                    ),
                    None,
                )
                if job is None:
                    break
                job.state, job.started = "running", time.time()
                self._running[job.id] = job
                task = asyncio.create_task(self._execute(job), name="tts-" + job.id)
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            try:
                await asyncio.wait_for(self._event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass

    async def _execute(self, job: Job):
        work = asyncio.get_running_loop().run_in_executor(
            self._executor, self._run, job
        )
        try:
            try:
                wav = await asyncio.shield(work)
            except asyncio.CancelledError:
                # Cancelling a client must not free a still-running model thread's slot.
                job.cancel.set()
                wav = await work
            if job.cancel.is_set():
                self._finish(job, "cancelled", "任务已取消", 409)
            else:
                job.wav = wav
                job.meta = {
                    "voice": job.voice.name,
                    "ms": round((time.time() - job.created) * 1000, 1),
                }
                self._finish(job, "completed")
        except Exception:
            logger.exception("Inference failed for request %s", job.id)
            self._finish(
                job, "failed", f"语音合成失败，请联系管理员，任务号：{job.id}", 500
            )
        finally:
            self._running.pop(job.id, None)
            job.params = None
            self._event.set()

    def _finish(self, job: Job, state: str, error="", status=500):
        if job.state in TERMINAL:
            return
        try:
            self.store.finish(job.id, state)
        except sqlite3.Error:
            self._unavailable(exclude=job.id)
            state, error, status = "failed", "状态存储不可用，请稍后重试", 503
        job.state, job.error, job.error_status, job.finished = (
            state,
            error,
            status,
            time.time(),
        )
        self._counts[state] += 1
        if state != "completed":
            job.params = None
        if not job.done.done():
            job.done.set_result(None)

    def cancel_job(self, job_id: str):
        job = self.get_job(job_id)
        if job.state == "queued":
            job.cancel.set()
            self._finish(job, "cancelled", "任务已取消", 409)
        elif job.state in ("running", "cancelling"):
            job.cancel.set()
            job.state = "cancelling"
        self._event.set()
        return job

    def invalidate(self, *, user_id=None, key_id=None):
        for job in list(self.jobs.values()):
            if (
                (user_id and job.principal.user_id == user_id)
                or (key_id and job.principal.key_id == key_id)
            ) and job.state not in TERMINAL:
                self.cancel_job(job.id)
        self._event.set()

    def get_job(self, job_id: str) -> Job:
        self._prune()
        if job_id not in self.jobs:
            raise ApiError(404, "任务不存在或结果已过期")
        return self.jobs[job_id]

    def _prune(self):
        now = time.time()
        finished = sorted(
            (j for j in self.jobs.values() if j.state in TERMINAL),
            key=lambda j: j.finished or 0,
        )
        size = sum(len(j.wav or b"") for j in finished)
        for job in finished:
            if (
                (job.finished or now) + self.settings.result_ttl < now
                or size > self.settings.result_max_mb * 1024**2
                or len(self.jobs) > 256
            ):
                size -= len(job.wav or b"")
                del self.jobs[job.id]

    def job_info(self, job: Job, admin=False):
        waiting = sorted(
            (j for j in self.jobs.values() if j.state == "queued"), key=self._rank
        )
        result = {
            "id": job.id,
            "state": job.state,
            "voice": job.voice.name,
            "chars": job.chars,
            "created": job.created,
            "started": job.started,
            "finished": job.finished,
            "position": waiting.index(job) + 1 if job in waiting else None,
            "error": job.error,
            "expires": job.finished + self.settings.result_ttl
            if job.finished
            else None,
        }
        if admin:
            result.update(
                user_id=job.principal.user_id,
                user_name=job.principal.name,
                key_id=job.principal.key_id,
                priority=job.principal.priority,
            )
        return result

    def queue(self):
        return [
            self.job_info(j, admin=True)
            for j in sorted(self.jobs.values(), key=lambda j: j.created)
            if j.state not in TERMINAL
        ]

    def stats(self):
        queued = sum(j.state == "queued" for j in self.jobs.values())
        return {
            "pending": queued + len(self._running),
            "queued": queued,
            "running": len(self._running),
            **self._counts,
        }

    def status(self):
        return {
            "backend": self.backend.status(),
            "workers": self.max_workers,
            "capacity": self.capacity,
            "config": self.config,
            "max_queue": self.config["max_queue"],
            "queue": self.stats(),
            "uptime_sec": int(time.time() - self.started_at),
        }

    async def shutdown(self):
        self._closed = True
        self._event.set()
        if self._dispatcher:
            await self._dispatcher
        for job in list(self.jobs.values()):
            if job.state not in TERMINAL:
                self.cancel_job(job.id)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._executor.shutdown(wait=True)

    def _run(self, job: Job) -> bytes:
        params, voice = job.params, job.voice
        pcm, sr = bytearray(), 0
        for seg in self._split_text(params.text, params.cut_punc):
            if job.cancel.is_set():
                break
            chunk, sr = self.backend.synthesize(self._build_job(voice, params, seg))
            pcm += chunk
            limit = int(params.max_sec * sr) * 2
            if len(pcm) >= limit:
                del pcm[limit:]
                break
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr or 32000)
            w.writeframes(pcm)
        return buf.getvalue()

    def _build_job(self, voice: Voice, params: TTSRequest, text: str) -> SynthesisJob:
        def path(rel):
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
            gpt_weights=path(voice.gpt_weights),
            sovits_weights=path(voice.sovits_weights),
            ref_audio_path=path(voice.ref_audio),
            ref_text=voice.ref_text,
            ref_lang=voice.ref_lang,
        )

    @staticmethod
    def _split_text(text: str, cut_punc: str) -> list[str]:
        if not cut_punc:
            return [text]
        parts, buf = [], []
        for ch in text:
            buf.append(ch)
            if ch in cut_punc:
                parts.append("".join(buf).strip())
                buf.clear()
        if buf:
            parts.append("".join(buf).strip())
        return [s for s in parts if s] or [text]
