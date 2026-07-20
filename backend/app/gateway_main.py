"""Final Project MVP 的 loopback-only FastAPI app。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, ExceptionHandler, Message, Receive, Scope, Send

from app.api.routes.codex_gateway import router as gateway_router
from app.core.gateway_settings import GatewaySettings
from app.schemas.api.codex_gateway import (
    GatewayErrorBody,
    GatewayErrorEnvelope,
)
from app.services.codex_gateway.catalog import MockGatewayCatalogProvider
from app.services.codex_gateway.client import (
    CodexAppServerClient,
    CodexGatewayClient,
)
from app.services.codex_gateway.service import (
    CodexGatewayService,
    CodexGatewayServiceError,
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
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_gateway_app(
    settings: GatewaySettings | None = None,
    *,
    client: CodexGatewayClient | None = None,
) -> FastAPI:
    """建立不依賴 DB、GPU、ComfyUI 或產品 Planner 的 Gateway app。"""

    configured_settings = settings or GatewaySettings()
    configured_client = client

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime_client = configured_client or CodexAppServerClient(configured_settings)
        app.state.gateway_service = CodexGatewayService(
            runtime_client,
            MockGatewayCatalogProvider(),
        )
        try:
            yield
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
    app.add_middleware(_SecurityHeadersMiddleware)
    app.add_exception_handler(
        CodexGatewayServiceError,
        cast(ExceptionHandler, _handle_gateway_error),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, _handle_validation_error),
    )
    app.include_router(gateway_router)

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
