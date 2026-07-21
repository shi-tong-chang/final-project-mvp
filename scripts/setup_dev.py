#!/usr/bin/env python3
"""以 runtime lock 建立不含 ComfyUI／模型的專案開發環境。"""

from __future__ import annotations

import argparse
import os
import platform
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.bootstrap import BootstrapError, PinnedUvBootstrap  # noqa: E402
from runtime.spec import (  # noqa: E402
    RuntimeLock,
    RuntimeLockError,
    ToolLock,
    load_runtime_lock,
)

_LINUX_X86_64_NAMES = frozenset({"amd64", "x86_64"})


class SetupError(RuntimeError):
    """可安全顯示給開發者的環境建立錯誤。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CommandResult:
    """固定 argv subprocess 的結果。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    """可由 unit test 取代的唯一 subprocess 邊界。"""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandResult:
        """以 shell=False 執行 argv。"""


class UvBootstrap(Protocol):
    """可由 unit test 取代的釘定 uv bootstrap。"""

    def ensure(self) -> Path:
        """回傳已驗證的 uv executable。"""


class SubprocessCommandRunner:
    """正式執行固定 argv，且不經過 shell。"""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandResult:
        """執行單一 child process 並擷取 UTF-8 輸出。"""

        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True, slots=True)
class SetupAction:
    """dry-run 與正式執行共用的固定命令。"""

    action_id: str
    argv: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SetupPlan:
    """開發環境 bootstrap 的完整、可稽核計畫。"""

    uv_version: str
    python_version: str
    actions: tuple[SetupAction, ...]


class DevSetup:
    """只管理 repository-local `.python/` 與 `.venv/`。"""

    def __init__(
        self,
        project_root: Path = PROJECT_ROOT,
        *,
        lock: RuntimeLock | None = None,
        runner: CommandRunner | None = None,
        uv_bootstrap_factory: Callable[[Path, ToolLock], UvBootstrap] = (
            PinnedUvBootstrap
        ),
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.lock = lock or load_runtime_lock(
            self.project_root / "runtime/runtime-lock.json",
            models_path=self.project_root / "runtime/models.lock.json",
        )
        self.runner = runner or SubprocessCommandRunner()
        self.uv_bootstrap_factory = uv_bootstrap_factory
        self.environ = dict(os.environ if environ is None else environ)
        self.python_root = self.project_root / ".python"
        self.venv_root = self.project_root / ".venv"
        self.tools_root = self.python_root / "tools"
        self.uv_binary = self.tools_root / "uv"
        self.python_binary = self._managed_python_executable(self.lock.gateway.python)

    def plan(self) -> SetupPlan:
        """建立不寫檔、不執行 subprocess 的固定 action plan。"""

        actions = (
            SetupAction(
                "verify-python-ignore",
                self._git_ignore_argv(".python/.fpmvp-dev-ignore-probe"),
            ),
            SetupAction(
                "verify-venv-ignore",
                self._git_ignore_argv(".venv/.fpmvp-dev-ignore-probe"),
            ),
            SetupAction(
                "bootstrap-uv",
                detail=(
                    f"下載 uv {self.lock.uv.version} 的 lock-pinned HTTPS asset，"
                    "驗證 SHA-256 後只解出 uv。"
                ),
            ),
            SetupAction("verify-uv", (str(self.uv_binary), "--version")),
            SetupAction(
                "install-python",
                (
                    str(self.uv_binary),
                    "python",
                    "install",
                    "--install-dir",
                    str(self.python_root),
                    "--no-bin",
                    self.lock.gateway.python,
                ),
            ),
            SetupAction(
                "verify-python",
                (str(self.python_binary), "--version"),
            ),
            SetupAction(
                "sync-dev-dependencies",
                (
                    str(self.uv_binary),
                    "sync",
                    "--locked",
                    "--dev",
                    "--python",
                    str(self.python_binary),
                ),
            ),
            SetupAction(
                "verify-venv-python",
                (str(self.venv_root / "bin/python"), "--version"),
            ),
        )
        return SetupPlan(
            uv_version=self.lock.uv.version,
            python_version=self.lock.gateway.python,
            actions=actions,
        )

    def setup(self, *, dry_run: bool = False) -> SetupPlan:
        """建立 locked dev 環境；dry-run 僅回傳 plan。"""

        plan = self.plan()
        if dry_run:
            return plan

        self._require_supported_platform()
        self._require_project_inputs()
        self._require_managed_paths_safe()
        self._require_git_ignored()

        try:
            uv_binary = self.uv_bootstrap_factory(
                self.tools_root,
                self.lock.uv,
            ).ensure()
        except BootstrapError as exc:
            raise SetupError("UV_BOOTSTRAP_FAILED", str(exc)) from exc
        self._require_expected_uv(uv_binary)
        self._require_managed_paths_safe()

        uv_environment = self._uv_environment()
        actions = {action.action_id: action for action in plan.actions}
        self._require_uv_version(uv_environment)
        self._run_checked(
            actions["install-python"].argv,
            label=f"安裝 Python {self.lock.gateway.python}",
            env=uv_environment,
        )
        self._require_python_binary()
        self._require_python_version(
            self.python_binary,
            env=uv_environment,
            code="PYTHON_VERSION_MISMATCH",
        )
        self._run_checked(
            actions["sync-dev-dependencies"].argv,
            label="依 uv.lock 同步 dev dependencies",
            env=uv_environment,
        )
        self._require_venv_python(uv_environment)
        return plan

    def _managed_python_executable(self, version: str) -> Path:
        major_minor = ".".join(version.split(".")[:2])
        return (
            self.python_root
            / f"cpython-{version}-linux-x86_64-gnu"
            / "bin"
            / f"python{major_minor}"
        )

    def _require_supported_platform(self) -> None:
        machine = platform.machine().lower()
        if platform.system() != "Linux" or machine not in _LINUX_X86_64_NAMES:
            raise SetupError(
                "PLATFORM_UNSUPPORTED",
                "開發環境 bootstrap 目前只支援 Linux／WSL x86_64。",
            )

    def _require_project_inputs(self) -> None:
        if not self.project_root.is_dir():
            raise SetupError("PROJECT_ROOT_INVALID", "找不到 repository 根目錄。")
        for relative in (
            "pyproject.toml",
            "uv.lock",
            "runtime/runtime-lock.json",
            "runtime/models.lock.json",
        ):
            path = self.project_root / relative
            if path.is_symlink() or not path.is_file():
                raise SetupError(
                    "PROJECT_INPUT_INVALID",
                    f"{relative} 必須是 repository 內的非 symlink 檔案。",
                )

    def _require_managed_paths_safe(self) -> None:
        directories = (
            self.python_root,
            self.tools_root,
            self.python_root / "cache",
            self.python_root / "cache/uv",
            self.tools_root / "installed",
            self.python_binary.parent.parent,
            self.python_binary.parent,
            self.venv_root,
            self.venv_root / "bin",
        )
        for path in directories:
            self._require_managed_directory(path)
        for path, label in (
            (self.uv_binary, "uv executable"),
            (self.python_binary, "managed Python executable"),
        ):
            self._require_path_inside_project(path)
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise SetupError(
                    "MANAGED_PATH_INVALID",
                    f"{label} 必須是非 symlink regular file。",
                )

    def _require_managed_directory(self, path: Path) -> None:
        self._require_path_inside_project(path)
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            relative = path.relative_to(self.project_root).as_posix()
            raise SetupError(
                "MANAGED_PATH_INVALID",
                f"{relative} 必須是非 symlink 目錄。",
            )

    def _require_path_inside_project(self, path: Path) -> None:
        try:
            path.relative_to(self.project_root)
        except ValueError as exc:
            raise SetupError(
                "MANAGED_PATH_INVALID",
                "開發環境 managed path 不在 repository 內。",
            ) from exc

    def _require_git_ignored(self) -> None:
        environment = self._git_environment()
        for relative in (
            ".python/.fpmvp-dev-ignore-probe",
            ".venv/.fpmvp-dev-ignore-probe",
        ):
            result = self._run(
                self._git_ignore_argv(relative),
                env=environment,
            )
            if result.returncode != 0:
                root = relative.split("/", 1)[0]
                if result.returncode != 1:
                    raise SetupError(
                        "GIT_IGNORE_CHECK_FAILED",
                        "無法以 Git 驗證 ignore 規則；請確認目前目錄是完整 clone。",
                    )
                raise SetupError(
                    "GIT_IGNORE_MISSING",
                    f"{root}/ 未被 repository 的 Git ignore 規則排除；停止安裝。",
                )

    def _git_ignore_argv(self, relative: str) -> tuple[str, ...]:
        return (
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            "-C",
            str(self.project_root),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            relative,
        )

    def _require_expected_uv(self, executable: Path) -> None:
        returned = Path(executable)
        if not returned.is_absolute():
            raise SetupError(
                "UV_PATH_INVALID",
                "uv bootstrap 必須回傳 `.python/tools/uv` 的絕對路徑。",
            )
        returned = Path(os.path.abspath(returned))
        if returned != self.uv_binary:
            raise SetupError(
                "UV_PATH_INVALID",
                "uv bootstrap 回傳的 executable 不在 `.python/tools/uv`。",
            )
        self._require_regular_executable(returned, "uv executable")

    def _require_uv_version(self, environment: Mapping[str, str]) -> None:
        result = self._run_checked(
            (str(self.uv_binary), "--version"),
            label="驗證 uv 版本",
            env=environment,
        )
        tokens = result.stdout.strip().split()
        if len(tokens) < 2 or tokens[0] != "uv" or tokens[1] != self.lock.uv.version:
            raise SetupError(
                "UV_VERSION_MISMATCH",
                f"uv 版本必須是 runtime lock 的 {self.lock.uv.version}。",
            )

    def _require_python_binary(self) -> None:
        self._require_managed_paths_safe()
        self._require_regular_executable(
            self.python_binary,
            "managed Python executable",
        )

    def _require_python_version(
        self,
        executable: Path,
        *,
        env: Mapping[str, str],
        code: str,
    ) -> None:
        result = self._run_checked(
            (str(executable), "--version"),
            label="驗證 Python 版本",
            env=env,
        )
        version_output = (result.stdout or result.stderr).strip()
        expected = f"Python {self.lock.gateway.python}"
        if version_output != expected:
            raise SetupError(
                code,
                f"開發環境 Python 必須是 runtime lock 的 {self.lock.gateway.python}。",
            )

    def _require_venv_python(self, environment: Mapping[str, str]) -> None:
        self._require_managed_directory(self.venv_root)
        self._require_managed_directory(self.venv_root / "bin")
        executable = self.venv_root / "bin/python"
        if executable.is_symlink():
            try:
                target = executable.resolve(strict=True)
                expected = self.python_binary.resolve(strict=True)
            except OSError as exc:
                raise SetupError(
                    "VENV_PYTHON_INVALID",
                    ".venv/bin/python 是無法解析的 symlink。",
                ) from exc
            if target != expected:
                raise SetupError(
                    "VENV_PYTHON_INVALID",
                    ".venv/bin/python 未指向 project-local managed Python。",
                )
        else:
            self._require_regular_executable(executable, ".venv Python executable")
        self._require_python_version(
            executable,
            env=environment,
            code="VENV_PYTHON_VERSION_MISMATCH",
        )

    @staticmethod
    def _require_regular_executable(path: Path, label: str) -> None:
        try:
            status = path.lstat()
        except OSError as exc:
            raise SetupError(
                "MANAGED_EXECUTABLE_INVALID",
                f"{label} 不存在或無法讀取。",
            ) from exc
        if not stat.S_ISREG(status.st_mode) or not status.st_mode & stat.S_IXUSR:
            raise SetupError(
                "MANAGED_EXECUTABLE_INVALID",
                f"{label} 必須是可執行的非 symlink regular file。",
            )

    def _run_checked(
        self,
        argv: Sequence[str],
        *,
        label: str,
        env: Mapping[str, str],
    ) -> CommandResult:
        result = self._run(argv, env=env)
        if result.returncode != 0:
            raise SetupError(
                "COMMAND_FAILED",
                f"{label}失敗（exit code {result.returncode}）。",
            )
        return result

    def _run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
    ) -> CommandResult:
        try:
            return self.runner.run(
                tuple(argv),
                cwd=self.project_root,
                env=env,
            )
        except FileNotFoundError as exc:
            raise SetupError(
                "COMMAND_NOT_FOUND",
                f"找不到必要命令：{Path(argv[0]).name}。",
            ) from exc
        except PermissionError as exc:
            raise SetupError(
                "COMMAND_PERMISSION_DENIED",
                f"沒有權限執行必要命令：{Path(argv[0]).name}。",
            ) from exc
        except OSError as exc:
            raise SetupError(
                "COMMAND_EXEC_FAILED",
                f"無法安全執行必要命令：{Path(argv[0]).name}。",
            ) from exc

    def _base_environment(self) -> dict[str, str]:
        allowed_names = {
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
        environment = {
            key: value for key, value in self.environ.items() if key in allowed_names
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    def _git_environment(self) -> dict[str, str]:
        environment = self._base_environment()
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        return environment

    def _uv_environment(self) -> dict[str, str]:
        environment = self._base_environment()
        environment.update(
            {
                "UV_CACHE_DIR": str(self.python_root / "cache/uv"),
                "UV_NO_CONFIG": "1",
                "UV_NO_MODIFY_PATH": "1",
                "UV_PROJECT_ENVIRONMENT": str(self.venv_root),
                "UV_PYTHON_INSTALL_DIR": str(self.python_root),
                "UV_TOOL_DIR": str(self.tools_root / "installed"),
            }
        )
        return environment


def build_parser() -> argparse.ArgumentParser:
    """建立只接受 `--dry-run` 的開發環境 CLI parser。"""

    parser = argparse.ArgumentParser(
        prog="setup_dev.py",
        description="建立 pinned Python 與 locked dev dependencies；不安裝 runtime。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只顯示固定計畫，不寫檔、不下載、不執行 subprocess。",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """執行開發環境 bootstrap 並回傳 process exit code。"""

    arguments = build_parser().parse_args(argv)
    try:
        setup = DevSetup(PROJECT_ROOT)
        plan = setup.setup(dry_run=arguments.dry_run)
    except RuntimeLockError as exc:
        print(f"錯誤 [RUNTIME_LOCK_INVALID]：{exc}", file=stderr)
        return 2
    except SetupError as exc:
        print(f"錯誤 [{exc.code}]：{exc.message}", file=stderr)
        return 2
    except OSError:
        print("錯誤 [LOCAL_IO_FAILED]：無法讀寫 project-local 開發環境。", file=stderr)
        return 2

    if arguments.dry_run:
        print(
            "dry-run：不寫檔、不下載、不執行 subprocess；固定計畫如下。",
            file=stdout,
        )
        for action in plan.actions:
            rendered = " ".join(action.argv) if action.argv else action.detail
            print(f"- {action.action_id}: {rendered}", file=stdout)
        return 0

    print(
        f"開發環境已就緒：Python {plan.python_version}、uv {plan.uv_version}。",
        file=stdout,
    )
    print("啟用方式：source .venv/bin/activate", file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
