"""Codex Gateway V2 的隔離設定。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_GATEWAY_REPO_ROOT = Path(__file__).resolve().parents[3]


class GatewaySettings(BaseSettings):
    """載入本機 Codex child process 與 Gateway UI 設定。

    Gateway 不讀取 ComfyUI、資料庫或產品 Planner 設定。所有可供瀏覽器
    使用的執行權限在 server 端固定為唯讀，client 不可覆寫。
    """

    model_config = SettingsConfigDict(
        env_file=DEFAULT_GATEWAY_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="CODEX_GATEWAY_",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    repo_root: Path = Field(default=DEFAULT_GATEWAY_REPO_ROOT)
    frontend_root: Path = Field(default=Path("frontend/gateway"))
    codex_cwd: Path = Field(default=Path("."))
    codex_binary: str = Field(default="codex", min_length=1, max_length=1024)
    codex_model: str | None = Field(default=None, min_length=1, max_length=128)
    thread_ephemeral: bool = Field(default=True)
    startup_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    turn_timeout_seconds: float = Field(default=900.0, gt=0, le=3600)
    shutdown_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    protocol_line_limit_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=64 * 1024,
        le=32 * 1024 * 1024,
    )

    @field_validator("codex_binary")
    @classmethod
    def validate_codex_binary(cls, value: str) -> str:
        """拒絕空白與 NUL，並保留可由 PATH 解析的 executable 名稱。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("CODEX_GATEWAY_CODEX_BINARY 不可為空白")
        if "\x00" in normalized:
            raise ValueError("CODEX_GATEWAY_CODEX_BINARY 不可包含 NUL")
        return normalized

    @field_validator("codex_model", mode="before")
    @classmethod
    def normalize_optional_model(cls, value: object) -> object:
        """空白 model 視為未設定，避免程式自行猜測 model ID。"""

        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def normalize_project_paths(self) -> GatewaySettings:
        """正規化並限制 UI／Codex cwd 在專案根目錄內。"""

        self.repo_root = self.repo_root.expanduser().resolve()
        self.frontend_root = self._resolve_project_path(self.frontend_root)
        self.codex_cwd = self._resolve_project_path(self.codex_cwd)
        return self

    def _resolve_project_path(self, path: Path) -> Path:
        expanded = path.expanduser()
        if not expanded.is_absolute():
            expanded = self.repo_root / expanded
        resolved = expanded.resolve()
        if not resolved.is_relative_to(self.repo_root):
            raise ValueError(f"Gateway 路徑必須位於專案根目錄內：{resolved.name}")
        return resolved
