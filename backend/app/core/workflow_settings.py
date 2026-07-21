"""Storyboard workflow 與 loopback ComfyUI 的隔離設定。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.gateway_settings import DEFAULT_GATEWAY_REPO_ROOT


class WorkflowSettings(BaseSettings):
    """載入固定 workflow、圖片限制與 loopback ComfyUI 設定。"""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_GATEWAY_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="STORYBOARD_WORKFLOW_",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    repo_root: Path = Field(default=DEFAULT_GATEWAY_REPO_ROOT)
    workflow_root: Path = Field(default=Path("docs/workflows"))
    asset_library_root: Path = Field(default=Path(".local-data/asset-library"))
    comfyui_base_url: str = Field(default="http://127.0.0.1:8188", max_length=256)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    prompt_timeout_seconds: float = Field(default=1_800.0, gt=0, le=3_600)
    poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=5)
    max_upload_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )
    max_output_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1024,
        le=200 * 1024 * 1024,
    )
    max_image_dimension: int = Field(default=8_192, ge=64, le=16_384)
    max_image_pixels: int = Field(default=40_000_000, ge=4_096, le=100_000_000)
    multipart_overhead_bytes: int = Field(
        default=256 * 1024,
        ge=1024,
        le=2 * 1024 * 1024,
    )
    max_queue_size: int = Field(default=8, ge=1, le=64)
    max_runs: int = Field(default=64, ge=1, le=1_000)
    max_retained_image_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024,
        le=4 * 1024 * 1024 * 1024,
    )

    @field_validator("comfyui_base_url")
    @classmethod
    def validate_loopback_comfyui_url(cls, value: str) -> str:
        """只接受不含憑證、query 或額外 path 的 loopback HTTP URL。"""

        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port is None
        ):
            raise ValueError("ComfyUI URL 必須是含 port 的 loopback HTTP URL")
        return normalized

    @model_validator(mode="after")
    def normalize_paths(self) -> WorkflowSettings:
        """固定 workflow 位於 repo；素材只能位於專用 local-data 樹。"""

        self.repo_root = self.repo_root.expanduser().resolve()
        workflow_root = self.workflow_root.expanduser()
        if not workflow_root.is_absolute():
            workflow_root = self.repo_root / workflow_root
        self.workflow_root = workflow_root.resolve()
        if not self.workflow_root.is_relative_to(self.repo_root):
            raise ValueError("workflow_root 必須位於專案根目錄內")

        local_data_root = (self.repo_root / ".local-data").resolve()
        if not local_data_root.is_relative_to(self.repo_root):
            raise ValueError(".local-data 不可指向專案外部")
        asset_library_root = self.asset_library_root.expanduser()
        if not asset_library_root.is_absolute():
            asset_library_root = self.repo_root / asset_library_root
        self.asset_library_root = asset_library_root.resolve()
        if not self.asset_library_root.is_relative_to(local_data_root):
            raise ValueError("asset_library_root 必須位於專案 .local-data 內")
        return self

    @property
    def max_storyboard_request_bytes(self) -> int:
        """multipart 兩張圖加 typed JSON／boundary 的總 body 上限。"""

        return (2 * self.max_upload_bytes) + self.multipart_overhead_bytes
