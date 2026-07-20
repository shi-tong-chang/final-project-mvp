"""WSL/Linux clone-to-run 的 managed/adopted runtime manager。"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import time
import tomllib
import uuid
import webbrowser
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from runtime.bootstrap import BootstrapError, PinnedUvBootstrap
from runtime.downloads import DownloadError, ModelDownloader
from runtime.spec import ModelLock, RuntimeLock, ToolLock, load_runtime_lock

_CONFIG_SCHEMA = 1
_PROCESS_SCHEMA = 2
_MANAGED_MARKER = "fpmvp-managed.json"
_GATEWAY_PACKAGE_NAMES = (
    "fastapi",
    "httpx",
    "pillow",
    "pydantic",
    "pydantic-settings",
    "python-multipart",
    "starlette",
    "uvicorn",
)
_MODEL_SEARCH_SUBDIRS: dict[str, tuple[str, ...]] = {
    "unet": ("unet", "diffusion_models"),
    "diffusion_models": ("unet", "diffusion_models"),
    "clip": ("text_encoders", "clip"),
    "vae": ("vae",),
    "upscale_models": ("upscale_models",),
}
_SELECTED_MODEL_SUBDIRS: dict[str, tuple[str, ...]] = {
    "unet": ("unet", "diffusion_models"),
    "diffusion_models": ("unet", "diffusion_models"),
    "clip": ("clip",),
    "vae": ("vae",),
    "upscale_models": ("upscale_models",),
}


class RuntimeMode(StrEnum):
    """ComfyUI source ownership。"""

    AUTO = "auto"
    MANAGED = "managed"
    ADOPTED = "adopted"


class ModelsMode(StrEnum):
    """模型檔案 ownership。"""

    AUTO = "auto"
    MANAGED = "managed"
    EXTERNAL = "external"


class RuntimeOperationError(RuntimeError):
    """CLI 可安全顯示的 runtime 操作錯誤。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CommandResult:
    """subprocess 執行結果。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    """可由 unit test spy 取代的 subprocess 邊界。"""

    def which(self, name: str) -> str | None:
        """尋找固定工具。"""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """以 shell=False 執行固定 argv。"""

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_path: Path,
    ) -> int:
        """啟動 runtime-owned process group 並回傳 PID。"""


class UvBootstrap(Protocol):
    """可由 unit test fake 取代的 pinned uv bootstrap。"""

    def ensure(self) -> Path:
        """回傳已驗證的 uv executable。"""


class SubprocessCommandRunner:
    """正式 subprocess 邊界；不接受 shell command 字串。"""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                env=dict(env) if env is not None else None,
            )
        except FileNotFoundError as exc:
            raise RuntimeOperationError(
                "COMMAND_NOT_FOUND",
                f"找不到本機命令：{Path(argv[0]).name}。",
            ) from exc
        except PermissionError as exc:
            raise RuntimeOperationError(
                "COMMAND_PERMISSION_DENIED",
                f"沒有權限執行本機命令：{Path(argv[0]).name}。",
            ) from exc
        except OSError as exc:
            raise RuntimeOperationError(
                "COMMAND_EXEC_FAILED",
                f"無法安全執行本機命令：{Path(argv[0]).name}。",
            ) from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_path: Path,
    ) -> int:
        log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            log_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "ab", closefd=True) as log:
                process = subprocess.Popen(
                    list(argv),
                    cwd=cwd,
                    env=dict(env),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=True,
                )
        except FileNotFoundError as exc:
            raise RuntimeOperationError(
                "COMMAND_NOT_FOUND",
                f"找不到服務命令：{Path(argv[0]).name}。",
            ) from exc
        except PermissionError as exc:
            raise RuntimeOperationError(
                "COMMAND_PERMISSION_DENIED",
                f"沒有權限啟動服務命令：{Path(argv[0]).name}。",
            ) from exc
        except OSError as exc:
            raise RuntimeOperationError(
                "PROCESS_SPAWN_FAILED",
                f"無法安全啟動服務：{Path(argv[0]).name}。",
            ) from exc
        return process.pid


@dataclass(frozen=True, slots=True)
class RuntimeCheck:
    """穩定 ID 的 preflight/status check。"""

    check_id: str
    status: str
    code: str
    message: str

    @property
    def passed(self) -> bool:
        """只有 fail 會阻擋 ready。"""

        return self.status != "fail"


@dataclass(frozen=True, slots=True)
class RuntimeAction:
    """dry-run 與正式執行共用的可稽核 action。"""

    action_id: str
    status: str
    argv: tuple[str, ...] = ()
    cwd: str | None = None
    detail: str | None = None


@dataclass(slots=True)
class RuntimeReport:
    """五個 CLI command 的共同輸出 schema。"""

    command: str
    dry_run: bool
    ok: bool
    overall: str
    changed: bool = False
    mode: str | None = None
    models_mode: str | None = None
    urls: dict[str, str] = field(default_factory=dict)
    checks: list[RuntimeCheck] = field(default_factory=list)
    actions: list[RuntimeAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    agents: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """輸出 deterministic JSON-ready payload。"""

        return {
            "schema": "fpmvp.runtime.v1",
            "command": self.command,
            "ok": self.ok,
            "changed": self.changed,
            "dry_run": self.dry_run,
            "overall": self.overall,
            "mode": self.mode,
            "models_mode": self.models_mode,
            "urls": dict(sorted(self.urls.items())),
            "checks": [asdict(item) for item in self.checks],
            "actions": [asdict(item) for item in self.actions],
            "warnings": self.warnings,
            "agents": self.agents,
        }


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """machine-local paths；只寫進 ignored runtime state。"""

    instance_id: str
    project_root: Path
    comfy_mode: RuntimeMode
    models_mode: ModelsMode
    comfyui_root: Path
    comfyui_python: Path
    model_root: Path
    gateway_port: int
    comfyui_port: int

    def to_dict(self) -> dict[str, object]:
        """序列化 config，不含秘密。"""

        return {
            "schema_version": _CONFIG_SCHEMA,
            "instance_id": self.instance_id,
            "project_root": str(self.project_root),
            "comfy_mode": self.comfy_mode.value,
            "models_mode": self.models_mode.value,
            "comfyui_root": str(self.comfyui_root),
            "comfyui_python": str(self.comfyui_python),
            "model_root": str(self.model_root),
            "gateway_port": self.gateway_port,
            "comfyui_port": self.comfyui_port,
        }


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    """避免 stale PID/PID reuse 誤殺的 Linux process identity。"""

    name: str
    pid: int
    pgid: int
    start_ticks: str
    boot_id: str
    executable: str
    argv_sha256: str
    comfy_enabled: bool = False


class RuntimeManager:
    """不依賴 FastAPI/Pydantic 的 clone-to-run orchestration。"""

    def __init__(
        self,
        project_root: Path,
        *,
        state_dir: Path | None = None,
        runtime_lock: RuntimeLock | None = None,
        runner: CommandRunner | None = None,
        environ: Mapping[str, str] | None = None,
        downloader_factory: Callable[[Path], ModelDownloader] | None = None,
        uv_bootstrap_factory: Callable[[Path, ToolLock], UvBootstrap] | None = None,
        browser_opener: Callable[[str], bool] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.state_dir = (
            state_dir.expanduser().resolve()
            if state_dir is not None
            else self.project_root / ".runtime"
        )
        self.lock = runtime_lock or load_runtime_lock()
        self.runner = runner or SubprocessCommandRunner()
        self.environ = dict(environ if environ is not None else os.environ)
        self.downloader_factory = downloader_factory or ModelDownloader
        self.uv_bootstrap_factory = uv_bootstrap_factory or PinnedUvBootstrap
        self.browser_opener = browser_opener or webbrowser.open
        self.progress = progress
        self.config_path = self.state_dir / "config.json"
        self.process_path = self.state_dir / "state/processes.json"
        self.model_receipt_path = self.state_dir / "state/models.receipt.json"
        self.lock_path = self.state_dir / "runtime.lock"
        self.state_marker_path = self.state_dir / ".fpmvp-runtime-root.json"
        self.extra_models_path = self.state_dir / "extra_model_paths.yaml"
        self.managed_comfy_root = self.state_dir / "comfyui"
        self.managed_model_root = self.state_dir / "models"
        self.data_root = self.state_dir / "comfy-data"

    def install(
        self,
        *,
        comfy_mode: RuntimeMode = RuntimeMode.AUTO,
        models_mode: ModelsMode = ModelsMode.AUTO,
        comfyui_root: Path | None = None,
        comfyui_python: Path | None = None,
        model_root: Path | None = None,
        dry_run: bool = False,
    ) -> RuntimeReport:
        """安裝兩個隔離 Python 環境；不啟動任何服務或 GPU。"""

        planned_comfy_mode = (
            RuntimeMode.MANAGED if comfy_mode is RuntimeMode.AUTO else comfy_mode
        )
        planned_comfy_root = (
            self.managed_comfy_root
            if planned_comfy_mode is RuntimeMode.MANAGED
            else self._require_path_argument(comfyui_root, "adopted ComfyUI root")
        )
        planned_comfy_python = (
            self._safe_cli_path(str(comfyui_python), "ComfyUI Python")
            if comfyui_python is not None
            else self._default_comfy_python(
                planned_comfy_root,
                planned_comfy_mode,
            )
        )
        if models_mode is ModelsMode.EXTERNAL and model_root is None:
            raise RuntimeOperationError(
                "PATH_REQUIRED",
                "external models mode 必須明確提供 model root。",
            )
        planned_model_root = (
            self._safe_cli_path(str(model_root), "model root")
            if model_root is not None
            else self.managed_model_root
        )
        report = self._report(
            "install",
            dry_run=dry_run,
            mode=planned_comfy_mode,
            models_mode=models_mode,
        )
        report.actions.extend(
            self._install_plan(
                planned_comfy_mode,
                models_mode,
                planned_comfy_root,
                planned_comfy_python,
                planned_model_root,
            )
        )
        if dry_run:
            report.overall = "planned"
            report.warnings.append(
                "dry-run 未寫檔、未下載模型、未執行 subprocess、未啟動服務。"
            )
            return report

        self._require_supported_platform()
        self._require_runtime_paths_ignored()
        self._require_managed_paths_safe()
        with self._exclusive_lock():
            self._prepare_state_dirs()
            self._require_install_stopped()
            uv_binary = self._ensure_uv()
            self._progress(f"準備 Gateway Python {self.lock.gateway.python}…")
            self._run_checked(
                [
                    uv_binary,
                    "python",
                    "install",
                    "--install-dir",
                    str(self.state_dir / "python"),
                    "--no-bin",
                    self.lock.gateway.python,
                ],
                cwd=self.project_root,
            )
            gateway_python = self._managed_python_executable(self.lock.gateway.python)
            if not gateway_python.is_file():
                raise RuntimeOperationError(
                    "PYTHON_INSTALL_FAILED",
                    "uv 未在 project-local install-dir 建立 Gateway Python。",
                )
            self._progress("同步 Gateway locked dependencies…")
            self._run_checked(
                [
                    uv_binary,
                    "sync",
                    "--locked",
                    "--dev",
                    "--python",
                    str(gateway_python),
                ],
                cwd=self.project_root,
            )

            if planned_comfy_mode is RuntimeMode.MANAGED:
                self._progress("準備 managed ComfyUI source 與獨立 venv…")
                self._install_managed_comfy(uv_binary)
                resolved_comfy_root = self.managed_comfy_root
                resolved_comfy_python = self._default_comfy_python(
                    resolved_comfy_root,
                    RuntimeMode.MANAGED,
                )
            else:
                self._progress("唯讀驗證 adopted ComfyUI…")
                resolved_comfy_root = planned_comfy_root
                resolved_comfy_python = planned_comfy_python
                self._validate_adopted_comfy(
                    resolved_comfy_root,
                    resolved_comfy_python,
                )
            self._reject_source_extra_model_paths(resolved_comfy_root)

            resolved_models_mode, resolved_model_root = self._resolve_models(
                models_mode=models_mode,
                requested_root=model_root,
                comfyui_root=resolved_comfy_root,
            )
            self._validate_model_search_identity(resolved_model_root)
            self._write_extra_model_paths(
                resolved_model_root,
                resolved_comfy_root,
            )
            existing_config = self._load_config(required=False)
            instance_id = (
                existing_config.instance_id
                if existing_config is not None
                and existing_config.comfy_mode is planned_comfy_mode
                and existing_config.models_mode is resolved_models_mode
                and existing_config.comfyui_root == resolved_comfy_root
                and existing_config.comfyui_python == resolved_comfy_python
                and existing_config.model_root == resolved_model_root
                else uuid.uuid4().hex
            )
            config = RuntimeConfig(
                instance_id=instance_id,
                project_root=self.project_root,
                comfy_mode=planned_comfy_mode,
                models_mode=resolved_models_mode,
                comfyui_root=resolved_comfy_root,
                comfyui_python=resolved_comfy_python,
                model_root=resolved_model_root,
                gateway_port=self.lock.gateway.port,
                comfyui_port=self.lock.comfyui.port,
            )
            self._atomic_json(self.config_path, config.to_dict())
            report.changed = True
            report.mode = config.comfy_mode.value
            report.models_mode = config.models_mode.value
            report.actions = [
                RuntimeAction(
                    item.action_id,
                    "completed",
                    item.argv,
                    item.cwd,
                    item.detail,
                )
                for item in report.actions
            ]
            report.checks = self._preflight_checks(config, full_model_hash=False)
            report.ok = all(item.passed for item in report.checks)
            report.overall = "ready" if report.ok else "incompatible"
        return report

    def preflight(
        self,
        *,
        full_model_hash: bool = False,
        dry_run: bool = False,
    ) -> RuntimeReport:
        """唯讀檢查 pins、獨立 Python 與八顆模型。"""

        report = self._report("preflight", dry_run=dry_run)
        if dry_run:
            report.overall = "planned"
            report.actions = [
                RuntimeAction(
                    "inspect-runtime",
                    "planned",
                    detail="檢查 Git pins、worktree、Python 與 package metadata。",
                ),
                RuntimeAction(
                    "verify-models",
                    "planned",
                    detail=(
                        "逐顆計算 SHA-256。"
                        if full_model_hash
                        else "只核對模型存在與 exact bytes。"
                    ),
                ),
            ]
            return report
        config = self._load_config()
        if config is None:
            raise RuntimeOperationError(
                "RUNTIME_NOT_INSTALLED",
                "尚未執行 runtime install。",
            )
        report.mode = config.comfy_mode.value
        report.models_mode = config.models_mode.value
        report.checks = self._preflight_checks(
            config,
            full_model_hash=full_model_hash,
        )
        report.ok = all(item.passed for item in report.checks)
        report.overall = "ready" if report.ok else "incompatible"
        return report

    def start(
        self,
        *,
        gateway_only: bool = False,
        open_browser: bool = True,
        dry_run: bool = False,
        wait_seconds: float = 120.0,
    ) -> RuntimeReport:
        """啟動 loopback ComfyUI 與單 worker Gateway；不隱式安裝。"""

        config = self._load_config(required=not dry_run)
        if config is None:
            config = self._planned_default_config()
        report = self._report(
            "start",
            dry_run=dry_run,
            mode=config.comfy_mode,
            models_mode=config.models_mode,
        )
        gateway_argv = self._gateway_argv(config)
        comfy_argv = self._comfy_argv(config)
        if not gateway_only:
            report.actions.append(
                RuntimeAction(
                    "start-comfyui",
                    "planned",
                    comfy_argv,
                    str(self.data_root),
                )
            )
        report.actions.append(
            RuntimeAction(
                "start-gateway",
                "planned",
                gateway_argv,
                str(self.project_root),
            )
        )
        if open_browser:
            report.actions.append(
                RuntimeAction(
                    "open-browser",
                    "planned",
                    detail=f"best-effort 開啟 {self._urls(config)['gateway']}。",
                )
            )
        report.urls = self._urls(config)
        if dry_run:
            report.overall = "planned"
            return report

        with self._exclusive_lock():
            critical_checks = self._preflight_checks(
                config,
                full_model_hash=False,
            )
            report.checks = critical_checks
            check_by_id = {item.check_id: item for item in critical_checks}
            gateway_checks = [
                item
                for item in critical_checks
                if item.check_id
                in {
                    "platform",
                    "gateway-python",
                    "gateway-packages",
                    "gateway-port",
                    "git-ignore",
                    "managed-paths",
                    "state-writable",
                }
            ]
            if not all(item.passed for item in gateway_checks):
                report.ok = False
                report.overall = "incompatible"
                return report
            if (
                not gateway_only
                and "comfyui-port" in check_by_id
                and not check_by_id["comfyui-port"].passed
            ):
                report.ok = False
                report.overall = "incompatible"
                return report
            comfy_blocking_ids = {
                "comfyui-python",
                "comfyui-git",
                "gguf-git",
                "comfy-packages",
                "models-receipt",
                "nvidia",
                "disk-space",
                "extra-model-paths",
                "model-search-identity",
                "source-model-config",
                "runtime-data",
                "runtime-custom-nodes",
                "managed-paths",
                "state-writable",
            }
            comfy_allowed = not gateway_only and all(
                item.passed
                for item in critical_checks
                if item.check_id in comfy_blocking_ids
            )
            if not gateway_only and not comfy_allowed:
                report.warnings.append(
                    "ComfyUI preflight 未通過，本次只啟動 Gateway；網站仍可使用。"
                )

            records = self._load_processes()
            original_records = dict(records)
            started_records: list[ProcessRecord] = []
            started_comfy = False
            started_gateway = False
            gateway_health = "free"
            comfy_ready = False
            try:
                if not gateway_only:
                    comfy_health = self._http_health(
                        config.comfyui_port,
                        "/system_stats",
                    )
                    comfy_ownership = self._require_owned_or_free(
                        "COMFYUI",
                        comfy_health,
                        records.get("comfyui"),
                        port=config.comfyui_port,
                    )
                    if (
                        comfy_allowed
                        and comfy_health == "free"
                        and comfy_ownership == "free"
                    ):
                        records.pop("comfyui", None)
                        self._prepare_runtime_data()
                        self._progress("啟動 ComfyUI（首次載入可能需要一些時間）…")
                        pid = self._spawn_checked(
                            comfy_argv,
                            cwd=self.data_root,
                            env=self._comfy_env(),
                            log_path=self.state_dir / "logs/comfyui.log",
                        )
                        try:
                            record = self._record_process(
                                "comfyui",
                                pid,
                                comfy_argv,
                            )
                        except Exception:
                            self._terminate_fresh_process(pid)
                            raise
                        records["comfyui"] = record
                        started_records.append(record)
                        started_comfy = True
                        self._write_processes(records)

                    comfy_record = records.get("comfyui")
                    if comfy_allowed and comfy_record is not None:
                        comfy_ready = self._wait_for_health(
                            config.comfyui_port,
                            "/system_stats",
                            wait_seconds,
                        ) and self._service_owned(
                            config.comfyui_port,
                            comfy_record,
                        )

                gateway_health = self._http_health(
                    config.gateway_port,
                    "/api/v1/gateway/status",
                )
                gateway_ownership = self._require_owned_or_free(
                    "GATEWAY",
                    gateway_health,
                    records.get("gateway"),
                    port=config.gateway_port,
                )
                if gateway_health == "free" and gateway_ownership == "free":
                    records.pop("gateway", None)
                    self._progress("啟動 Gateway…")
                    pid = self._spawn_checked(
                        gateway_argv,
                        cwd=self.project_root,
                        env=self._gateway_env(
                            config,
                            comfy_enabled=comfy_ready,
                        ),
                        log_path=self.state_dir / "logs/gateway.log",
                    )
                    try:
                        record = self._record_process(
                            "gateway",
                            pid,
                            gateway_argv,
                        )
                    except Exception:
                        self._terminate_fresh_process(pid)
                        raise
                    record = replace(record, comfy_enabled=comfy_ready)
                    records["gateway"] = record
                    started_records.append(record)
                    started_gateway = True
                    self._write_processes(records)
            except Exception:
                for record in reversed(started_records):
                    with contextlib.suppress(Exception):
                        self._stop_record(record, timeout_seconds=5)
                with contextlib.suppress(Exception):
                    self._write_processes(original_records)
                raise

            gateway_record = records.get("gateway")
            gateway_ready = self._wait_for_health(
                config.gateway_port,
                "/api/v1/gateway/status",
                wait_seconds,
            ) and (
                gateway_record is not None
                and self._service_owned(config.gateway_port, gateway_record)
            )
            gateway_link_ready = gateway_only or (
                gateway_record is not None and gateway_record.comfy_enabled
            )
            report.changed = started_gateway or started_comfy
            report.ok = (
                gateway_ready and (gateway_only or comfy_ready) and gateway_link_ready
            )
            report.overall = (
                "ready" if report.ok else "degraded" if gateway_ready else "failed"
            )
            if (
                not gateway_only
                and gateway_ready
                and comfy_ready
                and not gateway_link_ready
            ):
                report.warnings.append(
                    "Gateway 本次未連接 ComfyUI；請先停止，再重新啟動完整服務。"
                )
            updated_actions: list[RuntimeAction] = []
            for item in report.actions:
                if item.action_id == "start-comfyui":
                    status = (
                        "skipped"
                        if not comfy_allowed
                        else "completed"
                        if comfy_ready
                        else "attempted"
                    )
                elif item.action_id == "start-gateway":
                    status = "completed" if gateway_ready else "attempted"
                else:
                    status = "attempted"
                updated_actions.append(
                    RuntimeAction(
                        item.action_id,
                        status,
                        item.argv,
                        item.cwd,
                        item.detail,
                    )
                )
            report.actions = updated_actions
            if gateway_ready and open_browser:
                try:
                    opened = self.browser_opener(report.urls["gateway"])
                except Exception:
                    opened = False
                report.actions = [
                    RuntimeAction(
                        item.action_id,
                        (
                            "completed"
                            if item.action_id == "open-browser" and opened
                            else item.status
                        ),
                        item.argv,
                        item.cwd,
                        item.detail,
                    )
                    for item in report.actions
                ]
                if not opened:
                    report.warnings.append(
                        f"無法自動開啟瀏覽器；請手動開啟 {report.urls['gateway']}。"
                    )
        return report

    def stop(
        self,
        *,
        dry_run: bool = False,
        timeout_seconds: float = 15.0,
    ) -> RuntimeReport:
        """只停止 identity 完整吻合的 runtime-owned process group。"""

        report = self._report("stop", dry_run=dry_run)
        records = self._load_processes(required=False)
        for name in ("gateway", "comfyui"):
            record = records.get(name)
            report.actions.append(
                RuntimeAction(
                    f"stop-{name}",
                    "planned" if record is not None else "skipped",
                    detail=(
                        f"只對 runtime-owned PID {record.pid} 發送 SIGTERM。"
                        if record is not None
                        else "沒有 runtime-owned process record。"
                    ),
                )
            )
        if dry_run:
            report.overall = "planned"
            return report

        with self._exclusive_lock():
            records = self._load_processes(required=False)
            remaining = dict(records)
            failures = False
            changed = False
            outcomes = {"gateway": "skipped", "comfyui": "skipped"}
            gateway = records.get("gateway")
            if gateway is not None:
                gateway_stopped = self._stop_record(
                    gateway,
                    timeout_seconds=timeout_seconds,
                )
                if gateway_stopped:
                    remaining.pop("gateway", None)
                    changed = True
                    outcomes["gateway"] = "completed"
                else:
                    failures = True
                    outcomes["gateway"] = "failed"
                    if records.get("comfyui") is not None:
                        outcomes["comfyui"] = "blocked"
                    report.checks.append(
                        RuntimeCheck(
                            "gateway-identity",
                            "fail",
                            "PROCESS_IDENTITY_MISMATCH",
                            "gateway PID identity 不符或未在期限內停止；"
                            "為避免截斷任務，不會停止 ComfyUI。",
                        )
                    )

            if not failures:
                comfyui = records.get("comfyui")
                if comfyui is not None:
                    comfyui_stopped = self._stop_record(
                        comfyui,
                        timeout_seconds=timeout_seconds,
                    )
                    if comfyui_stopped:
                        remaining.pop("comfyui", None)
                        changed = True
                        outcomes["comfyui"] = "completed"
                    else:
                        failures = True
                        outcomes["comfyui"] = "failed"
                        report.checks.append(
                            RuntimeCheck(
                                "comfyui-identity",
                                "fail",
                                "PROCESS_IDENTITY_MISMATCH",
                                "comfyui PID identity 不符或未在期限內停止；"
                                "未強制終止。",
                            )
                        )
            self._write_processes(remaining)
            report.changed = changed
            report.ok = not failures
            report.overall = "stopped" if not remaining else "degraded"
            report.actions = [
                RuntimeAction(
                    item.action_id,
                    outcomes[item.action_id.removeprefix("stop-")],
                    item.argv,
                    item.cwd,
                    item.detail,
                )
                for item in report.actions
            ]
        return report

    def status(self, *, dry_run: bool = False) -> RuntimeReport:
        """快速唯讀狀態；不啟 subprocess、不 hash 43GB、不 import torch。"""

        report = self._report("status", dry_run=dry_run)
        if dry_run:
            report.overall = "planned"
            report.actions = [
                RuntimeAction(
                    "read-status",
                    "planned",
                    detail="只讀 config、PID identity、ports 與模型 exact bytes。",
                )
            ]
            return report
        config = self._load_config(required=False)
        if config is None:
            report.ok = False
            report.overall = "unconfigured"
            report.checks.append(
                RuntimeCheck(
                    "config",
                    "fail",
                    "RUNTIME_NOT_INSTALLED",
                    "尚未執行 runtime install。",
                )
            )
            return report

        report.mode = config.comfy_mode.value
        report.models_mode = config.models_mode.value
        report.urls = self._urls(config)
        records = self._load_processes(required=False)
        owned: dict[str, bool] = {}
        for name in ("gateway", "comfyui"):
            record = records.get(name)
            matches = record is not None and self._process_matches(record)
            owned[name] = matches
            report.checks.append(
                RuntimeCheck(
                    f"{name}-process",
                    "pass" if matches else "warn",
                    "PROCESS_OWNED" if matches else "PROCESS_NOT_OWNED",
                    (
                        f"{name} 是 runtime-owned process。"
                        if matches
                        else f"{name} 沒有可驗證的 runtime-owned PID。"
                    ),
                )
            )
        gateway_health = self._http_health(
            config.gateway_port,
            "/api/v1/gateway/status",
        )
        comfy_health = self._http_health(config.comfyui_port, "/system_stats")
        gateway_record = records.get("gateway")
        comfy_record = records.get("comfyui")
        gateway_comfy_linked = (
            gateway_record is not None and gateway_record.comfy_enabled
        )
        gateway_valid = (
            gateway_health == "healthy"
            and owned["gateway"]
            and gateway_record is not None
            and self._service_owned(config.gateway_port, gateway_record)
        )
        comfy_service_valid = (
            comfy_health == "healthy"
            and owned["comfyui"]
            and comfy_record is not None
            and self._service_owned(config.comfyui_port, comfy_record)
        )
        comfy_valid = comfy_service_valid and gateway_comfy_linked
        models_check = self._model_size_check(config.model_root)
        report.checks.extend(
            [
                RuntimeCheck(
                    "gateway-health",
                    "pass" if gateway_valid else "fail",
                    "GATEWAY_READY"
                    if gateway_valid
                    else "GATEWAY_NOT_READY_OR_NOT_OWNED",
                    f"Gateway health：{gateway_health}。",
                ),
                RuntimeCheck(
                    "comfyui-health",
                    "pass" if comfy_service_valid else "warn",
                    "COMFYUI_READY"
                    if comfy_service_valid
                    else "COMFYUI_NOT_READY_OR_NOT_OWNED",
                    f"ComfyUI health：{comfy_health}。",
                ),
                RuntimeCheck(
                    "gateway-comfy-link",
                    "pass" if gateway_comfy_linked else "warn",
                    "GATEWAY_COMFY_LINKED"
                    if gateway_comfy_linked
                    else "GATEWAY_COMFY_NOT_LINKED",
                    (
                        "Gateway 啟動時已連接 ComfyUI。"
                        if gateway_comfy_linked
                        else "Gateway 啟動時未連接 ComfyUI；請停止後重新啟動完整服務。"
                    ),
                ),
                models_check,
            ]
        )
        report.ok = gateway_valid and comfy_valid and models_check.passed
        report.overall = (
            "ready"
            if gateway_valid and comfy_valid and models_check.passed
            else "degraded"
            if gateway_valid or comfy_service_valid
            else "stopped"
        )
        return report

    def _install_plan(
        self,
        comfy_mode: RuntimeMode,
        models_mode: ModelsMode,
        comfy_root: Path,
        comfy_python: Path,
        model_root: Path,
    ) -> list[RuntimeAction]:
        uv = str(self.state_dir / "tools/uv")
        gateway_python = self._managed_python_executable(self.lock.gateway.python)
        comfy_base_python = self._managed_python_executable(self.lock.comfyui.python)
        actions = [
            RuntimeAction(
                "bootstrap-uv",
                "planned",
                detail=(
                    f"下載 uv {self.lock.uv.version} 固定 Linux asset，"
                    f"驗證 SHA-256 {self.lock.uv.sha256} 後安全解壓。"
                ),
            ),
            RuntimeAction(
                "install-gateway-python",
                "planned",
                (
                    uv,
                    "python",
                    "install",
                    "--install-dir",
                    str(self.state_dir / "python"),
                    "--no-bin",
                    self.lock.gateway.python,
                ),
                str(self.project_root),
            ),
            RuntimeAction(
                "sync-gateway",
                "planned",
                (
                    uv,
                    "sync",
                    "--locked",
                    "--dev",
                    "--python",
                    str(gateway_python),
                ),
                str(self.project_root),
            ),
        ]
        if comfy_mode is RuntimeMode.MANAGED:
            node = self.lock.custom_nodes[0]
            actions.extend(
                [
                    RuntimeAction(
                        "clone-comfyui",
                        "planned",
                        (
                            "git",
                            "clone",
                            "--filter=blob:none",
                            "--no-checkout",
                            self.lock.comfyui.repository,
                            str(self.state_dir / "staging/comfyui"),
                        ),
                        str(self.project_root),
                        f"checkout {self.lock.comfyui.commit}",
                    ),
                    RuntimeAction(
                        "clone-comfyui-gguf",
                        "planned",
                        (
                            "git",
                            "clone",
                            "--filter=blob:none",
                            "--no-checkout",
                            node.repository,
                            "custom_nodes/ComfyUI-GGUF",
                        ),
                        str(self.state_dir / "staging/comfyui"),
                        f"checkout {node.commit}",
                    ),
                    RuntimeAction(
                        "create-comfy-venv",
                        "planned",
                        (
                            uv,
                            "venv",
                            "--python",
                            str(comfy_base_python),
                            str(comfy_root / ".venv"),
                        ),
                        str(comfy_root),
                    ),
                    RuntimeAction(
                        "sync-comfy-requirements",
                        "planned",
                        (
                            uv,
                            "pip",
                            "sync",
                            "--python",
                            str(comfy_python),
                            str(
                                self.project_root
                                / "runtime/comfy-requirements.lock.txt"
                            ),
                            "--require-hashes",
                        ),
                        str(comfy_root),
                    ),
                ]
            )
        else:
            actions.append(
                RuntimeAction(
                    "validate-adopted-comfyui",
                    "planned",
                    detail=(
                        "唯讀檢查 core/GGUF commits、clean worktree 與 Python；"
                        "不執行 checkout、pip 或寫入 adopted root。"
                    ),
                )
            )
        actions.append(
            RuntimeAction(
                "resolve-models",
                "planned",
                detail=(
                    "完整 SHA 通過後唯讀採用 external model root；"
                    f"否則下載至 managed root {model_root}，使用 .part 續傳。"
                    if models_mode is ModelsMode.AUTO
                    else "只驗證 external models，不修改。"
                    if models_mode is ModelsMode.EXTERNAL
                    else "依 models.lock.json 下載 managed models，完成 SHA 後 rename。"
                ),
            )
        )
        return actions

    def _install_managed_comfy(self, uv_binary: str) -> None:
        root = self.managed_comfy_root
        managed_venv = root / ".venv"
        node_root = root / "custom_nodes/ComfyUI-GGUF"
        for label, path in (
            ("managed ComfyUI venv", managed_venv),
            ("managed ComfyUI-GGUF", node_root),
        ):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise RuntimeOperationError(
                    "MANAGED_PATH_INVALID",
                    f"{label} 必須是非 symlink 目錄。",
                )
        if root.exists():
            self._validate_managed_marker(root)
            self._validate_git_tree(
                root,
                self.lock.comfyui.commit,
                "ComfyUI",
                include_untracked=False,
            )
            self._validate_git_tree(
                root / "custom_nodes/ComfyUI-GGUF",
                self.lock.custom_nodes[0].commit,
                "ComfyUI-GGUF",
                include_untracked=False,
            )
        else:
            staging = self.state_dir / "staging/comfyui"
            if staging.exists():
                raise RuntimeOperationError(
                    "MANAGED_STAGING_CONFLICT",
                    "managed staging 已存在；為避免刪除未知資料，請人工確認。",
                )
            staging.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._progress("Clone ComfyUI pinned source…")
            self._run_checked(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    self.lock.comfyui.repository,
                    str(staging),
                ],
                cwd=self.project_root,
            )
            self._run_checked(
                [
                    "git",
                    "-C",
                    str(staging),
                    "checkout",
                    "--detach",
                    self.lock.comfyui.commit,
                ],
            )
            node = self.lock.custom_nodes[0]
            node_root = staging / "custom_nodes/ComfyUI-GGUF"
            self._progress("Clone ComfyUI-GGUF pinned source…")
            self._run_checked(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    node.repository,
                    str(node_root),
                ],
                cwd=staging,
            )
            self._run_checked(
                [
                    "git",
                    "-C",
                    str(node_root),
                    "checkout",
                    "--detach",
                    node.commit,
                ],
            )
            self._validate_git_tree(
                staging,
                self.lock.comfyui.commit,
                "ComfyUI",
                include_untracked=False,
            )
            self._validate_git_tree(
                node_root,
                node.commit,
                "ComfyUI-GGUF",
                include_untracked=False,
            )
            self._write_managed_marker(staging)
            os.replace(staging, root)

        python_path = self._default_comfy_python(root, RuntimeMode.MANAGED)
        self._progress(f"準備 ComfyUI Python {self.lock.comfyui.python}…")
        self._run_checked(
            [
                uv_binary,
                "python",
                "install",
                "--install-dir",
                str(self.state_dir / "python"),
                "--no-bin",
                self.lock.comfyui.python,
            ],
            cwd=root,
        )
        comfy_base_python = self._managed_python_executable(self.lock.comfyui.python)
        if not comfy_base_python.is_file():
            raise RuntimeOperationError(
                "PYTHON_INSTALL_FAILED",
                "uv 未在 project-local install-dir 建立 ComfyUI Python。",
            )
        if not python_path.exists():
            self._run_checked(
                [
                    uv_binary,
                    "venv",
                    "--python",
                    str(comfy_base_python),
                    str(root / ".venv"),
                ],
                cwd=root,
            )
        else:
            self._require_python_version(
                python_path,
                self.lock.comfyui.python,
                "managed ComfyUI",
            )
        self._progress("同步 ComfyUI hashed dependency lock（含 CUDA 13.0 wheels）…")
        self._run_checked(
            [
                uv_binary,
                "pip",
                "sync",
                "--python",
                str(python_path),
                str(self.project_root / "runtime/comfy-requirements.lock.txt"),
                "--require-hashes",
            ],
            cwd=root,
        )

    def _resolve_models(
        self,
        *,
        models_mode: ModelsMode,
        requested_root: Path | None,
        comfyui_root: Path,
    ) -> tuple[ModelsMode, Path]:
        if models_mode is ModelsMode.EXTERNAL:
            root = self._require_path_argument(requested_root, "external model root")
            self._progress(f"驗證 external models：{root}")
            self._verify_all_models(root)
            self._write_model_receipt(root)
            return ModelsMode.EXTERNAL, root
        if models_mode is ModelsMode.AUTO:
            if requested_root is not None:
                root = self._safe_cli_path(str(requested_root), "model root")
                self._progress(f"驗證指定 external models：{root}")
                self._verify_all_models(root)
                self._write_model_receipt(root)
                return ModelsMode.EXTERNAL, root
            candidates = self._external_model_candidates(
                requested_root,
                comfyui_root,
            )
            for candidate in candidates:
                if self._model_receipt_matches(candidate):
                    self._progress(f"重用已驗證 external models receipt：{candidate}")
                    return ModelsMode.EXTERNAL, candidate
                self._progress(f"掃描 external model candidate：{candidate}")
                try:
                    self._verify_all_models(candidate)
                except RuntimeOperationError:
                    continue
                self._write_model_receipt(candidate)
                return ModelsMode.EXTERNAL, candidate
        self._ensure_model_disk_space()
        self.managed_model_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        downloader = self.downloader_factory(self.managed_model_root)
        for index, model in enumerate(self.lock.models, start=1):
            self._progress(f"模型 {index}/8：下載或重用 {model.filename}")
            try:
                downloader.download(model)
            except DownloadError as exc:
                raise RuntimeOperationError(
                    "MODEL_DOWNLOAD_FAILED",
                    str(exc),
                ) from exc
        self._write_model_receipt(self.managed_model_root)
        return ModelsMode.MANAGED, self.managed_model_root

    def _external_model_candidates(
        self,
        requested_root: Path | None,
        comfyui_root: Path,
    ) -> list[Path]:
        candidates: list[Path] = []
        if requested_root is not None:
            candidates.append(requested_root.expanduser().resolve())
        env_root = self.environ.get("FPMVP_MODEL_ROOT")
        if env_root:
            candidates.append(self._safe_cli_path(env_root, "FPMVP_MODEL_ROOT"))
        candidates.append((comfyui_root / "models").resolve())
        home = Path(self.environ.get("HOME", str(Path.home()))).expanduser().resolve()
        for candidate_comfy in (home / "ai/ComfyUI", home / "ComfyUI"):
            candidates.append((candidate_comfy / "models").resolve())
            candidates.extend(self._discover_canonical_model_roots(candidate_comfy))
        unique: list[Path] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)
        return unique

    @staticmethod
    def _discover_canonical_model_roots(comfyui_root: Path) -> list[Path]:
        """唯讀解析已知 Comfy root 的絕對 base_path/models 候選。

        這只用於尋找可完整 SHA 驗證的 external root；runtime 不會把來源
        YAML 傳給 managed ComfyUI，也不推測任意 category mapping。
        """

        config = comfyui_root / "extra_model_paths.yaml"
        try:
            if (
                config.is_symlink()
                or not config.is_file()
                or config.stat().st_size > 1024 * 1024
            ):
                return []
            lines = config.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        roots: list[Path] = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped.startswith("base_path:"):
                continue
            value = stripped.removeprefix("base_path:").strip().strip("\"'")
            if not value or "\x00" in value or "\\" in value or value.startswith("$"):
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                continue
            candidate = (path / "models").resolve()
            if candidate not in roots:
                roots.append(candidate)
        return roots

    def _verify_all_models(self, model_root: Path) -> None:
        root = model_root.expanduser().resolve()
        for index, model in enumerate(self.lock.models, start=1):
            self._progress(f"模型 {index}/8：SHA-256 {model.filename}")
            try:
                ModelDownloader.verify(root, model)
            except DownloadError as exc:
                raise RuntimeOperationError(
                    "MODEL_VERIFICATION_FAILED",
                    str(exc),
                ) from exc

    def _validate_model_search_identity(self, selected_model_root: Path) -> None:
        selected = selected_model_root.resolve()
        default_models = self.data_root / "models"
        for model in self.lock.models:
            selected_file = selected / Path(*model.relative_path.parts)
            candidates = [
                selected / subdir / model.filename
                for subdir in _SELECTED_MODEL_SUBDIRS[model.subdir]
            ]
            candidates.extend(
                default_models / subdir / model.filename
                for subdir in _MODEL_SEARCH_SUBDIRS[model.subdir]
            )
            for possible_shadow in candidates:
                if not possible_shadow.exists() and not possible_shadow.is_symlink():
                    continue
                if possible_shadow.resolve() == selected_file.resolve():
                    continue
                if not self._model_file_matches(possible_shadow, model):
                    raise RuntimeOperationError(
                        "MODEL_SEARCH_SHADOW_MISMATCH",
                        (
                            "ComfyUI 較高優先序路徑含同名但不同 identity 的 "
                            f"{model.filename}；不會啟動。"
                        ),
                    )

    @staticmethod
    def _model_file_matches(path: Path, model: ModelLock) -> bool:
        if path.is_symlink() or not path.is_file():
            return False
        try:
            if path.stat().st_size != model.size_bytes:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(8 * 1024 * 1024):
                    digest.update(chunk)
        except OSError:
            return False
        return digest.hexdigest() == model.sha256

    def _validate_adopted_comfy(
        self,
        root: Path,
        python_path: Path,
    ) -> None:
        if not root.is_dir() or not (root / "main.py").is_file():
            raise RuntimeOperationError(
                "ADOPTED_COMFYUI_INVALID",
                "adopted ComfyUI root 缺少 main.py。",
            )
        node_root = root / "custom_nodes/ComfyUI-GGUF"
        if node_root.is_symlink() or not node_root.is_dir():
            raise RuntimeOperationError(
                "ADOPTED_COMFYUI_INVALID",
                "adopted ComfyUI-GGUF 必須是 source root 內的非 symlink 目錄。",
            )
        self._validate_git_tree(
            root,
            self.lock.comfyui.commit,
            "ComfyUI",
            include_untracked=False,
        )
        self._validate_git_tree(
            node_root,
            self.lock.custom_nodes[0].commit,
            "ComfyUI-GGUF",
            include_untracked=True,
        )
        self._require_python_version(
            python_path,
            self.lock.comfyui.python,
            "ComfyUI",
        )
        package_check = self._comfy_packages_check(python_path)
        if not package_check.passed:
            raise RuntimeOperationError(
                "PACKAGE_PIN_MISMATCH",
                package_check.message,
            )

    @staticmethod
    def _reject_source_extra_model_paths(comfyui_root: Path) -> None:
        source_config = comfyui_root / "extra_model_paths.yaml"
        if source_config.exists() or source_config.is_symlink():
            raise RuntimeOperationError(
                "SOURCE_MODEL_CONFIG_CONFLICT",
                "ComfyUI source 含會優先載入的 extra_model_paths.yaml；"
                "請移除該 machine-local 設定或改用預設 managed code。",
            )

    def _validate_git_tree(
        self,
        root: Path,
        commit: str,
        label: str,
        *,
        include_untracked: bool,
    ) -> None:
        head = self._run_checked(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            env=self._git_audit_env(),
        ).stdout.strip()
        if head != commit:
            raise RuntimeOperationError(
                "GIT_PIN_MISMATCH",
                f"{label} commit 不符合 runtime lock。",
            )
        dirty = self._run_checked(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain",
                f"--untracked-files={'all' if include_untracked else 'no'}",
            ],
            env=self._git_audit_env(),
        ).stdout
        if dirty:
            raise RuntimeOperationError(
                "GIT_WORKTREE_DIRTY",
                f"{label} worktree 不是 clean；不會自動修改。",
            )

    def _preflight_checks(
        self,
        config: RuntimeConfig,
        *,
        full_model_hash: bool,
    ) -> list[RuntimeCheck]:
        checks: list[RuntimeCheck] = []
        checks.append(self._platform_check())
        checks.append(self._git_ignore_check())
        checks.append(self._managed_paths_check())
        checks.append(self._state_writable_check())
        checks.append(
            self._python_check(
                self.project_root / ".venv/bin/python",
                self.lock.gateway.python,
                "gateway-python",
            )
        )
        checks.append(self._gateway_packages_check())
        checks.append(
            self._python_check(
                config.comfyui_python,
                self.lock.comfyui.python,
                "comfyui-python",
            )
        )
        checks.extend(self._git_checks(config))
        checks.append(self._source_model_config_check(config.comfyui_root))
        checks.append(self._extra_model_paths_check(config))
        checks.append(self._runtime_data_check())
        checks.append(self._runtime_custom_nodes_check())
        checks.append(self._model_search_identity_check(config.model_root))
        checks.append(self._comfy_packages_check(config.comfyui_python))
        checks.append(
            self._model_hash_check(config.model_root)
            if full_model_hash
            else self._model_receipt_check(config.model_root)
        )
        checks.append(self._nvidia_check())
        checks.append(self._disk_space_check(config))
        checks.extend(self._port_checks(config))
        checks.append(
            RuntimeCheck(
                "agents",
                "warn",
                "AGENTS_PENDING",
                "角色與場景 agent 尚待接入；不阻擋網站與既有分鏡 workflow。",
            )
        )
        return checks

    def _gateway_packages_check(self) -> RuntimeCheck:
        try:
            expected = self._gateway_locked_versions()
            code = (
                "import importlib.metadata,json;"
                f"names={list(expected)!r};"
                "print(json.dumps({n:importlib.metadata.version(n) for n in names},"
                "sort_keys=True))"
            )
            result = self._run_checked(
                [str(self.project_root / ".venv/bin/python"), "-B", "-c", code],
                env=self._audit_env(),
            )
            actual = json.loads(result.stdout)
        except (RuntimeOperationError, OSError, ValueError, json.JSONDecodeError):
            expected = {}
            actual = None
        ok = bool(expected) and actual == expected
        return RuntimeCheck(
            "gateway-packages",
            "pass" if ok else "fail",
            "GATEWAY_PACKAGES_OK" if ok else "GATEWAY_PACKAGES_MISMATCH",
            (
                "Gateway direct dependencies 符合 uv.lock exact versions。"
                if ok
                else "Gateway direct dependencies 與 uv.lock 不一致。"
            ),
        )

    def _gateway_locked_versions(self) -> dict[str, str]:
        try:
            payload = tomllib.loads(
                (self.project_root / "uv.lock").read_text(encoding="utf-8")
            )
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise RuntimeOperationError(
                "GATEWAY_LOCK_INVALID",
                "無法讀取 Gateway uv.lock。",
            ) from exc
        packages = payload.get("package")
        if not isinstance(packages, list):
            raise RuntimeOperationError(
                "GATEWAY_LOCK_INVALID",
                "Gateway uv.lock 缺少 package 清單。",
            )
        versions: dict[str, str] = {}
        for item in packages:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            version = item.get("version")
            if name in _GATEWAY_PACKAGE_NAMES and isinstance(version, str):
                versions[str(name)] = version
        if set(versions) != set(_GATEWAY_PACKAGE_NAMES):
            raise RuntimeOperationError(
                "GATEWAY_LOCK_INVALID",
                "Gateway uv.lock 缺少 direct dependency pin。",
            )
        return dict(sorted(versions.items()))

    def _source_model_config_check(self, comfyui_root: Path) -> RuntimeCheck:
        source_config = comfyui_root / "extra_model_paths.yaml"
        ok = not source_config.exists() and not source_config.is_symlink()
        return RuntimeCheck(
            "source-model-config",
            "pass" if ok else "fail",
            "SOURCE_MODEL_CONFIG_ABSENT" if ok else "SOURCE_MODEL_CONFIG_CONFLICT",
            (
                "ComfyUI source 沒有會優先載入的 extra_model_paths.yaml。"
                if ok
                else "ComfyUI source 含優先載入的 extra_model_paths.yaml；"
                "為避免模型 shadowing 不會啟動。"
            ),
        )

    def _extra_model_paths_check(self, config: RuntimeConfig) -> RuntimeCheck:
        expected = self._extra_model_paths_bytes(
            config.model_root,
            config.comfyui_root,
        )
        try:
            actual = self.extra_models_path.read_bytes()
        except OSError:
            actual = b""
        ok = (
            self.extra_models_path.is_file()
            and not self.extra_models_path.is_symlink()
            and actual == expected
        )
        return RuntimeCheck(
            "extra-model-paths",
            "pass" if ok else "fail",
            "EXTRA_MODEL_PATHS_OK" if ok else "EXTRA_MODEL_PATHS_MISMATCH",
            (
                "runtime model/custom-node path config 符合目前 config。"
                if ok
                else "runtime extra_model_paths.yaml 缺少或與目前 config 不符。"
            ),
        )

    def _runtime_custom_nodes_check(self) -> RuntimeCheck:
        path = self.data_root / "custom_nodes"
        if self.data_root.is_symlink():
            ok = False
        elif not path.exists() and not path.is_symlink():
            ok = True
        else:
            try:
                ok = path.is_dir() and not path.is_symlink() and not any(path.iterdir())
            except OSError:
                ok = False
        return RuntimeCheck(
            "runtime-custom-nodes",
            "pass" if ok else "fail",
            "RUNTIME_CUSTOM_NODES_EMPTY" if ok else "RUNTIME_CUSTOM_NODES_CONFLICT",
            (
                "runtime base 沒有可 shadow allowlist 的 custom node。"
                if ok
                else "runtime comfy-data/custom_nodes 必須是空白、非 symlink 目錄。"
            ),
        )

    def _runtime_data_check(self) -> RuntimeCheck:
        paths = [
            self.data_root,
            *(self.data_root / name for name in ("input", "output", "temp", "user")),
            self.data_root / "models",
        ]
        ok = not self.data_root.is_symlink()
        for path in paths:
            if path.exists() or path.is_symlink():
                ok = ok and path.is_dir() and not path.is_symlink()
        return RuntimeCheck(
            "runtime-data",
            "pass" if ok else "fail",
            "RUNTIME_DATA_PATHS_OK" if ok else "RUNTIME_DATA_PATH_INVALID",
            (
                "ComfyUI mutation roots 均位於非 symlink runtime 目錄。"
                if ok
                else "ComfyUI input/output/temp/user/models 含非目錄或 symlink。"
            ),
        )

    def _model_search_identity_check(self, model_root: Path) -> RuntimeCheck:
        try:
            self._validate_model_search_identity(model_root)
        except RuntimeOperationError as exc:
            return RuntimeCheck(
                "model-search-identity",
                "fail",
                exc.code,
                exc.message,
            )
        return RuntimeCheck(
            "model-search-identity",
            "pass",
            "MODEL_SEARCH_IDENTITY_OK",
            "所有較高優先序的同名模型皆缺席或 identity 相同。",
        )

    def _port_checks(self, config: RuntimeConfig) -> list[RuntimeCheck]:
        records = self._load_processes(required=False)
        return [
            self._port_check(
                "gateway-port",
                config.gateway_port,
                "/api/v1/gateway/status",
                records.get("gateway"),
            ),
            self._port_check(
                "comfyui-port",
                config.comfyui_port,
                "/system_stats",
                records.get("comfyui"),
            ),
        ]

    def _port_check(
        self,
        check_id: str,
        port: int,
        path: str,
        record: ProcessRecord | None,
    ) -> RuntimeCheck:
        health = self._http_health(port, path)
        owned = (
            health == "healthy"
            and record is not None
            and self._process_matches(record)
            and self._service_owned(port, record)
        )
        ok = health == "free" or owned
        return RuntimeCheck(
            check_id,
            "pass" if ok else "fail",
            "PORT_FREE" if health == "free" else "PORT_OWNED" if owned else "PORT_BUSY",
            (
                f"127.0.0.1:{port} 可供 runtime 使用。"
                if health == "free"
                else f"127.0.0.1:{port} 由本 runtime process 持有。"
                if owned
                else f"127.0.0.1:{port} 被未知 process 占用。"
            ),
        )

    def _nvidia_check(self) -> RuntimeCheck:
        binary = self.runner.which("nvidia-smi")
        if binary is None:
            return RuntimeCheck(
                "nvidia",
                "fail",
                "NVIDIA_NOT_FOUND",
                "找不到 nvidia-smi；不會 import torch 或啟動 GPU probe。",
            )
        try:
            result = self._run_checked(
                [
                    binary,
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ]
            )
            rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            memory_values = [
                int(row.rsplit(",", 1)[1].strip()) for row in rows if "," in row
            ]
        except (RuntimeOperationError, ValueError):
            memory_values = []
        ok = bool(memory_values) and max(memory_values) >= 15_000
        return RuntimeCheck(
            "nvidia",
            "pass" if ok else "fail",
            "NVIDIA_READY" if ok else "NVIDIA_INSUFFICIENT",
            (
                "NVIDIA GPU metadata 顯示至少 15,000 MiB VRAM。"
                if ok
                else "未偵測到符合 16GB 級目標的 NVIDIA GPU metadata。"
            ),
        )

    def _git_checks(self, config: RuntimeConfig) -> list[RuntimeCheck]:
        checks: list[RuntimeCheck] = []
        pairs = (
            (
                config.comfyui_root,
                self.lock.comfyui.commit,
                "comfyui-git",
                False,
            ),
            (
                config.comfyui_root / "custom_nodes/ComfyUI-GGUF",
                self.lock.custom_nodes[0].commit,
                "gguf-git",
                config.comfy_mode is RuntimeMode.ADOPTED,
            ),
        )
        for root, commit, check_id, include_untracked in pairs:
            try:
                head = self._run_checked(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    env=self._git_audit_env(),
                ).stdout.strip()
                dirty = self._run_checked(
                    [
                        "git",
                        "-C",
                        str(root),
                        "status",
                        "--porcelain",
                        (
                            "--untracked-files=all"
                            if include_untracked
                            else "--untracked-files=no"
                        ),
                    ],
                    env=self._git_audit_env(),
                ).stdout
            except RuntimeOperationError:
                head = ""
                dirty = "unknown"
            ok = head == commit and not dirty
            checks.append(
                RuntimeCheck(
                    check_id,
                    "pass" if ok else "fail",
                    "GIT_PIN_OK" if ok else "GIT_PIN_OR_CLEANLINESS_FAILED",
                    (
                        f"{check_id} 符合完整 commit 且 worktree clean。"
                        if ok
                        else f"{check_id} commit 或 clean worktree 驗證失敗。"
                    ),
                )
            )
        return checks

    def _python_check(
        self,
        python_path: Path,
        expected: str,
        check_id: str,
    ) -> RuntimeCheck:
        try:
            result = self._run_checked([str(python_path), "--version"])
            actual = (result.stdout or result.stderr).strip().removeprefix("Python ")
        except RuntimeOperationError:
            actual = ""
        ok = actual == expected
        return RuntimeCheck(
            check_id,
            "pass" if ok else "fail",
            "PYTHON_VERSION_OK" if ok else "PYTHON_VERSION_MISMATCH",
            f"預期 Python {expected}；偵測為 {actual or 'unavailable'}。",
        )

    def _package_check(
        self,
        python_path: Path,
        expected: Mapping[str, str],
    ) -> RuntimeCheck:
        code = (
            "import importlib.metadata,json;"
            f"names={list(expected)!r};"
            "print(json.dumps({n:importlib.metadata.version(n) for n in names},"
            "sort_keys=True))"
        )
        try:
            result = self._run_checked(
                [str(python_path), "-B", "-c", code],
                env=self._audit_env(),
            )
            actual = json.loads(result.stdout)
        except (RuntimeOperationError, json.JSONDecodeError):
            actual = {}
        ok = actual == dict(expected)
        return RuntimeCheck(
            "comfy-packages",
            "pass" if ok else "fail",
            "PACKAGE_PINS_OK" if ok else "PACKAGE_PIN_MISMATCH",
            (
                "package metadata 符合 exact pins。"
                if ok
                else "package metadata 不符合 exact pins。"
            ),
        )

    def _comfy_packages_check(self, python_path: Path) -> RuntimeCheck:
        expected = self._locked_requirement_versions(
            self.project_root / "runtime/comfy-requirements.lock.txt"
        )
        check = self._package_check(python_path, expected)
        if check.passed:
            return RuntimeCheck(
                "comfy-packages",
                "pass",
                "PACKAGE_PINS_OK",
                f"ComfyUI 的 {len(expected)} 個 locked packages 全數符合。",
            )
        return RuntimeCheck(
            "comfy-packages",
            "fail",
            "PACKAGE_PIN_MISMATCH",
            "ComfyUI dependency metadata 與 hashed requirements lock 不一致。",
        )

    @staticmethod
    def _locked_requirement_versions(path: Path) -> dict[str, str]:
        pattern = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;\\]+)")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RuntimeOperationError(
                "COMFY_LOCK_INVALID",
                "無法讀取 ComfyUI hashed requirements lock。",
            ) from exc
        versions: dict[str, str] = {}
        for line in lines:
            match = pattern.match(line)
            if match is not None:
                versions[match.group(1)] = match.group(2)
        if not versions:
            raise RuntimeOperationError(
                "COMFY_LOCK_INVALID",
                "ComfyUI hashed requirements lock 沒有 exact package pins。",
            )
        return dict(sorted(versions.items()))

    def _model_size_check(self, model_root: Path) -> RuntimeCheck:
        missing: list[str] = []
        root = model_root.expanduser().resolve()
        for model in self.lock.models:
            path = root / Path(*model.relative_path.parts)
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            if path.is_symlink() or not path.is_file() or size != model.size_bytes:
                missing.append(model.filename)
        return RuntimeCheck(
            "models-size",
            "pass" if not missing else "fail",
            "MODEL_BYTES_OK" if not missing else "MODEL_MISSING_OR_WRONG_SIZE",
            (
                "八顆模型 exact bytes 均符合 models lock。"
                if not missing
                else f"{len(missing)} 顆模型缺少或大小錯誤。"
            ),
        )

    def _model_receipt_check(self, model_root: Path) -> RuntimeCheck:
        size_check = self._model_size_check(model_root)
        if not size_check.passed:
            return RuntimeCheck(
                "models-receipt",
                "fail",
                size_check.code,
                size_check.message,
            )
        matches = self._model_receipt_matches(model_root)
        return RuntimeCheck(
            "models-receipt",
            "pass" if matches else "fail",
            "MODEL_RECEIPT_OK" if matches else "MODEL_RECEIPT_STALE",
            (
                "八顆模型 metadata 與 install SHA receipt 均未改變。"
                if matches
                else "模型 receipt 缺少或 metadata 已變；請重新 install 驗證。"
            ),
        )

    def _model_hash_check(self, model_root: Path) -> RuntimeCheck:
        try:
            self._verify_all_models(model_root)
        except RuntimeOperationError as exc:
            return RuntimeCheck(
                "models-sha256",
                "fail",
                exc.code,
                exc.message,
            )
        return RuntimeCheck(
            "models-sha256",
            "pass",
            "MODEL_SHA256_OK",
            "八顆模型 exact bytes 與 SHA-256 均符合 models lock。",
        )

    def _write_model_receipt(self, model_root: Path) -> None:
        root = model_root.expanduser().resolve()
        files: dict[str, object] = {}
        for model in self.lock.models:
            relative = model.relative_path.as_posix()
            path = root / Path(*model.relative_path.parts)
            stat_result = path.stat()
            files[relative] = {
                "device": stat_result.st_dev,
                "inode": stat_result.st_ino,
                "size": stat_result.st_size,
                "mtime_ns": stat_result.st_mtime_ns,
                "sha256": model.sha256,
            }
        self._atomic_json(
            self.model_receipt_path,
            {
                "schema_version": 1,
                "models_lock_sha256": self._models_lock_digest(),
                "model_root": str(root),
                "files": files,
            },
        )

    def _model_receipt_matches(self, model_root: Path) -> bool:
        root = model_root.expanduser().resolve()
        try:
            payload = json.loads(self.model_receipt_path.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return False
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {"schema_version", "models_lock_sha256", "model_root", "files"}
            or payload.get("schema_version") != 1
            or payload.get("models_lock_sha256") != self._models_lock_digest()
            or payload.get("model_root") != str(root)
            or not isinstance(payload.get("files"), dict)
        ):
            return False
        files = cast(dict[str, object], payload["files"])
        if set(files) != {model.relative_path.as_posix() for model in self.lock.models}:
            return False
        for model in self.lock.models:
            path = root / Path(*model.relative_path.parts)
            if path.is_symlink():
                return False
            try:
                stat_result = path.stat()
            except OSError:
                return False
            raw = files.get(model.relative_path.as_posix())
            if not isinstance(raw, dict) or set(raw) != {
                "device",
                "inode",
                "size",
                "mtime_ns",
                "sha256",
            }:
                return False
            if (
                raw.get("device") != stat_result.st_dev
                or raw.get("inode") != stat_result.st_ino
                or raw.get("size") != stat_result.st_size
                or raw.get("mtime_ns") != stat_result.st_mtime_ns
                or raw.get("sha256") != model.sha256
            ):
                return False
        return True

    @staticmethod
    def _models_lock_digest() -> str:
        encoded = Path(__file__).with_name("models.lock.json").read_bytes()
        return hashlib.sha256(encoded).hexdigest()

    def _ensure_model_disk_space(self) -> None:
        required = 0
        for model in self.lock.models:
            target = self.managed_model_root / Path(*model.relative_path.parts)
            part = target.with_name(f"{target.name}.part")
            if target.is_file() and not target.is_symlink():
                try:
                    ModelDownloader.verify(self.managed_model_root, model)
                except DownloadError:
                    required += model.size_bytes
                continue
            try:
                partial_bytes = (
                    part.stat().st_size
                    if part.is_file()
                    and not part.is_symlink()
                    and part.stat().st_size < model.size_bytes
                    else 0
                )
            except OSError:
                partial_bytes = 0
            required += model.size_bytes - partial_bytes
        reserve = 5 * 1024 * 1024 * 1024
        anchor = self.state_dir
        while not anchor.exists() and anchor != anchor.parent:
            anchor = anchor.parent
        free = shutil.disk_usage(anchor).free
        if free < required + reserve:
            raise RuntimeOperationError(
                "DISK_SPACE_INSUFFICIENT",
                "managed models 下載空間不足（需保留模型大小外加 5 GiB）。",
            )

    def _disk_space_check(self, config: RuntimeConfig) -> RuntimeCheck:
        anchors = {self._existing_anchor(self.state_dir)}
        if config.models_mode is ModelsMode.MANAGED:
            anchors.add(self._existing_anchor(config.model_root))
        free_values: list[int] = []
        for anchor in anchors:
            try:
                free_values.append(shutil.disk_usage(anchor).free)
            except OSError:
                free_values.append(0)
        free = min(free_values, default=0)
        minimum = 2 * 1024 * 1024 * 1024
        ok = free >= minimum
        return RuntimeCheck(
            "disk-space",
            "pass" if ok else "fail",
            "DISK_SPACE_OK" if ok else "DISK_SPACE_LOW",
            (
                "runtime 可用磁碟空間至少 2 GiB。"
                if ok
                else "runtime 可用磁碟空間低於 2 GiB。"
            ),
        )

    @staticmethod
    def _existing_anchor(path: Path) -> Path:
        anchor = path
        while not anchor.exists() and anchor != anchor.parent:
            anchor = anchor.parent
        return anchor

    def _platform_check(self) -> RuntimeCheck:
        is_linux = platform.system() == "Linux"
        is_x86_64 = platform.machine().lower() in {"x86_64", "amd64"}
        release = platform.release().lower()
        is_wsl2 = "microsoft" in release and "wsl2" in release
        os_release = self._os_release()
        is_ubuntu_2404 = (
            os_release.get("ID") == "ubuntu" and os_release.get("VERSION_ID") == "24.04"
        )
        ok = is_linux and is_x86_64 and is_wsl2 and is_ubuntu_2404
        return RuntimeCheck(
            "platform",
            "pass" if ok else "fail",
            "PLATFORM_OK" if ok else "PLATFORM_UNSUPPORTED",
            (
                "WSL2／Ubuntu 24.04／x86_64 runtime 可用。"
                if ok
                else "目前只支援 WSL2/Ubuntu 24.04 或 Linux x86_64。"
            ),
        )

    @staticmethod
    def _os_release() -> dict[str, str]:
        try:
            lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        result: dict[str, str] = {}
        for line in lines:
            if "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            result[key] = value.strip().strip("\"'")
        return result

    def _require_supported_platform(self) -> None:
        if not self._platform_check().passed:
            raise RuntimeOperationError(
                "PLATFORM_UNSUPPORTED",
                "目前只支援 WSL2／Ubuntu 24.04／x86_64。",
            )

    def _require_runtime_paths_ignored(self) -> None:
        check = self._git_ignore_check()
        if not check.passed:
            raise RuntimeOperationError("GIT_IGNORE_MISSING", check.message)

    def _require_managed_paths_safe(self) -> None:
        gateway_venv = self.project_root / ".venv"
        if gateway_venv.is_symlink() or (
            gateway_venv.exists() and not gateway_venv.is_dir()
        ):
            raise RuntimeOperationError(
                "MANAGED_PATH_INVALID",
                "Gateway .venv 必須是非 symlink 目錄。",
            )
        if self.state_dir.is_symlink() or (
            self.state_dir.exists() and not self.state_dir.is_dir()
        ):
            raise RuntimeOperationError(
                "MANAGED_PATH_INVALID",
                "runtime state root 必須是非 symlink 目錄。",
            )
        for name in (
            "cache",
            "cache/audit",
            "cache/comfyui",
            "comfy-data",
            "comfyui",
            "home",
            "logs",
            "models",
            "python",
            "staging",
            "state",
            "tools",
        ):
            path = self.state_dir / name
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise RuntimeOperationError(
                    "MANAGED_PATH_INVALID",
                    f"runtime managed path {name} 必須是非 symlink 目錄。",
                )

    def _git_ignore_check(self) -> RuntimeCheck:
        probes: list[Path] = [self.project_root / ".venv/.fpmvp-ignore-probe"]
        if self.state_dir.is_relative_to(self.project_root):
            probes.append(self.state_dir / ".fpmvp-ignore-probe")
        for probe in probes:
            try:
                relative = probe.relative_to(self.project_root).as_posix()
            except ValueError:
                continue
            try:
                ignored = self.runner.run(
                    [
                        "git",
                        "-C",
                        str(self.project_root),
                        "check-ignore",
                        "--quiet",
                        "--no-index",
                        "--",
                        relative,
                    ],
                    cwd=self.project_root,
                    env=self._git_audit_env(),
                )
            except (RuntimeOperationError, OSError):
                ignored = CommandResult(1)
            if ignored.returncode != 0:
                return RuntimeCheck(
                    "git-ignore",
                    "fail",
                    "GIT_IGNORE_MISSING",
                    f"{relative.rsplit('/', 1)[0]} 未被 Git ignore；安裝前停止。",
                )
        return RuntimeCheck(
            "git-ignore",
            "pass",
            "GIT_IGNORE_OK",
            "Gateway venv 與 runtime state 均由 Git ignore 排除。",
        )

    def _state_writable_check(self) -> RuntimeCheck:
        anchor = self._existing_anchor(self.state_dir)
        ok = (
            not self.state_dir.is_symlink()
            and anchor.is_dir()
            and os.access(anchor, os.W_OK | os.X_OK)
        )
        return RuntimeCheck(
            "state-writable",
            "pass" if ok else "fail",
            "STATE_WRITABLE" if ok else "STATE_NOT_WRITABLE",
            (
                "runtime state filesystem 可寫。"
                if ok
                else "runtime state filesystem 不可寫或 path 是 symlink。"
            ),
        )

    def _managed_paths_check(self) -> RuntimeCheck:
        try:
            self._require_managed_paths_safe()
        except RuntimeOperationError as exc:
            return RuntimeCheck(
                "managed-paths",
                "fail",
                exc.code,
                exc.message,
            )
        return RuntimeCheck(
            "managed-paths",
            "pass",
            "MANAGED_PATHS_OK",
            "Gateway venv 與 runtime managed roots 均為非 symlink 目錄。",
        )

    def _require_install_stopped(self) -> None:
        records = self._load_processes(required=False)
        if any(self._process_matches(record) for record in records.values()):
            raise RuntimeOperationError(
                "RUNTIME_RUNNING",
                "install 前請先執行 runtime stop。",
            )
        for port, path in (
            (self.lock.gateway.port, "/api/v1/gateway/status"),
            (self.lock.comfyui.port, "/system_stats"),
        ):
            if self._http_health(port, path) != "free":
                raise RuntimeOperationError(
                    "RUNTIME_PORT_BUSY",
                    "install 不會修改正在執行或占用固定 port 的環境。",
                )

    def _require_python_version(
        self,
        python_path: Path,
        expected: str,
        label: str,
    ) -> None:
        check = self._python_check(python_path, expected, label)
        if not check.passed:
            raise RuntimeOperationError("PYTHON_VERSION_MISMATCH", check.message)

    def _gateway_argv(self, config: RuntimeConfig) -> tuple[str, ...]:
        return (
            str(self.project_root / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "app.gateway_main:app",
            "--app-dir",
            str(self.project_root / "backend"),
            "--host",
            self.lock.gateway.host,
            "--port",
            str(config.gateway_port),
            "--workers",
            "1",
        )

    def _comfy_argv(self, config: RuntimeConfig) -> tuple[str, ...]:
        return (
            str(config.comfyui_python),
            str(config.comfyui_root / "main.py"),
            "--base-directory",
            str(self.data_root),
            "--listen",
            self.lock.comfyui.host,
            "--port",
            str(config.comfyui_port),
            "--disable-auto-launch",
            "--disable-all-custom-nodes",
            "--whitelist-custom-nodes",
            "ComfyUI-GGUF",
            "--disable-api-nodes",
            "--input-directory",
            str(self.data_root / "input"),
            "--output-directory",
            str(self.data_root / "output"),
            "--temp-directory",
            str(self.data_root / "temp"),
            "--user-directory",
            str(self.data_root / "user"),
            "--database-url",
            "sqlite:///:memory:",
            "--extra-model-paths-config",
            str(self.extra_models_path),
        )

    def _default_comfy_python(
        self,
        root: Path,
        mode: RuntimeMode,
    ) -> Path:
        if mode is RuntimeMode.MANAGED:
            return root / ".venv/bin/python"
        for candidate in (root / ".venv/bin/python", root / "venv/bin/python"):
            if candidate.exists():
                return candidate.resolve()
        return root / "venv/bin/python"

    def _managed_python_executable(self, version: str) -> Path:
        major_minor = ".".join(version.split(".")[:2])
        return (
            self.state_dir
            / "python"
            / f"cpython-{version}-linux-x86_64-gnu"
            / "bin"
            / f"python{major_minor}"
        )

    def _write_managed_marker(self, root: Path) -> None:
        marker = root / ".git" / _MANAGED_MARKER
        self._atomic_json(
            marker,
            {
                "schema_version": 1,
                "owner": "final-project-mvp",
                "comfyui_commit": self.lock.comfyui.commit,
                "gguf_commit": self.lock.custom_nodes[0].commit,
            },
        )

    def _validate_managed_marker(self, root: Path) -> None:
        marker = root / ".git" / _MANAGED_MARKER
        try:
            payload = json.loads(marker.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeOperationError(
                "MANAGED_OWNERSHIP_MISSING",
                "existing managed path 缺少 ownership marker；不會修改。",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("owner") != "final-project-mvp"
            or payload.get("comfyui_commit") != self.lock.comfyui.commit
            or payload.get("gguf_commit") != self.lock.custom_nodes[0].commit
        ):
            raise RuntimeOperationError(
                "MANAGED_OWNERSHIP_MISMATCH",
                "managed ownership marker 不符合 runtime lock。",
            )

    def _write_extra_model_paths(
        self,
        model_root: Path,
        comfyui_root: Path,
    ) -> None:
        self._atomic_bytes(
            self.extra_models_path,
            self._extra_model_paths_bytes(model_root, comfyui_root),
        )

    @staticmethod
    def _extra_model_paths_bytes(
        model_root: Path,
        comfyui_root: Path,
    ) -> bytes:
        encoded_root = json.dumps(str(model_root.resolve()), ensure_ascii=False)
        encoded_comfy_root = json.dumps(
            str(comfyui_root.resolve()),
            ensure_ascii=False,
        )
        lines = [
            "final_project_models:",
            f"  base_path: {encoded_root}",
            "  is_default: true",
            "  diffusion_models: |",
            "    unet",
            "    diffusion_models",
            "  text_encoders: clip",
            "  vae: vae",
            "  upscale_models: upscale_models",
            "final_project_code:",
            f"  base_path: {encoded_comfy_root}",
            "  is_default: true",
            "  custom_nodes: custom_nodes",
        ]
        return ("\n".join(lines) + "\n").encode()

    def _child_env(self, config: RuntimeConfig) -> dict[str, str]:
        """回傳不含 Codex/OpenAI secrets 的最小 child environment。"""

        del config
        allowed = {
            key: value
            for key, value in self.environ.items()
            if key
            in {
                "HOME",
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "LANG",
                "LC_ALL",
                "NO_PROXY",
                "PATH",
                "REQUESTS_CA_BUNDLE",
                "SSL_CERT_DIR",
                "SSL_CERT_FILE",
                "USER",
                "WSL_DISTRO_NAME",
                "WSL_INTEROP",
                "https_proxy",
                "http_proxy",
                "no_proxy",
            }
        }
        allowed["PYTHONUNBUFFERED"] = "1"
        allowed["PYTHONDONTWRITEBYTECODE"] = "1"
        return allowed

    def _audit_env(self) -> dict[str, str]:
        allowed = self._child_env(self._planned_default_config())
        allowed["HOME"] = str(self.state_dir / "home")
        allowed["XDG_CACHE_HOME"] = str(self.state_dir / "cache/audit")
        return allowed

    def _git_audit_env(self) -> dict[str, str]:
        allowed = self._audit_env()
        allowed["GIT_OPTIONAL_LOCKS"] = "0"
        return allowed

    def _gateway_env(
        self,
        config: RuntimeConfig,
        *,
        comfy_enabled: bool,
    ) -> dict[str, str]:
        allowed = self._child_env(config)
        for name in ("CODEX_HOME", "OPENAI_API_KEY"):
            value = self.environ.get(name)
            if value:
                allowed[name] = value
        allowed["STORYBOARD_WORKFLOW_COMFYUI_BASE_URL"] = (
            f"http://127.0.0.1:{config.comfyui_port}"
            if comfy_enabled
            else "http://127.0.0.1:1"
        )
        return allowed

    def _comfy_env(self) -> dict[str, str]:
        allowed = self._child_env(self._planned_default_config())
        runtime_home = self.state_dir / "home"
        runtime_cache = self.state_dir / "cache/comfyui"
        for path in (self.state_dir / "cache", runtime_home, runtime_cache):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise RuntimeOperationError(
                    "MANAGED_PATH_INVALID",
                    "ComfyUI HOME/cache 必須是非 symlink runtime 目錄。",
                )
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        allowed["HOME"] = str(runtime_home)
        allowed["XDG_CACHE_HOME"] = str(runtime_cache)
        allowed["HF_HUB_DISABLE_TELEMETRY"] = "1"
        allowed["DO_NOT_TRACK"] = "1"
        return allowed

    def _prepare_state_dirs(self) -> None:
        if self.state_dir.is_symlink():
            raise RuntimeOperationError(
                "STATE_OWNERSHIP_INVALID",
                "runtime state dir 不可是 symlink。",
            )
        created = not self.state_dir.exists()
        if created:
            self.state_dir.mkdir(mode=0o700, parents=True)
            os.chmod(self.state_dir, 0o700)
        elif not self.state_dir.is_dir():
            raise RuntimeOperationError(
                "STATE_OWNERSHIP_INVALID",
                "runtime state path 不是目錄。",
            )
        if self.state_marker_path.exists():
            try:
                marker = json.loads(self.state_marker_path.read_bytes())
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeOperationError(
                    "STATE_OWNERSHIP_INVALID",
                    "runtime state ownership marker 損壞。",
                ) from exc
            if (
                not isinstance(marker, dict)
                or marker.get("owner") != "final-project-mvp"
                or marker.get("project_root") != str(self.project_root)
            ):
                raise RuntimeOperationError(
                    "STATE_OWNERSHIP_INVALID",
                    "runtime state ownership marker 不符合目前專案。",
                )
        else:
            if not created and any(self.state_dir.iterdir()):
                raise RuntimeOperationError(
                    "STATE_OWNERSHIP_MISSING",
                    "既有 state dir 缺少 ownership marker；不會 chmod 或寫入。",
                )
            self._atomic_json(
                self.state_marker_path,
                {
                    "schema_version": 1,
                    "owner": "final-project-mvp",
                    "project_root": str(self.project_root),
                },
            )
        (self.state_dir / "state").mkdir(mode=0o700, parents=True, exist_ok=True)

    def _prepare_runtime_data(self) -> None:
        if self.data_root.is_symlink():
            raise RuntimeOperationError(
                "RUNTIME_DATA_PATH_INVALID",
                "runtime comfy-data root 不可是 symlink。",
            )
        for name in ("input", "output", "temp", "user", "models", "custom_nodes"):
            path = self.data_root / name
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise RuntimeOperationError(
                    "RUNTIME_DATA_PATH_INVALID",
                    f"runtime comfy-data/{name} 必須是非 symlink 目錄。",
                )
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        custom_nodes_check = self._runtime_custom_nodes_check()
        if not custom_nodes_check.passed:
            raise RuntimeOperationError(
                custom_nodes_check.code,
                custom_nodes_check.message,
            )

    @contextlib.contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self._prepare_state_dirs()
        descriptor = os.open(
            self.lock_path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise RuntimeOperationError(
                "RUNTIME_BUSY",
                "另一個 runtime install/start/stop 正在執行。",
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _load_config(self, *, required: bool = True) -> RuntimeConfig | None:
        try:
            payload = json.loads(self.config_path.read_bytes())
        except FileNotFoundError:
            if not required:
                return None
            raise RuntimeOperationError(
                "RUNTIME_NOT_INSTALLED",
                "尚未執行 runtime install。",
            ) from None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeOperationError(
                "RUNTIME_CONFIG_INVALID",
                "runtime config 損壞或無法讀取。",
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "instance_id",
            "project_root",
            "comfy_mode",
            "models_mode",
            "comfyui_root",
            "comfyui_python",
            "model_root",
            "gateway_port",
            "comfyui_port",
        }:
            raise RuntimeOperationError(
                "RUNTIME_CONFIG_INVALID",
                "runtime config schema 不相容。",
            )
        if payload.get("schema_version") != _CONFIG_SCHEMA:
            raise RuntimeOperationError(
                "RUNTIME_CONFIG_INVALID",
                "runtime config schema 版本不相容。",
            )
        try:
            project_root = self._safe_config_path(payload["project_root"])
            if project_root != self.project_root:
                raise ValueError
            instance_id = str(payload["instance_id"])
            if len(instance_id) != 32 or any(
                character not in "0123456789abcdef" for character in instance_id
            ):
                raise ValueError
            gateway_port = self._safe_port(payload["gateway_port"])
            comfyui_port = self._safe_port(payload["comfyui_port"])
            if (
                gateway_port != self.lock.gateway.port
                or comfyui_port != self.lock.comfyui.port
            ):
                raise ValueError
            return RuntimeConfig(
                instance_id=instance_id,
                project_root=project_root,
                comfy_mode=RuntimeMode(str(payload["comfy_mode"])),
                models_mode=ModelsMode(str(payload["models_mode"])),
                comfyui_root=self._safe_config_path(payload["comfyui_root"]),
                comfyui_python=self._safe_config_path(payload["comfyui_python"]),
                model_root=self._safe_config_path(payload["model_root"]),
                gateway_port=gateway_port,
                comfyui_port=comfyui_port,
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeOperationError(
                "RUNTIME_CONFIG_INVALID",
                "runtime config 欄位不安全。",
            ) from exc

    def _planned_default_config(self) -> RuntimeConfig:
        return RuntimeConfig(
            instance_id="0" * 32,
            project_root=self.project_root,
            comfy_mode=RuntimeMode.MANAGED,
            models_mode=ModelsMode.MANAGED,
            comfyui_root=self.managed_comfy_root,
            comfyui_python=self.managed_comfy_root / ".venv/bin/python",
            model_root=self.managed_model_root,
            gateway_port=self.lock.gateway.port,
            comfyui_port=self.lock.comfyui.port,
        )

    def _load_processes(
        self,
        *,
        required: bool = False,
    ) -> dict[str, ProcessRecord]:
        try:
            payload = json.loads(self.process_path.read_bytes())
        except FileNotFoundError:
            if required:
                raise RuntimeOperationError(
                    "PROCESS_STATE_MISSING",
                    "找不到 runtime process state。",
                ) from None
            return {}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeOperationError(
                "PROCESS_STATE_INVALID",
                "runtime process state 損壞。",
            ) from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "processes"}
            or payload.get("schema_version") != _PROCESS_SCHEMA
            or not isinstance(payload.get("processes"), dict)
        ):
            raise RuntimeOperationError(
                "PROCESS_STATE_INVALID",
                "runtime process state schema 不相容。",
            )
        records: dict[str, ProcessRecord] = {}
        for name, raw in cast(dict[str, object], payload["processes"]).items():
            if (
                name not in {"gateway", "comfyui"}
                or not isinstance(raw, dict)
                or set(raw)
                != {
                    "pid",
                    "pgid",
                    "start_ticks",
                    "boot_id",
                    "executable",
                    "argv_sha256",
                    "comfy_enabled",
                }
                or not isinstance(raw.get("comfy_enabled"), bool)
            ):
                raise RuntimeOperationError(
                    "PROCESS_STATE_INVALID",
                    "runtime process record 不合法。",
                )
            try:
                record = ProcessRecord(
                    name=name,
                    pid=int(raw["pid"]),
                    pgid=int(raw["pgid"]),
                    start_ticks=str(raw["start_ticks"]),
                    boot_id=str(raw["boot_id"]),
                    executable=str(raw["executable"]),
                    argv_sha256=str(raw["argv_sha256"]),
                    comfy_enabled=raw["comfy_enabled"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeOperationError(
                    "PROCESS_STATE_INVALID",
                    "runtime process record 欄位不合法。",
                ) from exc
            if (
                record.pid < 2
                or record.pgid < 2
                or not record.start_ticks.isdigit()
                or not record.boot_id
                or not Path(record.executable).is_absolute()
                or len(record.argv_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in record.argv_sha256
                )
            ):
                raise RuntimeOperationError(
                    "PROCESS_STATE_INVALID",
                    "runtime process PID 不合法。",
                )
            records[name] = record
        return records

    def _write_processes(self, records: Mapping[str, ProcessRecord]) -> None:
        try:
            self._atomic_json(
                self.process_path,
                {
                    "schema_version": _PROCESS_SCHEMA,
                    "processes": {
                        name: {
                            "pid": record.pid,
                            "pgid": record.pgid,
                            "start_ticks": record.start_ticks,
                            "boot_id": record.boot_id,
                            "executable": record.executable,
                            "argv_sha256": record.argv_sha256,
                            "comfy_enabled": record.comfy_enabled,
                        }
                        for name, record in sorted(records.items())
                    },
                },
            )
        except OSError as exc:
            raise RuntimeOperationError(
                "PROCESS_STATE_WRITE_FAILED",
                "無法安全寫入 runtime process state。",
            ) from exc

    def _record_process(
        self,
        name: str,
        pid: int,
        argv: Sequence[str],
    ) -> ProcessRecord:
        try:
            start_ticks = self._proc_start_ticks(pid)
            boot_id = self._boot_id()
            executable = str(Path(argv[0]).resolve())
            pgid = os.getpgid(pid)
        except (OSError, RuntimeOperationError) as exc:
            raise RuntimeOperationError(
                "PROCESS_RECORD_FAILED",
                f"無法記錄剛啟動的 {name} process identity。",
            ) from exc
        if pgid != pid:
            raise RuntimeOperationError(
                "PROCESS_RECORD_FAILED",
                f"{name} 未建立獨立 process group。",
            )
        return ProcessRecord(
            name=name,
            pid=pid,
            pgid=pgid,
            start_ticks=start_ticks,
            boot_id=boot_id,
            executable=executable,
            argv_sha256=self._argv_digest(argv),
        )

    def _process_matches(self, record: ProcessRecord) -> bool:
        try:
            if self._boot_id() != record.boot_id:
                return False
            if self._proc_start_ticks(record.pid) != record.start_ticks:
                return False
            if os.getpgid(record.pid) != record.pgid or record.pgid != record.pid:
                return False
            executable = Path(f"/proc/{record.pid}/exe").resolve()
            if str(executable) != record.executable:
                return False
            raw_argv = Path(f"/proc/{record.pid}/cmdline").read_bytes()
            argv = tuple(
                item.decode(errors="surrogateescape")
                for item in raw_argv.rstrip(b"\x00").split(b"\x00")
            )
            return self._argv_digest(argv) == record.argv_sha256
        except (OSError, UnicodeError):
            return False

    def _stop_record(
        self,
        record: ProcessRecord,
        *,
        timeout_seconds: float,
    ) -> bool:
        if not self._process_matches(record):
            return not Path(f"/proc/{record.pid}").exists()
        try:
            os.killpg(record.pgid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            return False
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not Path(f"/proc/{record.pid}").exists():
                return True
            time.sleep(0.05)
        return False

    @staticmethod
    def _terminate_fresh_process(pid: int) -> bool:
        """回收本次 spawn 剛回傳、但尚未能持久化 identity 的 process。"""

        try:
            if pid < 2 or os.getpgid(pid) != pid:
                return False
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            return False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with contextlib.suppress(ChildProcessError):
                waited, _ = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    return True
            if not Path(f"/proc/{pid}").exists():
                return True
            time.sleep(0.05)
        return False

    @staticmethod
    def _proc_start_ticks(pid: int) -> str:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        closing = raw.rfind(")")
        if closing < 0:
            raise RuntimeOperationError(
                "PROCESS_IDENTITY_INVALID",
                "無法讀取 Linux process identity。",
            )
        fields_after_name = raw[closing + 2 :].split()
        return fields_after_name[19]

    @staticmethod
    def _boot_id() -> str:
        return (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        )

    @staticmethod
    def _argv_digest(argv: Sequence[str]) -> str:
        digest = hashlib.sha256()
        digest.update(b"\x00".join(item.encode() for item in argv))
        return digest.hexdigest()

    @staticmethod
    def _http_health(port: int, path: str) -> str:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            response.read(1024)
        except ConnectionRefusedError:
            return "free"
        except (OSError, http.client.HTTPException):
            return "occupied"
        finally:
            connection.close()
        return "healthy" if 200 <= response.status < 300 else "occupied"

    def _wait_for_health(
        self,
        port: int,
        path: str,
        timeout_seconds: float,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._http_health(port, path) == "healthy":
                return True
            time.sleep(0.25)
        return False

    def _service_owned(self, port: int, record: ProcessRecord) -> bool:
        if not self._process_matches(record):
            return False
        socket_inodes = self._listen_socket_inodes(port)
        if not socket_inodes:
            return False
        for process_dir in Path("/proc").glob("[0-9]*"):
            try:
                pid = int(process_dir.name)
                if os.getpgid(pid) != record.pgid:
                    continue
                for descriptor in (process_dir / "fd").iterdir():
                    target = os.readlink(descriptor)
                    if (
                        target.startswith("socket:[")
                        and target.endswith("]")
                        and target[8:-1] in socket_inodes
                    ):
                        return True
            except (OSError, ValueError):
                continue
        return False

    @staticmethod
    def _listen_socket_inodes(port: int) -> set[str]:
        expected_port = f"{port:04X}"
        inodes: set[str] = set()
        for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
            try:
                lines = table.read_text(encoding="ascii").splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "0A":
                    continue
                local = fields[1]
                if ":" not in local:
                    continue
                address, raw_port = local.rsplit(":", 1)
                if raw_port != expected_port:
                    continue
                if address not in {
                    "0100007F",
                    "00000000000000000000000001000000",
                    "0000000000000000FFFF00000100007F",
                }:
                    continue
                inodes.add(fields[9])
        return inodes

    def _require_owned_or_free(
        self,
        name: str,
        health: str,
        record: ProcessRecord | None,
        *,
        port: int,
    ) -> str:
        if health == "free":
            if record is not None and self._process_matches(record):
                return "owned-starting"
            return "free"
        if (
            health == "healthy"
            and record is not None
            and self._service_owned(port, record)
        ):
            return "owned"
        code = (
            f"{name}_PORT_NOT_OWNED" if health == "healthy" else f"{name}_PORT_CONFLICT"
        )
        raise RuntimeOperationError(
            code,
            f"{name} port 已被非 runtime-owned process 占用；不會採用或停止它。",
        )

    def _run_checked(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        effective_env = env
        if (
            effective_env is None
            and argv
            and Path(argv[0]).resolve() == (self.state_dir / "tools/uv").resolve()
        ):
            effective_env = self._uv_env()
        try:
            result = self.runner.run(argv, cwd=cwd, env=effective_env)
        except FileNotFoundError as exc:
            raise RuntimeOperationError(
                "COMMAND_NOT_FOUND",
                f"找不到本機命令：{Path(argv[0]).name}。",
            ) from exc
        except PermissionError as exc:
            raise RuntimeOperationError(
                "COMMAND_PERMISSION_DENIED",
                f"沒有權限執行本機命令：{Path(argv[0]).name}。",
            ) from exc
        except OSError as exc:
            raise RuntimeOperationError(
                "COMMAND_EXEC_FAILED",
                f"無法安全執行本機命令：{Path(argv[0]).name}。",
            ) from exc
        if result.returncode != 0:
            raise RuntimeOperationError(
                "COMMAND_FAILED",
                f"本機命令失敗：{Path(argv[0]).name}。",
            )
        return result

    def _spawn_checked(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_path: Path,
    ) -> int:
        try:
            return self.runner.spawn(
                argv,
                cwd=cwd,
                env=env,
                log_path=log_path,
            )
        except RuntimeOperationError:
            raise
        except FileNotFoundError as exc:
            raise RuntimeOperationError(
                "COMMAND_NOT_FOUND",
                f"找不到服務命令：{Path(argv[0]).name}。",
            ) from exc
        except PermissionError as exc:
            raise RuntimeOperationError(
                "COMMAND_PERMISSION_DENIED",
                f"沒有權限啟動服務命令：{Path(argv[0]).name}。",
            ) from exc
        except OSError as exc:
            raise RuntimeOperationError(
                "PROCESS_SPAWN_FAILED",
                f"無法安全啟動服務：{Path(argv[0]).name}。",
            ) from exc

    def _uv_env(self) -> dict[str, str]:
        environment = dict(self.environ)
        environment["UV_CACHE_DIR"] = str(self.state_dir / "cache/uv")
        environment["UV_PYTHON_INSTALL_DIR"] = str(self.state_dir / "python")
        environment["UV_TOOL_DIR"] = str(self.state_dir / "tools/installed")
        environment["UV_NO_MODIFY_PATH"] = "1"
        return environment

    def _ensure_uv(self) -> str:
        self._progress(f"準備釘定 uv {self.lock.uv.version}…")
        try:
            bootstrap = self.uv_bootstrap_factory(
                self.state_dir / "tools",
                self.lock.uv,
            )
            executable = bootstrap.ensure()
        except BootstrapError as exc:
            raise RuntimeOperationError(
                "UV_BOOTSTRAP_FAILED",
                str(exc),
            ) from exc
        version = self._run_checked([str(executable), "--version"]).stdout.strip()
        tokens = version.split()
        if len(tokens) < 2 or tokens[0] != "uv" or tokens[1] != self.lock.uv.version:
            raise RuntimeOperationError(
                "UV_VERSION_MISMATCH",
                "bootstrap uv 版本與 runtime lock 不一致。",
            )
        return str(executable)

    def _progress(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def _report(
        self,
        command: str,
        *,
        dry_run: bool,
        mode: RuntimeMode | None = None,
        models_mode: ModelsMode | None = None,
    ) -> RuntimeReport:
        return RuntimeReport(
            command=command,
            dry_run=dry_run,
            ok=True,
            overall="ready",
            mode=mode.value if mode is not None else None,
            models_mode=models_mode.value if models_mode is not None else None,
            agents=[
                {
                    "name": agent.name,
                    "status": agent.status,
                    "blocks_start": agent.blocks_start,
                }
                for agent in self.lock.agents
            ],
        )

    def _urls(self, config: RuntimeConfig) -> dict[str, str]:
        return {
            "gateway": f"http://127.0.0.1:{config.gateway_port}",
            "comfyui": f"http://127.0.0.1:{config.comfyui_port}",
        }

    @staticmethod
    def _safe_port(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError
        if value < 1024 or value > 65535:
            raise ValueError
        return value

    @staticmethod
    def _safe_config_path(value: object) -> Path:
        if (
            not isinstance(value, str)
            or not value
            or "\x00" in value
            or value.startswith("\\\\")
        ):
            raise ValueError
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError
        return path.resolve()

    @staticmethod
    def _safe_cli_path(value: str, label: str) -> Path:
        if not value or "\x00" in value or value.startswith("\\\\"):
            raise RuntimeOperationError(
                "UNSAFE_PATH",
                f"{label} 必須是 WSL/Linux 絕對路徑，不接受 UNC path。",
            )
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise RuntimeOperationError(
                "UNSAFE_PATH",
                f"{label} 必須是絕對路徑。",
            )
        return path.resolve()

    def _require_path_argument(self, value: Path | None, label: str) -> Path:
        if value is None:
            raise RuntimeOperationError(
                "PATH_REQUIRED",
                f"必須明確提供 {label}。",
            )
        return self._safe_cli_path(str(value), label)

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
        RuntimeManager._atomic_bytes(path, encoded)

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
