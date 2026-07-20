"""Codex Gateway V2 的 strict HTTP DTO。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class GatewayStrictModel(BaseModel):
    """拒絕未知欄位的 Gateway DTO 基底。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class WorkspaceKind(StrEnum):
    """Web 工作室支援的三個內容分類。"""

    CHARACTER = "character"
    SCENE = "scene"
    STORYBOARD = "storyboard"


class CatalogItemStatus(StrEnum):
    """展示卡目前是 placeholder 或之後接入的正式素材。"""

    PLACEHOLDER = "placeholder"
    READY = "ready"


CatalogTag = Annotated[str, Field(min_length=1, max_length=64)]
ReferenceId = Annotated[str, Field(min_length=1, max_length=256)]


class CatalogItem(GatewayStrictModel):
    """可由 mock 或後續正式資料 provider 提供的展示項目。"""

    item_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    status: CatalogItemStatus = CatalogItemStatus.PLACEHOLDER
    preview_kind: str = Field(min_length=1, max_length=64)
    preview_url: str | None = Field(default=None, max_length=2048)
    tags: tuple[CatalogTag, ...] = Field(default=(), max_length=12)

    @field_validator("preview_url")
    @classmethod
    def validate_preview_url(cls, value: str | None) -> str | None:
        """正式預覽只允許同源相對路徑，不開放任意外站或本機檔案 URL。"""

        if value is None:
            return None
        parsed = urlsplit(value)
        decoded_segments = unquote(parsed.path).split("/")
        if (
            parsed.scheme
            or parsed.netloc
            or not parsed.path.startswith("/")
            or value.startswith("//")
            or "\\" in value
            or any(segment in {".", ".."} for segment in decoded_segments)
        ):
            raise ValueError("preview_url 必須是安全的同源絕對路徑")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """標籤必須非空白且不得重複。"""

        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("catalog tags 不可包含空白值")
        if len(set(normalized)) != len(normalized):
            raise ValueError("catalog tags 不可重複")
        return normalized


class CharacterStyleItem(CatalogItem):
    """角色風格卡與實際生成提示詞的一對一契約。"""

    prompt_fragment: str = Field(min_length=1, max_length=1_200)


class GatewayCatalog(GatewayStrictModel):
    """三工作區的可替換展示 catalog。"""

    schema_version: Literal["storyboard-studio.catalog.v2"]
    character_styles: tuple[CharacterStyleItem, ...]
    scene_showcase: tuple[CatalogItem, ...]
    storyboard_showcase: tuple[CatalogItem, ...]


class WorkspaceContext(GatewayStrictModel):
    """隨 turn 傳給 Codex 的受控 UI context。"""

    selected_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_draft: str | None = Field(default=None, max_length=12_000)
    reference_ids: tuple[ReferenceId, ...] = Field(default=(), max_length=20)

    @field_validator("reference_ids")
    @classmethod
    def validate_reference_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """reference ID 視為 opaque，但必須非空白且唯一。"""

        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("reference_ids 不可包含空白值")
        if len(set(normalized)) != len(normalized):
            raise ValueError("reference_ids 不可重複")
        return normalized


class GatewayStatusResponse(GatewayStrictModel):
    """不啟動 child process 即可讀取的 Gateway 安全狀態。"""

    status: Literal["ready", "unavailable"]
    available: bool
    connected: bool
    codex_binary: str
    sandbox: Literal["read-only"] = "read-only"
    approval_policy: Literal["never"] = "never"
    detail: str


class GatewayThreadCreateRequest(GatewayStrictModel):
    """建立 Codex thread 時保存目前工作區。"""

    workspace: WorkspaceKind
    context: WorkspaceContext = Field(default_factory=WorkspaceContext)


class GatewayThreadResponse(GatewayStrictModel):
    """只回傳 Codex 核發的 opaque thread ID。"""

    thread_id: str = Field(min_length=1)
    workspace: WorkspaceKind
    status: Literal["ready"] = "ready"


class GatewayTurnRequest(GatewayStrictModel):
    """同一 thread 可隨 UI tab 切換工作區。"""

    message: str = Field(min_length=1, max_length=12_000)
    workspace: WorkspaceKind
    context: WorkspaceContext = Field(default_factory=WorkspaceContext)

    @model_validator(mode="after")
    def reject_blank_message(self) -> GatewayTurnRequest:
        """Pydantic 的 min_length 不會排除全空白字串。"""

        if not self.message.strip():
            raise ValueError("message 不可只有空白")
        return self


class GatewayTurnResponse(GatewayStrictModel):
    """單一 Codex turn 的最終文字回覆。"""

    thread_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    response: str = Field(min_length=1)
    status: Literal["completed"] = "completed"


class GatewayErrorBody(GatewayStrictModel):
    """Gateway 對外安全錯誤內容。"""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class GatewayErrorEnvelope(GatewayStrictModel):
    """統一錯誤 envelope。"""

    error: GatewayErrorBody
