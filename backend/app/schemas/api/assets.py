"""本機角色與場景素材庫的 strict DTO。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.api.codex_gateway import GatewayStrictModel


class CharacterView(StrEnum):
    """角色包 MVP 固定的四視圖。"""

    FRONT = "front"
    LEFT = "left"
    RIGHT = "right"
    BACK = "back"


class _AssetMetadataBase(GatewayStrictModel):
    """儲存於本機的共用 metadata 契約。"""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1_000)
    created_at: datetime

    @field_validator("name", "description")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("素材名稱與描述不可只有空白")
        return value

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_at 必須是 UTC 時間")
        return value.astimezone(UTC)


class CharacterAssetMetadata(_AssetMetadataBase):
    """磁碟上的角色素材 metadata。"""

    schema_version: Literal["storyboard-studio.character-asset.v1"]
    kind: Literal["character"]
    asset_id: str = Field(pattern=r"^char_[0-9a-f]{32}$")


class SceneAssetMetadata(_AssetMetadataBase):
    """磁碟上的場景素材 metadata。"""

    schema_version: Literal["storyboard-studio.scene-asset.v1"]
    kind: Literal["scene"]
    asset_id: str = Field(pattern=r"^scene_[0-9a-f]{32}$")


class CharacterAssetViewUrls(GatewayStrictModel):
    """角色四視圖的安全同源 URL。"""

    front: str = Field(pattern=r"^/api/v1/gateway/assets/")
    left: str = Field(pattern=r"^/api/v1/gateway/assets/")
    right: str = Field(pattern=r"^/api/v1/gateway/assets/")
    back: str = Field(pattern=r"^/api/v1/gateway/assets/")


class CharacterAssetResponse(_AssetMetadataBase):
    """給 browser 的角色素材摘要。"""

    asset_id: str = Field(pattern=r"^char_[0-9a-f]{32}$")
    views: CharacterAssetViewUrls


class SceneAssetResponse(_AssetMetadataBase):
    """給 browser 的場景素材摘要。"""

    asset_id: str = Field(pattern=r"^scene_[0-9a-f]{32}$")
    image_url: str = Field(pattern=r"^/api/v1/gateway/assets/")


class AssetLibraryResponse(GatewayStrictModel):
    """完整本機素材庫快照。"""

    characters: tuple[CharacterAssetResponse, ...]
    scenes: tuple[SceneAssetResponse, ...]
