"""隔離 Codex Gateway 的 HTTP routes。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.schemas.api.codex_gateway import (
    GatewayCatalog,
    GatewayStatusResponse,
    GatewayThreadCreateRequest,
    GatewayThreadResponse,
    GatewayTurnRequest,
    GatewayTurnResponse,
)
from app.services.codex_gateway.service import CodexGatewayService

router = APIRouter(prefix="/api/v1/gateway", tags=["Storyboard Gateway"])


def get_gateway_service(request: Request) -> CodexGatewayService:
    """從獨立 gateway lifespan 取得 application service。"""

    return request.app.state.gateway_service


GatewayServiceDependency = Annotated[
    CodexGatewayService,
    Depends(get_gateway_service),
]


@router.get("/status", response_model=GatewayStatusResponse)
async def get_status(service: GatewayServiceDependency) -> GatewayStatusResponse:
    """查詢 Codex binary 與 child process 狀態。"""

    return await service.status()


@router.get("/catalog", response_model=GatewayCatalog)
async def get_catalog(service: GatewayServiceDependency) -> GatewayCatalog:
    """取得三工作區的 placeholder catalog。"""

    return service.catalog()


@router.post(
    "/threads",
    response_model=GatewayThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    payload: GatewayThreadCreateRequest,
    service: GatewayServiceDependency,
) -> GatewayThreadResponse:
    """建立一條 Codex thread。"""

    return await service.create_thread(payload)


@router.post(
    "/threads/{thread_id}/turns",
    response_model=GatewayTurnResponse,
)
async def create_turn(
    thread_id: str,
    payload: GatewayTurnRequest,
    service: GatewayServiceDependency,
) -> GatewayTurnResponse:
    """在既有 Gateway thread 完成一輪 Codex 對話。"""

    return await service.run_turn(thread_id, payload)
