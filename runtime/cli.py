"""`安裝環境`／`啟動` 可呼叫的 stdlib runtime CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from runtime.manager import (
    ModelsMode,
    RuntimeManager,
    RuntimeMode,
    RuntimeOperationError,
    RuntimeReport,
)
from runtime.spec import RuntimeLockError


def build_parser() -> argparse.ArgumentParser:
    """建立五命令 CLI；不從 `.env` 執行 shell code。"""

    parser = argparse.ArgumentParser(
        prog="fpmvp-runtime",
        description="Final Project MVP 的 WSL/Linux 本機 runtime 控制器。",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="覆寫 machine-local state；預設為專案 .runtime。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="輸出穩定 JSON schema。",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install", help="安裝／設定但不啟動服務。")
    _add_json_option(install)
    install.add_argument(
        "--comfy-mode",
        choices=tuple(RuntimeMode),
        type=RuntimeMode,
        default=RuntimeMode.AUTO,
    )
    install.add_argument(
        "--models-mode",
        choices=tuple(ModelsMode),
        type=ModelsMode,
        default=ModelsMode.AUTO,
    )
    install.add_argument("--comfyui-root", type=Path)
    install.add_argument("--comfyui-python", type=Path)
    install.add_argument("--model-root", type=Path)
    install.add_argument("--dry-run", action="store_true")

    preflight = commands.add_parser("preflight", help="唯讀檢查環境與模型。")
    _add_json_option(preflight)
    preflight.add_argument(
        "--full",
        action="store_true",
        help="逐顆重算 47GB SHA-256；預設只核對 exact bytes 與 install receipt。",
    )
    preflight.add_argument("--dry-run", action="store_true")

    start = commands.add_parser("start", help="啟動 loopback 服務。")
    _add_json_option(start)
    start.add_argument("--gateway-only", action="store_true")
    start.add_argument(
        "--no-open",
        action="store_true",
        help="不要 best-effort 開啟固定本機網站 URL。",
    )
    start.add_argument("--wait-seconds", type=_positive_float, default=120.0)
    start.add_argument("--dry-run", action="store_true")

    stop = commands.add_parser("stop", help="停止 runtime-owned 服務。")
    _add_json_option(stop)
    stop.add_argument("--timeout-seconds", type=_positive_float, default=15.0)
    stop.add_argument("--dry-run", action="store_true")

    status = commands.add_parser("status", help="快速唯讀狀態。")
    _add_json_option(status)
    status.add_argument("--dry-run", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """執行 CLI，回傳穩定 exit code。"""

    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        manager = RuntimeManager(
            arguments.project_root,
            state_dir=arguments.state_dir,
            progress=lambda message: print(message, file=stderr, flush=True),
        )
        report = _dispatch(manager, arguments)
    except (RuntimeOperationError, RuntimeLockError) as exc:
        code = getattr(exc, "code", "RUNTIME_LOCK_INVALID")
        message = getattr(exc, "message", str(exc))
        payload = {
            "schema": "fpmvp.runtime.v1",
            "command": getattr(arguments, "command", "unknown"),
            "ok": False,
            "changed": False,
            "dry_run": bool(getattr(arguments, "dry_run", False)),
            "overall": "failed",
            "error": {"code": code, "message": message},
        }
        _write_payload(payload, as_json=arguments.json, output=stderr)
        return _error_exit_code(code)
    except OSError:
        payload = {
            "schema": "fpmvp.runtime.v1",
            "command": getattr(arguments, "command", "unknown"),
            "ok": False,
            "changed": False,
            "dry_run": bool(getattr(arguments, "dry_run", False)),
            "overall": "failed",
            "error": {
                "code": "LOCAL_IO_FAILED",
                "message": "本機 runtime 檔案或 process 操作失敗。",
            },
        }
        _write_payload(payload, as_json=arguments.json, output=stderr)
        return 5

    _write_report(report, as_json=arguments.json, output=stdout)
    if report.ok:
        return 0
    return 3 if report.overall in {"incompatible", "unconfigured"} else 5


def _dispatch(
    manager: RuntimeManager,
    arguments: argparse.Namespace,
) -> RuntimeReport:
    if arguments.command == "install":
        return manager.install(
            comfy_mode=arguments.comfy_mode,
            models_mode=arguments.models_mode,
            comfyui_root=arguments.comfyui_root,
            comfyui_python=arguments.comfyui_python,
            model_root=arguments.model_root,
            dry_run=arguments.dry_run,
        )
    if arguments.command == "preflight":
        return manager.preflight(
            full_model_hash=arguments.full,
            dry_run=arguments.dry_run,
        )
    if arguments.command == "start":
        return manager.start(
            gateway_only=arguments.gateway_only,
            open_browser=not arguments.no_open,
            dry_run=arguments.dry_run,
            wait_seconds=arguments.wait_seconds,
        )
    if arguments.command == "stop":
        return manager.stop(
            dry_run=arguments.dry_run,
            timeout_seconds=arguments.timeout_seconds,
        )
    if arguments.command == "status":
        return manager.status(dry_run=arguments.dry_run)
    raise RuntimeOperationError("COMMAND_INVALID", "未知 runtime command。")


def _write_report(
    report: RuntimeReport,
    *,
    as_json: bool,
    output: TextIO,
) -> None:
    _write_payload(report.to_dict(), as_json=as_json, output=output)


def _write_payload(
    payload: dict[str, object],
    *,
    as_json: bool,
    output: TextIO,
) -> None:
    if as_json:
        print(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            file=output,
        )
        return
    command = payload.get("command", "runtime")
    overall = payload.get("overall", "unknown")
    print(f"[{command}] {overall}", file=output)
    error = payload.get("error")
    if isinstance(error, dict):
        print(f"- {error.get('message', 'runtime 操作失敗')}", file=output)
        return
    checks = payload.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict):
                print(
                    f"- {check.get('status')}: {check.get('message')}",
                    file=output,
                )
    actions = payload.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict):
                print(
                    f"- {action.get('status')}: {action.get('action_id')}",
                    file=output,
                )
    urls = payload.get("urls")
    if isinstance(urls, dict):
        for name, url in sorted(urls.items()):
            print(f"- {name}: {url}", file=output)


def _error_exit_code(code: str) -> int:
    if code in {
        "PATH_REQUIRED",
        "RUNTIME_CONFIG_INVALID",
        "RUNTIME_NOT_INSTALLED",
        "UNSAFE_PATH",
    }:
        return 2
    if code in {
        "RUNTIME_BUSY",
        "COMFYUI_PORT_CONFLICT",
        "GATEWAY_PORT_CONFLICT",
    }:
        return 4
    return 5


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0 or parsed > 3600:
        raise argparse.ArgumentTypeError("必須是 0–3600 秒的正數")
    return parsed


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="輸出穩定 JSON schema。",
    )


if __name__ == "__main__":
    raise SystemExit(main())
