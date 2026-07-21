from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import NoReturn

import pytest

import runtime.cli as runtime_cli
from runtime.manager import (
    CommandResult,
    ModelsMode,
    ProcessRecord,
    RuntimeCheck,
    RuntimeConfig,
    RuntimeManager,
    RuntimeMode,
    RuntimeOperationError,
    RuntimeReport,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _passing_platform_check() -> RuntimeCheck:
    return RuntimeCheck("platform", "pass", "PLATFORM_OK", "ok")


class _FakeUvBootstrap:
    def __init__(self, tools_root: Path, lock: object) -> None:
        del lock
        self.executable = tools_root / "uv"

    def ensure(self) -> Path:
        return self.executable


class _IgnoreRejectingRunner:
    def __init__(self, missing_path: str, state_dir: Path) -> None:
        self.missing_path = missing_path
        self.state_dir = state_dir
        self.run_calls: list[tuple[str, ...]] = []

    def which(self, name: str) -> str | None:
        del name
        return None

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        del cwd, env
        encoded = tuple(str(item) for item in argv)
        self.run_calls.append(encoded)
        if encoded and encoded[0] == "git" and "check-ignore" in encoded:
            assert not self.state_dir.exists(), (
                "git-ignore 必須在建立 runtime state 之前檢查"
            )
            if self.missing_path in "\n".join(encoded):
                return CommandResult(1)
            return CommandResult(0)
        return CommandResult(0)

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_path: Path,
    ) -> int:
        del argv, cwd, env, log_path
        raise AssertionError("git-ignore fail closed 前不得 spawn")


class _FailingRunner:
    def __init__(self, error: OSError) -> None:
        self.error = error

    def which(self, name: str) -> str | None:
        del name
        return None

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        del argv, cwd, env
        raise self.error

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_path: Path,
    ) -> int:
        del argv, cwd, env, log_path
        raise self.error


class _SleepingRunner:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None

    def which(self, name: str) -> str | None:
        del name
        return None

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        del argv, cwd, env
        return CommandResult(0)

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_path: Path,
    ) -> int:
        del log_path
        self.process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        threading.Thread(target=self.process.wait, daemon=True).start()
        return self.process.pid

    def force_cleanup(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _runtime_config(manager: RuntimeManager, model_root: Path) -> RuntimeConfig:
    return RuntimeConfig(
        instance_id="1" * 32,
        project_root=manager.project_root,
        comfy_mode=RuntimeMode.MANAGED,
        models_mode=ModelsMode.MANAGED,
        comfyui_root=manager.managed_comfy_root,
        comfyui_python=manager.managed_comfy_root / ".venv/bin/python",
        model_root=model_root,
        gateway_port=manager.lock.gateway.port,
        comfyui_port=manager.lock.comfyui.port,
    )


def _process_record(name: str, pid: int) -> ProcessRecord:
    return ProcessRecord(
        name=name,
        pid=pid,
        pgid=pid,
        start_ticks="100",
        boot_id="boot-id",
        executable="/usr/bin/python3",
        argv_sha256="0" * 64,
    )


@pytest.mark.parametrize("missing_path", [".runtime", ".venv"])
def test_install_rejects_unignored_runtime_paths_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_path: str,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    state_dir = project_root / ".runtime"
    runner = _IgnoreRejectingRunner(missing_path, state_dir)
    manager = RuntimeManager(
        project_root,
        runner=runner,
        uv_bootstrap_factory=_FakeUvBootstrap,
    )
    monkeypatch.setattr(manager, "_platform_check", _passing_platform_check)

    with pytest.raises(RuntimeOperationError) as caught:
        manager.install()

    assert caught.value.code == "GIT_IGNORE_MISSING"
    assert not state_dir.exists()
    assert runner.run_calls
    assert all(
        call and call[0] == "git" and "check-ignore" in call
        for call in runner.run_calls
    )


def test_child_environment_does_not_forward_codex_or_openai_secrets(
    tmp_path: Path,
) -> None:
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        environ={
            "HOME": "/home/tester",
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "must-not-reach-child",
            "CODEX_HOME": "/home/tester/.codex",
            "HTTPS_PROXY": "https://proxy.example",
        },
    )

    child_env = manager._child_env(_runtime_config(manager, tmp_path / "models"))

    assert "OPENAI_API_KEY" not in child_env
    assert "CODEX_HOME" not in child_env
    assert child_env["HOME"] == "/home/tester"
    assert child_env["HTTPS_PROXY"] == "https://proxy.example"


def test_status_cannot_be_ready_when_required_models_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    config = _runtime_config(manager, tmp_path / "missing-models")
    records = {
        "gateway": _process_record("gateway", 2001),
        "comfyui": _process_record("comfyui", 2002),
    }
    monkeypatch.setattr(manager, "_load_config", lambda required=False: config)
    monkeypatch.setattr(manager, "_load_processes", lambda required=False: records)
    monkeypatch.setattr(manager, "_process_matches", lambda record: True)
    monkeypatch.setattr(manager, "_service_owned", lambda port, record: True)
    monkeypatch.setattr(manager, "_http_health", lambda port, path: "healthy")

    report = manager.status()
    models_check = next(
        check for check in report.checks if check.check_id == "models-size"
    )

    assert models_check.status == "fail"
    assert report.ok is False
    assert report.overall == "degraded"


@pytest.mark.parametrize(
    ("linked", "expected_ok", "expected_overall"),
    [(True, True, "ready"), (False, False, "degraded")],
)
def test_status_requires_gateway_to_have_started_with_comfy_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    linked: bool,
    expected_ok: bool,
    expected_overall: str,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    config = _runtime_config(manager, tmp_path / "models")
    gateway = replace(
        _process_record("gateway", 2101),
        comfy_enabled=linked,
    )
    records = {
        "gateway": gateway,
        "comfyui": _process_record("comfyui", 2102),
    }
    monkeypatch.setattr(manager, "_load_config", lambda required=False: config)
    monkeypatch.setattr(manager, "_load_processes", lambda required=False: records)
    monkeypatch.setattr(manager, "_process_matches", lambda record: True)
    monkeypatch.setattr(manager, "_service_owned", lambda port, record: True)
    monkeypatch.setattr(manager, "_http_health", lambda port, path: "healthy")
    monkeypatch.setattr(
        manager,
        "_model_size_check",
        lambda model_root: RuntimeCheck(
            "models-size",
            "pass",
            "MODEL_BYTES_OK",
            "ok",
        ),
    )

    report = manager.status()
    link_check = next(
        check for check in report.checks if check.check_id == "gateway-comfy-link"
    )

    assert report.ok is expected_ok
    assert report.overall == expected_overall
    assert link_check.status == ("pass" if linked else "warn")


def test_status_is_degraded_while_only_owned_comfyui_is_healthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    config = _runtime_config(manager, tmp_path / "models")
    records = {
        "gateway": replace(
            _process_record("gateway", 2201),
            comfy_enabled=True,
        ),
        "comfyui": _process_record("comfyui", 2202),
    }
    monkeypatch.setattr(manager, "_load_config", lambda required=False: config)
    monkeypatch.setattr(manager, "_load_processes", lambda required=False: records)
    monkeypatch.setattr(
        manager,
        "_process_matches",
        lambda record: record.name == "comfyui",
    )
    monkeypatch.setattr(manager, "_service_owned", lambda port, record: True)
    monkeypatch.setattr(
        manager,
        "_http_health",
        lambda port, path: "healthy" if port == config.comfyui_port else "free",
    )
    monkeypatch.setattr(
        manager,
        "_model_size_check",
        lambda model_root: RuntimeCheck(
            "models-size",
            "pass",
            "MODEL_BYTES_OK",
            "ok",
        ),
    )

    report = manager.status()

    assert report.ok is False
    assert report.overall == "degraded"


def test_gateway_stop_failure_prevents_attempting_to_stop_comfyui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    records = {
        "gateway": _process_record("gateway", 3001),
        "comfyui": _process_record("comfyui", 3002),
    }
    stop_calls: list[str] = []
    written: list[dict[str, ProcessRecord]] = []

    monkeypatch.setattr(
        manager,
        "_load_processes",
        lambda required=False: dict(records),
    )
    monkeypatch.setattr(manager, "_exclusive_lock", contextlib.nullcontext)

    def stop_record(
        record: ProcessRecord,
        *,
        timeout_seconds: float,
    ) -> bool:
        del timeout_seconds
        stop_calls.append(record.name)
        return False

    monkeypatch.setattr(manager, "_stop_record", stop_record)
    monkeypatch.setattr(
        manager,
        "_write_processes",
        lambda remaining: written.append(dict(remaining)),
    )

    report = manager.stop(timeout_seconds=0.1)

    assert stop_calls == ["gateway"]
    assert written == [records]
    assert report.ok is False
    assert report.changed is False


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            FileNotFoundError(errno.ENOENT, "missing executable", "probe-tool"),
            "COMMAND_NOT_FOUND",
        ),
        (
            PermissionError(errno.EACCES, "permission denied", "probe-tool"),
            "COMMAND_PERMISSION_DENIED",
        ),
    ],
)
def test_subprocess_oserror_becomes_typed_cli_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    expected_code: str,
) -> None:
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runner=_FailingRunner(error),
    )

    def probe_status(*, dry_run: bool = False) -> RuntimeReport:
        del dry_run
        manager._run_checked(["probe-tool"])
        raise AssertionError("unreachable")

    monkeypatch.setattr(manager, "status", probe_status)
    monkeypatch.setattr(
        runtime_cli,
        "RuntimeManager",
        lambda *args, **kwargs: manager,
    )
    stdout = StringIO()
    stderr = StringIO()

    exit_code = runtime_cli.main(
        ["--json", "status"],
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stderr.getvalue())

    assert exit_code == 5
    assert stdout.getvalue() == ""
    assert payload["ok"] is False
    assert payload["overall"] == "failed"
    assert payload["error"]["code"] == expected_code
    assert "probe-tool" in payload["error"]["message"]


@pytest.mark.parametrize("failure_point", ["record", "write"])
def test_start_rolls_back_spawned_gateway_if_record_or_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    runner = _SleepingRunner()
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runner=runner,
        environ={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
    )
    config = _runtime_config(manager, tmp_path / "models")
    gateway_argv = (
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
    )
    checks = [
        RuntimeCheck("platform", "pass", "PLATFORM_OK", "ok"),
        RuntimeCheck(
            "gateway-python",
            "pass",
            "PYTHON_VERSION_OK",
            "ok",
        ),
    ]
    monkeypatch.setattr(manager, "_load_config", lambda required=True: config)
    monkeypatch.setattr(manager, "_exclusive_lock", contextlib.nullcontext)
    monkeypatch.setattr(
        manager,
        "_preflight_checks",
        lambda config, full_model_hash: checks,
    )
    monkeypatch.setattr(manager, "_load_processes", lambda required=False: {})
    monkeypatch.setattr(manager, "_http_health", lambda port, path: "free")
    monkeypatch.setattr(manager, "_gateway_argv", lambda config: gateway_argv)
    monkeypatch.setattr(manager, "_write_processes", lambda records: None)

    expected_code = "PROCESS_RECORD_FAILED"
    if failure_point == "record":

        def fail_record(
            name: str,
            pid: int,
            argv: Sequence[str],
        ) -> NoReturn:
            del name, pid, argv
            raise RuntimeOperationError(expected_code, "record failed")

        monkeypatch.setattr(manager, "_record_process", fail_record)
    else:
        expected_code = "PROCESS_STATE_WRITE_FAILED"
        write_attempts = 0

        def fail_first_write(records: Mapping[str, ProcessRecord]) -> None:
            nonlocal write_attempts
            del records
            write_attempts += 1
            if write_attempts == 1:
                raise RuntimeOperationError(expected_code, "write failed")

        monkeypatch.setattr(manager, "_write_processes", fail_first_write)

    try:
        with pytest.raises(RuntimeOperationError) as caught:
            manager.start(
                gateway_only=True,
                open_browser=False,
                wait_seconds=0.1,
            )

        assert caught.value.code == expected_code
        process = runner.process
        assert process is not None
        deadline = time.monotonic() + 1.5
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert process.poll() is not None, (
            "spawn 後無法建立 record 或寫入 state 時必須 rollback Gateway"
        )
    finally:
        runner.force_cleanup()
