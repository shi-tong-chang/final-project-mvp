"""Storyboard 合成與 4K 放大的 strict HTTP DTO。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.schemas.api.codex_gateway import GatewayStrictModel


class WorkflowRunStatus(StrEnum):
    """分鏡工作流的公開生命週期。"""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_SELECTION = "awaiting_selection"
    UPSCALING = "upscaling"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowCandidateStatus(StrEnum):
    """單張分鏡候選的生命週期。"""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowUpscaleStatus(StrEnum):
    """4K 子工作的生命週期。"""

    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowRoute(StrEnum):
    """由 server 依角色數量選定的固定 workflow 路由。"""

    SINGLE_CHARACTER_B1 = "single_character_b1"
    DUAL_CHARACTER_B1_B2 = "dual_character_b1_b2"


class StoryboardCreateSpec(GatewayStrictModel):
    """multipart `request` 欄位內的 typed JSON。"""

    prompt: str = Field(min_length=1, max_length=12_000)
    candidate_count: int = Field(ge=1, le=3)

    @field_validator("prompt")
    @classmethod
    def reject_blank_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt 不可只有空白")
        return value


CharacterAssetId = Annotated[str, Field(pattern=r"^char_[0-9a-f]{32}$")]


class StoryboardFromLibraryCreateSpec(StoryboardCreateSpec):
    """只允許以本機素材庫 opaque ID 建立分鏡。"""

    character_asset_ids: tuple[CharacterAssetId, ...] = Field(
        min_length=1,
        max_length=2,
    )
    scene_asset_id: str = Field(pattern=r"^scene_[0-9a-f]{32}$")

    @field_validator("character_asset_ids")
    @classmethod
    def validate_character_asset_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("character_asset_ids 不可重複")
        return values


class StoryboardSelectionRequest(GatewayStrictModel):
    """使用 opaque candidate ID 選定分鏡。"""

    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")


class StoryboardUpscaleRequest(GatewayStrictModel):
    """以 optimistic candidate assertion 建立 4K 工作。"""

    refine_prompt: str = Field(min_length=1, max_length=12_000)
    expected_candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")

    @field_validator("refine_prompt")
    @classmethod
    def reject_blank_refine_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("refine_prompt 不可只有空白")
        return value


class WorkflowStageSeeds(GatewayStrictModel):
    """同一候選在固定 B1／B2 階段使用的 server seed。"""

    b1: int = Field(ge=0, le=9_007_199_254_740_991)
    b2: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)


class WorkflowCandidateResponse(GatewayStrictModel):
    """單一候選的安全狀態與同源資產 URL。"""

    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    seed: int = Field(ge=0, le=9_007_199_254_740_991)
    stage_seeds: WorkflowStageSeeds
    status: WorkflowCandidateStatus
    image_url: str | None = None
    download_url: str | None = None
    error: str | None = Field(default=None, max_length=500)


class WorkflowUpscaleResponse(GatewayStrictModel):
    """選定候選的 4K 子工作。"""

    status: WorkflowUpscaleStatus
    image_url: str | None = None
    download_url: str | None = None
    error: str | None = Field(default=None, max_length=500)


class StoryboardRunResponse(GatewayStrictModel):
    """分鏡合成、選片與放大的完整公開快照。"""

    run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    status: WorkflowRunStatus
    workflow_route: WorkflowRoute
    candidates: tuple[WorkflowCandidateResponse, ...]
    selected_candidate_id: str | None = Field(
        default=None,
        pattern=r"^cand_[0-9a-f]{32}$",
    )
    upscale: WorkflowUpscaleResponse


class WorkflowStatusResponse(GatewayStrictModel):
    """不洩漏本機路徑的 ComfyUI 能力狀態。"""

    status: Literal["ready", "unavailable"]
    available: bool
    detail: str = Field(min_length=1, max_length=500)
