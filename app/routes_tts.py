"""Mambo 兼容的 /tts 合成接口。

成功返回 audio/wav 二进制；失败返回 {"message": "错误描述"} 的 JSON。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .backends.base import BackendError
from .engine import QueueFullError
from .errors import ApiError
from .registry import VoiceError
from .schemas import TTSRequest

router = APIRouter()


@router.post("/tts")
async def tts(request: Request, body: TTSRequest):
    text = body.text.strip()
    if not text:
        raise ApiError(400, "text 不能为空")
    params = body.normalized()
    params.text = text

    reg = request.app.state.registry
    engine = request.app.state.engine

    try:
        voice = reg.resolve(body.voice)
    except VoiceError as e:
        raise ApiError(404 if body.voice else 400, str(e))

    if engine.backend.requires_files and not reg.is_complete(voice):
        raise ApiError(
            400,
            f"音色「{voice.name}」不完整：请在控制台上传 GPT 权重、SoVITS 权重和参考音频",
        )

    try:
        wav, meta = await engine.synthesize(voice, params)
    except QueueFullError:
        raise ApiError(503, "服务繁忙，队列已满，请稍后重试")
    except BackendError as e:
        raise ApiError(500, str(e))

    return Response(
        content=wav,
        media_type="audio/wav",
        headers={
            # 音色名可能含中文，HTTP 头需 URL 编码
            "X-Voice": quote(meta["voice"]),
            "X-Inference-Ms": str(meta["ms"]),
            "Content-Disposition": 'inline; filename="tts.wav"',
        },
    )
