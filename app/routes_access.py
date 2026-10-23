"""Admin login, trusted users, key issuance, scheduler controls and audit metadata."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .access import digest
from .errors import ApiError
from .security import check_origin, cookie_name, csrf_token, verify_admin

router = APIRouter(prefix="/api")


class Login(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class UserPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=10)
    max_concurrent: int = Field(default=1, ge=1, le=32)
    max_queue: int = Field(default=8, ge=1, le=100)
    rpm: int = Field(default=30, ge=1, le=600)
    daily_chars: int = Field(default=100000, ge=0, le=100000000)
    max_text_chars: int = Field(default=1000, ge=1, le=10000)

    @field_validator("name")
    @classmethod
    def name_clean(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("用户名称不能为空或包含控制字符")
        return value


class KeyIssue(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    label: str = Field(default="服务调用", min_length=1, max_length=64)
    expires_days: int = Field(default=30, ge=0, le=3650)


class QueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concurrency: int = Field(ge=1, le=32)
    max_queue: int = Field(ge=1, le=1000)
    queue_timeout: int = Field(ge=1, le=3600)
    aging_seconds: int = Field(ge=1, le=600)


@router.get("/auth/session")
async def session(request: Request):
    settings = request.app.state.settings
    raw = request.cookies.get(cookie_name(settings), "")
    valid = bool(
        settings.admin_token
        and request.app.state.access.valid_session(raw, digest(settings.admin_token))
    )
    return {
        "authenticated": valid,
        "configured": bool(settings.admin_token),
        "csrf_token": csrf_token(settings, raw) if valid else "",
        "base_url": settings.base_url,
        "base_path": settings.base_path,
    }


@router.post("/auth/login")
async def login(request: Request, body: Login, response: Response):
    st = request.app.state
    if not st.settings.admin_token:
        raise ApiError(503, "请先配置至少 24 字符的 TTS_ADMIN_TOKEN 后重启服务")
    check_origin(request)
    st.access.login_attempt(request.client.host if request.client else "unknown")
    if not hmac.compare_digest(body.token.encode(), st.settings.admin_token.encode()):
        raise ApiError(401, "管理员令牌不正确")
    raw = st.access.create_session(
        digest(st.settings.admin_token), st.settings.session_seconds
    )
    response.set_cookie(
        cookie_name(st.settings),
        raw,
        max_age=st.settings.session_seconds,
        httponly=True,
        secure=st.settings.secure_cookie or request.url.scheme == "https",
        samesite="strict",
        path=(st.settings.base_path or "") + "/",
    )
    return {"authenticated": True, "csrf_token": csrf_token(st.settings, raw)}


@router.post("/auth/logout", dependencies=[Depends(verify_admin)])
async def logout(request: Request, response: Response):
    settings = request.app.state.settings
    request.app.state.access.delete_session(
        request.cookies.get(cookie_name(settings), "")
    )
    response.delete_cookie(
        cookie_name(settings),
        path=(settings.base_path or "") + "/",
        secure=settings.secure_cookie or request.url.scheme == "https",
        httponly=True,
        samesite="strict",
    )
    return {"message": "已退出登录"}


@router.get("/users", dependencies=[Depends(verify_admin)])
async def users(request: Request):
    return request.app.state.access.users()


@router.post("/users", dependencies=[Depends(verify_admin)])
async def create_user(request: Request, body: UserPolicy):
    return request.app.state.access.save_user(body.model_dump())


@router.put("/users/{user_id}", dependencies=[Depends(verify_admin)])
async def update_user(request: Request, user_id: str, body: UserPolicy):
    st = request.app.state
    result = st.access.save_user(body.model_dump(), user_id)
    if not body.enabled:
        st.engine.invalidate(user_id=user_id)
    st.engine._event.set()
    return result


@router.get("/keys", dependencies=[Depends(verify_admin)])
async def keys(request: Request):
    return request.app.state.access.keys()


@router.post("/keys", dependencies=[Depends(verify_admin)])
async def issue_key(request: Request, body: KeyIssue):
    return request.app.state.access.issue_key(
        body.user_id, body.label, body.expires_days
    )


@router.delete("/keys/{key_id}", dependencies=[Depends(verify_admin)])
async def revoke_key(request: Request, key_id: str):
    st = request.app.state
    st.access.revoke_key(key_id)
    st.engine.invalidate(key_id=key_id)
    return {"message": "密钥已撤销，排队任务已取消"}


@router.post("/keys/{key_id}/rotate", dependencies=[Depends(verify_admin)])
async def rotate_key(request: Request, key_id: str):
    st = request.app.state
    result = st.access.rotate_key(key_id)
    st.engine.invalidate(key_id=key_id)
    return result


@router.get("/queue", dependencies=[Depends(verify_admin)])
async def queue(request: Request):
    return {
        "jobs": request.app.state.engine.queue(),
        "history": request.app.state.access.request_history(),
    }


@router.delete("/queue/{job_id}", dependencies=[Depends(verify_admin)])
async def cancel_job(request: Request, job_id: str):
    engine = request.app.state.engine
    return engine.job_info(engine.cancel_job(job_id), admin=True)


@router.get("/requests/{request_id}", dependencies=[Depends(verify_admin)])
async def request_detail(request: Request, request_id: str):
    return request.app.state.engine.request_detail(request_id)


@router.put("/settings", dependencies=[Depends(verify_admin)])
async def settings(request: Request, body: QueueConfig):
    request.app.state.engine.configure(body.model_dump())
    return request.app.state.engine.config


@router.get("/audit", dependencies=[Depends(verify_admin)])
async def audit(request: Request):
    return request.app.state.access.audit()
