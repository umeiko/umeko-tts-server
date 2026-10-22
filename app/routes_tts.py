"""Authenticated synchronous synthesis and pollable jobs for long ALB requests."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .errors import ApiError
from .registry import VoiceError
from .schemas import TTSRequest

router = APIRouter()


def submit(request, body):
    st = request.app.state
    if len(body.text) > st.settings.max_text_chars:
        raise ApiError(400, f"服务单次文本上限为 {st.settings.max_text_chars} 字符")
    try:
        voice = st.registry.resolve(body.voice)
        if st.engine.backend.requires_files and not st.registry.is_complete(voice):
            raise ApiError(400, "音色尚未准备好，请联系管理员")
    except VoiceError as exc:
        raise ApiError(404, str(exc)) from None
    return st.engine.submit(voice, body.normalized(), request.state.principal)


def audio_response(wav, meta):
    return Response(
        wav,
        media_type="audio/wav",
        headers={
            "X-Voice": quote(meta["voice"]),
            "X-Inference-Ms": str(meta["ms"]),
            "Content-Disposition": 'inline; filename="tts.wav"',
        },
    )


def owned_job(request, job_id):
    job = request.app.state.engine.get_job(job_id)
    if (
        request.state.principal.user_id != "admin"
        and job.principal.user_id != request.state.principal.user_id
    ):
        raise ApiError(404, "任务不存在或结果已过期")
    return job


def job_info(request, job):
    result = request.app.state.engine.job_info(job)
    base = request.app.state.settings.base_path + "/v1/jobs/" + job.id
    result.update(
        status_url=base, audio_url=base + "/audio" if job.state == "completed" else None
    )
    return result


@router.post("/tts")
async def tts(request: Request, body: TTSRequest):
    engine = request.app.state.engine
    job = submit(request, body)
    try:
        # Keep cancelled/disconnected requests from leaving queued work behind.
        while not job.done.done():
            if await request.is_disconnected():
                engine.cancel_job(job.id)
                raise ApiError(499, "客户端已断开")
            await asyncio.wait([job.done], timeout=0.2)
        wav, meta = await engine.wait(job)
        return audio_response(wav, meta)
    except asyncio.CancelledError:
        engine.cancel_job(job.id)
        raise


@router.post("/v1/jobs", status_code=202)
async def create_job(request: Request, body: TTSRequest):
    job = submit(request, body)
    info = job_info(request, job)
    return JSONResponse(
        info, 202, headers={"Location": info["status_url"], "Retry-After": "1"}
    )


@router.get("/v1/jobs/{job_id}")
async def get_job(request: Request, job_id: str):
    return job_info(request, owned_job(request, job_id))


@router.get("/v1/jobs/{job_id}/audio")
async def get_audio(request: Request, job_id: str):
    job = owned_job(request, job_id)
    if job.state != "completed":
        raise ApiError(409, job.error or "任务尚未完成")
    if job.wav is None:
        raise ApiError(410, "音频结果已过期")
    return audio_response(job.wav, job.meta)


@router.delete("/v1/jobs/{job_id}")
async def cancel_job(request: Request, job_id: str):
    job = owned_job(request, job_id)
    return job_info(request, request.app.state.engine.cancel_job(job.id))


@router.get("/v1/voices")
async def voices(request: Request):
    st = request.app.state
    return [
        {
            "name": v.name,
            "description": v.description,
            "is_default": v.name == st.registry.default,
        }
        for v in st.registry.list()
        if not st.engine.backend.requires_files or st.registry.is_complete(v)
    ]


@router.get("/v1/me")
async def me(request: Request):
    p = request.state.principal
    user = next(
        (u for u in request.app.state.access.users() if u["id"] == p.user_id), None
    )
    return {
        "user": p.name,
        "priority": p.priority,
        "max_concurrent": p.max_concurrent,
        "max_queue": p.max_queue,
        "rpm": p.rpm,
        "daily_chars": p.daily_chars,
        "max_text_chars": min(
            p.max_text_chars, request.app.state.settings.max_text_chars
        ),
        "used_chars": user["used_chars"] if user else 0,
        "reserved_chars": user["reserved_chars"] if user else 0,
        "quota_timezone": "UTC",
    }
