"""Final Project MVP 的 loopback-only FastAPI app。"""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, ExceptionHandler, Message, Receive, Scope, Send

from app.api.routes.assets import router as asset_router
from app.api.routes.codex_gateway import router as gateway_router
from app.api.routes.workflows import router as workflow_router
from app.core.gateway_settings import GatewaySettings
from app.core.workflow_settings import WorkflowSettings
from app.schemas.api.codex_gateway import (
    GatewayErrorBody,
    GatewayErrorEnvelope,
)
from app.services.assets import AssetLibraryError, AssetLibraryService
from app.services.codex_gateway.catalog import MockGatewayCatalogProvider
from app.services.codex_gateway.client import (
    CodexAppServerClient,
    CodexGatewayClient,
)
from app.services.codex_gateway.service import (
    CodexGatewayService,
    CodexGatewayServiceError,
)
from app.services.workflows.adapters import StoryboardWorkflowAdapter
from app.services.workflows.client import (
    ComfyUIClient,
    HttpComfyUIClient,
)
from app.services.workflows.service import (
    StoryboardWorkflowService,
    WorkflowServiceError,
)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    envelope = GatewayErrorEnvelope(error=GatewayErrorBody(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
    )


async def _handle_gateway_error(
    request: Request,
    exc: CodexGatewayServiceError,
) -> JSONResponse:
    del request
    return _error_response(exc.status_code, exc.code, exc.message)


async def _handle_workflow_error(
    request: Request,
    exc: WorkflowServiceError,
) -> JSONResponse:
    del request
    return _error_response(exc.status_code, exc.code, exc.message)


async def _handle_asset_library_error(
    request: Request,
    exc: AssetLibraryError,
) -> JSONResponse:
    del request
    return _error_response(exc.status_code, exc.code, exc.message)


async def _handle_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    del request, exc
    return _error_response(
        422,
        "GATEWAY_INVALID_REQUEST",
        "請求內容未通過驗證。",
    )


class _SecurityHeadersMiddleware:
    """加入同源 CSP 與基本瀏覽器安全 headers。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        async def send_with_headers(message: Message) -> None:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.append(
                    "content-security-policy",
                    "default-src 'self'; script-src 'self'; "
                    "style-src 'self'; img-src 'self' data:; "
                    "connect-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'",
                )
                headers.append("x-content-type-options", "nosniff")
                headers.append("referrer-policy", "no-referrer")
                headers.append("x-frame-options", "DENY")
                headers.append("cross-origin-resource-policy", "same-origin")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _RequestBodyTooLarge(RuntimeError):
    """receive wrapper 偵測到 body 超過 workflow hard cap。"""


class _RequestBoundaryMiddleware:
    """即使誤綁公開介面，也只接受 loopback 與同源 mutation。"""

    _MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    _STORYBOARD_CREATE_PATH = "/api/v1/gateway/workflows/storyboards"
    _STORYBOARD_LIBRARY_CREATE_PATH = (
        "/api/v1/gateway/workflows/storyboards/from-library"
    )

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_storyboard_body_bytes: int,
        max_library_body_bytes: int = 64 * 1024,
    ) -> None:
        self.app = app
        self.max_storyboard_body_bytes = max_storyboard_body_bytes
        self.max_library_body_bytes = max_library_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if not self._is_loopback_client(scope.get("client")):
            await self._reject(
                scope,
                receive,
                send,
                status_code=403,
                code="GATEWAY_LOOPBACK_REQUIRED",
                message="本服務只接受本機連線。",
            )
            return

        method = str(scope.get("method", "")).upper()
        if method in self._MUTATION_METHODS and not self._is_same_origin_mutation(
            scope
        ):
            await self._reject(
                scope,
                receive,
                send,
                status_code=403,
                code="GATEWAY_CROSS_SITE_FORBIDDEN",
                message="拒絕跨來源修改請求。",
            )
            return

        path = scope.get("path")
        if method != "POST" or path not in {
            self._STORYBOARD_CREATE_PATH,
            self._STORYBOARD_LIBRARY_CREATE_PATH,
        }:
            await self.app(scope, receive, send)
            return
        max_body_bytes = (
            self.max_storyboard_body_bytes
            if path == self._STORYBOARD_CREATE_PATH
            else self.max_library_body_bytes
        )

        content_lengths = self._header_values(scope, b"content-length")
        if content_lengths:
            if len(content_lengths) != 1:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=400,
                    code="WORKFLOW_INVALID_CONTENT_LENGTH",
                    message="Content-Length 格式不正確。",
                )
                return
            try:
                content_length = int(content_lengths[0])
            except ValueError:
                content_length = -1
            if content_length < 0:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=400,
                    code="WORKFLOW_INVALID_CONTENT_LENGTH",
                    message="Content-Length 格式不正確。",
                )
                return
            if content_length > max_body_bytes:
                await self._reject_too_large(scope, receive, send)
                return

        received_bytes = 0
        response_started = False

        async def bounded_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    received_bytes += len(body)
                if received_bytes > max_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            await self._reject_too_large(scope, receive, send)

    async def _reject_too_large(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        await self._reject(
            scope,
            receive,
            send,
            status_code=413,
            code="WORKFLOW_REQUEST_TOO_LARGE",
            message="工作請求超過安全大小上限。",
        )

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
        message: str,
    ) -> None:
        response = _error_response(status_code, code, message)
        await response(scope, receive, send)

    @classmethod
    def _is_same_origin_mutation(cls, scope: Scope) -> bool:
        if any(
            value.strip().lower() == "cross-site"
            for value in cls._header_values(scope, b"sec-fetch-site")
        ):
            return False
        origins = cls._header_values(scope, b"origin")
        if not origins:
            return True
        hosts = cls._header_values(scope, b"host")
        if len(origins) != 1 or len(hosts) != 1:
            return False
        parsed = urlsplit(origins[0])
        return (
            parsed.scheme == str(scope.get("scheme", "http"))
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and parsed.netloc.casefold() == hosts[0].casefold()
        )

    @staticmethod
    def _is_loopback_client(client: object) -> bool:
        if not isinstance(client, tuple) or not client:
            return False
        host = client[0]
        if host == "testclient":
            return True
        if not isinstance(host, str):
            return False
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _header_values(scope: Scope, name: bytes) -> list[str]:
        return [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.lower() == name
        ]


def create_gateway_app(
    settings: GatewaySettings | None = None,
    *,
    client: CodexGatewayClient | None = None,
    workflow_settings: WorkflowSettings | None = None,
    comfyui_client: ComfyUIClient | None = None,
) -> FastAPI:
    """建立可在 ComfyUI 離線時正常啟動的 loopback Gateway app。"""

    configured_settings = settings or GatewaySettings()
    configured_client = client
    configured_workflow_settings = workflow_settings or WorkflowSettings(
        repo_root=configured_settings.repo_root,
    )
    configured_comfyui_client = comfyui_client
    configured_workflow_adapter = StoryboardWorkflowAdapter(
        configured_workflow_settings
    )
    configured_asset_library = AssetLibraryService(configured_workflow_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await configured_asset_library.start()
        runtime_client = configured_client or CodexAppServerClient(configured_settings)
        runtime_comfyui_client = configured_comfyui_client or HttpComfyUIClient(
            configured_workflow_settings
        )
        workflow_service = StoryboardWorkflowService(
            runtime_comfyui_client,
            configured_workflow_settings,
            adapter=configured_workflow_adapter,
        )
        await workflow_service.start()
        app.state.gateway_service = CodexGatewayService(
            runtime_client,
            MockGatewayCatalogProvider(),
        )
        app.state.workflow_service = workflow_service
        app.state.asset_library_service = configured_asset_library
        try:
            yield
        finally:
            try:
                await workflow_service.close()
            finally:
                await runtime_client.close()

    app = FastAPI(
        title="Final Project MVP",
        description="本機故事視覺工作台與 typed 風格 catalog。",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.add_middleware(
        _RequestBoundaryMiddleware,
        max_storyboard_body_bytes=(
            configured_workflow_settings.max_storyboard_request_bytes
        ),
    )
    app.add_middleware(_SecurityHeadersMiddleware)
    app.add_exception_handler(
        CodexGatewayServiceError,
        cast(ExceptionHandler, _handle_gateway_error),
    )
    app.add_exception_handler(
        WorkflowServiceError,
        cast(ExceptionHandler, _handle_workflow_error),
    )
    app.add_exception_handler(
        AssetLibraryError,
        cast(ExceptionHandler, _handle_asset_library_error),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, _handle_validation_error),
    )
    app.include_router(gateway_router)
    app.include_router(asset_router)
    app.include_router(workflow_router)

    frontend_root = configured_settings.frontend_root
    app.mount(
        "/static/gateway",
        StaticFiles(directory=frontend_root),
        name="gateway-static",
    )

    @app.get("/", response_class=FileResponse, include_in_schema=False)
    async def gateway_index() -> FileResponse:
        """提供三工作區與角色風格櫥窗單頁 UI。"""

        return FileResponse(
            Path(frontend_root) / "index.html",
            media_type="text/html",
        )

    return app


app = create_gateway_app()
