"""音色管理与系统状态接口（/api/*）。

若设置了环境变量 TTS_ADMIN_TOKEN，所有管理接口要求请求头 X-Admin-Token。
"""

from __future__ import annotations

import functools
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from .errors import ApiError
from .registry import AUDIO_EXTS, GPT_EXTS, SOVITS_EXTS, VoiceError, VoiceRegistry
from .security import verify_admin

router = APIRouter(prefix="/api")

_STEM = {"gpt": "gpt_weights", "sovits": "sovits_weights", "ref_audio": "ref_audio"}
_EXTS = {"gpt": GPT_EXTS, "sovits": SOVITS_EXTS, "ref_audio": AUDIO_EXTS}
_LABEL = {"gpt": "GPT 权重", "sovits": "SoVITS 权重", "ref_audio": "参考音频"}


def voice_mutation(fn):
    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        request, name = kwargs["request"], kwargs["name"]
        engine = request.app.state.engine
        async with request.app.state.voice_lock:
            engine.begin_voice_edit(name)
            try:
                return await fn(*args, **kwargs)
            finally:
                engine.end_voice_edit(name)

    return wrapped


def _registry(request: Request) -> VoiceRegistry:
    return request.app.state.registry


def _has_file(upload: Optional[UploadFile]) -> bool:
    return upload is not None and bool(upload.filename)


async def _save_upload(reg: VoiceRegistry, name: str, kind: str, upload: UploadFile):
    ext = Path(upload.filename).suffix.lower()
    if ext not in _EXTS[kind]:
        raise ApiError(
            400,
            f"{_LABEL[kind]}仅支持 {'/'.join(sorted(_EXTS[kind]))} 格式，收到: {ext or '未知'}",
        )
    vdir = reg.voice_dir(name)
    vdir.mkdir(parents=True, exist_ok=True)
    dest = vdir / f"{_STEM[kind]}{ext}"
    staged = vdir / (".upload-" + uuid.uuid4().hex)

    def _copy():
        total = 0
        with staged.open("wb") as f:
            for chunk in iter(lambda: upload.file.read(1024 * 1024), b""):
                total += len(chunk)
                f.write(chunk)
        if total == 0:
            raise ApiError(400, "上传文件不能为空")
        staged.replace(dest)
        for old in vdir.glob(f"{_STEM[kind]}.*"):
            if old != dest:
                old.unlink()

    try:
        await run_in_threadpool(_copy)
    finally:
        staged.unlink(missing_ok=True)
    reg.set_file(name, kind, f"voices/{name}/{dest.name}")


@router.get("/status", dependencies=[Depends(verify_admin)])
async def status(request: Request):
    st = request.app.state
    reg = st.registry
    return {
        "engine": st.engine.status(),
        "voices": len(reg.list()),
        "default_voice": reg.default,
        "admin_auth": True,
        "base_url": st.settings.base_url,
        "base_path": st.settings.base_path,
        "limits": {
            "max_text_chars": st.settings.max_text_chars,
            "result_ttl": st.settings.result_ttl,
        },
        "data_dir": str(st.settings.data_dir),
        "gsv_root": str(st.settings.gsv_root),
    }


@router.get("/voices", dependencies=[Depends(verify_admin)])
async def list_voices(request: Request):
    reg = _registry(request)
    return [reg.to_dict(v) for v in reg.list()]


@router.post("/voices", dependencies=[Depends(verify_admin)])
@voice_mutation
async def create_voice(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    ref_text: str = Form(""),
    ref_lang: str = Form("zh"),
    gpt_file: Optional[UploadFile] = File(None),
    sovits_file: Optional[UploadFile] = File(None),
    ref_audio: Optional[UploadFile] = File(None),
):
    reg = _registry(request)
    try:
        v = reg.create(name.strip(), description, ref_text, ref_lang)
    except VoiceError as e:
        raise ApiError(400, str(e))
    for kind, up in (
        ("gpt", gpt_file),
        ("sovits", sovits_file),
        ("ref_audio", ref_audio),
    ):
        if _has_file(up):
            await _save_upload(reg, v.name, kind, up)
    return reg.to_dict(reg.get(v.name))


@router.put("/voices/{name}", dependencies=[Depends(verify_admin)])
@voice_mutation
async def update_voice(
    request: Request,
    name: str,
    description: Optional[str] = Form(None),
    ref_text: Optional[str] = Form(None),
    ref_lang: Optional[str] = Form(None),
    gpt_file: Optional[UploadFile] = File(None),
    sovits_file: Optional[UploadFile] = File(None),
    ref_audio: Optional[UploadFile] = File(None),
):
    reg = _registry(request)
    if reg.get(name) is None:
        raise ApiError(404, f"音色「{name}」不存在")
    reg.update_meta(name, description=description, ref_text=ref_text, ref_lang=ref_lang)
    for kind, up in (
        ("gpt", gpt_file),
        ("sovits", sovits_file),
        ("ref_audio", ref_audio),
    ):
        if _has_file(up):
            await _save_upload(reg, name, kind, up)
    return reg.to_dict(reg.get(name))


@router.delete("/voices/{name}", dependencies=[Depends(verify_admin)])
@voice_mutation
async def delete_voice(request: Request, name: str):
    reg = _registry(request)
    try:
        reg.delete(name)
    except VoiceError as e:
        raise ApiError(404, str(e))
    return {"message": f"音色「{name}」已删除"}


@router.post("/voices/{name}/default", dependencies=[Depends(verify_admin)])
async def set_default(request: Request, name: str):
    reg = _registry(request)
    try:
        reg.set_default(name)
    except VoiceError as e:
        raise ApiError(404, str(e))
    return {"message": f"已将「{name}」设为默认音色", "default_voice": name}


@router.get("/voices/{name}/ref-audio", dependencies=[Depends(verify_admin)])
async def get_ref_audio(request: Request, name: str):
    reg = _registry(request)
    v = reg.get(name)
    if v is None:
        raise ApiError(404, f"音色「{name}」不存在")
    p = reg.abs_path(v.ref_audio)
    if not p or not p.is_file():
        raise ApiError(404, f"音色「{name}」还没有参考音频")
    return FileResponse(p)
