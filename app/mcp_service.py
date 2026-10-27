"""Authenticated, stateless Streamable HTTP MCP tools over the same TTS scheduler."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from contextvars import ContextVar
import logging
from urllib.parse import urlsplit

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from .errors import ApiError
from .routes_tts import job_info, me, owned_job, submit, voices
from .schemas import TTSRequest

logger = logging.getLogger("umeko-tts")
current_request: ContextVar[Request] = ContextVar("umeko_mcp_request")
JOB_SCHEMA = {
    "type": "object",
    "properties": {"job_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}},
    "required": ["job_id"],
    "additionalProperties": False,
}
EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class MCPService:
    @asynccontextmanager
    async def run(self, app):
        server = Server(
            "umeko-tts",
            version="2.1.0",
            instructions="Query voices, submit synthesis, poll get_job until completed, then get_audio. Submission returns quickly while the shared user-aware queue performs synthesis. Every HTTP request requires your administrator-issued API Key.",
        )

        @server.list_tools()
        async def list_tools():
            return [
                types.Tool(
                    name="list_voices",
                    description="List available voice names. Pass an exact name as voice when submitting synthesis.",
                    inputSchema=EMPTY_SCHEMA,
                ),
                types.Tool(
                    name="get_my_limits",
                    description="Read your own quotas, rate limits and priority.",
                    inputSchema=EMPTY_SCHEMA,
                ),
                types.Tool(
                    name="submit_synthesis",
                    description="Queue a TTS job and return its job id immediately. Poll get_job; this does not block on model inference.",
                    inputSchema=TTSRequest.model_json_schema(),
                ),
                types.Tool(
                    name="get_job",
                    description="Read your job state, queue position, progress, queue/inference time and audio duration.",
                    inputSchema=JOB_SCHEMA,
                ),
                types.Tool(
                    name="get_audio",
                    description="Return a completed job as MCP audio/wav content. Only your own jobs are accessible; results expire.",
                    inputSchema=JOB_SCHEMA,
                ),
                types.Tool(
                    name="cancel_job",
                    description="Cancel your queued/running job. A running model call retains its slot until it actually stops.",
                    inputSchema=JOB_SCHEMA,
                ),
            ]

        @server.call_tool()
        async def call_tool(name, arguments):
            request = current_request.get()
            try:
                if name == "list_voices":
                    return {"voices": await voices(request)}
                if name == "get_my_limits":
                    return await me(request)
                if name == "submit_synthesis":
                    return job_info(
                        request, submit(request, TTSRequest.model_validate(arguments))
                    )
                if name not in {"get_job", "get_audio", "cancel_job"}:
                    raise ApiError(400, "未知的 MCP 工具")
                job = owned_job(request, arguments["job_id"])
                if name == "get_job":
                    return job_info(request, job)
                if name == "cancel_job":
                    return job_info(
                        request, request.app.state.engine.cancel_job(job.id)
                    )
                if job.state != "completed":
                    raise ApiError(409, job.error or "任务尚未完成，请继续查询 get_job")
                if job.wav is None:
                    raise ApiError(410, "音频结果已过期，请重新提交")
                return [
                    types.AudioContent(
                        type="audio",
                        data=base64.b64encode(job.wav).decode("ascii"),
                        mimeType="audio/wav",
                    )
                ]
            except ApiError as exc:
                return types.CallToolResult(
                    isError=True,
                    content=[types.TextContent(type="text", text=exc.message)],
                    structuredContent={
                        "status": exc.status_code,
                        "message": exc.message,
                    },
                )
            except ValidationError:
                return types.CallToolResult(
                    isError=True,
                    content=[
                        types.TextContent(
                            type="text", text="合成参数无效，请检查文本、音色和数值参数"
                        )
                    ],
                )
            except Exception:
                logger.exception("MCP tool failed: %s", name)
                return types.CallToolResult(
                    isError=True,
                    content=[
                        types.TextContent(
                            type="text", text="服务内部错误，请联系管理员"
                        )
                    ],
                )

        settings = app.state.settings
        public = urlsplit(settings.base_url)
        hosts = [
            "127.0.0.1",
            "localhost",
            "[::1]",
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
        ]
        origins = [
            "http://127.0.0.1",
            "http://localhost",
            "http://[::1]",
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ]
        if public.hostname:
            hosts.extend([public.hostname, public.hostname + ":*"])
            origins.append(f"{public.scheme}://{public.netloc}")
        elif settings.host not in {"0.0.0.0", "::"}:
            hosts.extend([settings.host, settings.host + ":*"])
        origins.extend(o.strip() for o in settings.cors_origins.split(",") if o.strip())
        # A new manager per application lifespan also supports controlled test restarts.
        self.manager = StreamableHTTPSessionManager(
            server,
            stateless=True,
            json_response=True,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=hosts,
                allowed_origins=origins,
            ),
        )
        async with self.manager.run():
            yield

    async def __call__(self, scope, receive, send):
        # Stateless clients can use POST alone; no indefinite GET/SSE connection is needed.
        if scope["method"] != "POST":
            return await JSONResponse(
                {"message": "MCP 使用 Streamable HTTP POST；请在客户端配置此入口"},
                405,
                headers={"Allow": "POST"},
            )(scope, receive, send)
        token = current_request.set(Request(scope, receive))
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            current_request.reset(token)
