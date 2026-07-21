from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from runtime.spec import RuntimeLock, ToolLock, load_runtime_lock

SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from setup_dev import (  # noqa: E402
    CommandResult,
    DevSetup,
    SetupError,
    SubprocessCommandRunner,
)


class FakeBootstrap:
    def __init__(self, executable: Path) -> None:
        self.executable = executable
        self.ensure_calls = 0

    def ensure(self) -> Path:
        self.ensure_calls += 1
        self.executable.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.executable.write_bytes(b"fixture uv")
        self.executable.chmod(0o700)
        return self.executable


class SpyRunner:
    def __init__(
        self,
        project_root: Path,
        lock: RuntimeLock,
        *,
        ignored: bool = True,
        fail_python_install: bool = False,
    ) -> None:
        self.project_root = project_root
        self.lock = lock
        self.ignored = ignored
        self.fail_python_install = fail_python_install
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandResult:
        encoded = tuple(argv)
        self.calls.append((encoded, cwd, dict(env)))
        if encoded[0] == "git":
            return CommandResult(0 if self.ignored else 1)

        uv_binary = self.project_root / ".python/tools/uv"
        python_binary = _python_binary(self.project_root, self.lock.gateway.python)
        venv_python = self.project_root / ".venv/bin/python"
        if encoded == (str(uv_binary), "--version"):
            return CommandResult(0, f"uv {self.lock.uv.version} (fixture)\n")
        if encoded[1:3] == ("python", "install"):
            if self.fail_python_install:
                return CommandResult(17, stderr="fixture install failure")
            python_binary.parent.mkdir(parents=True)
            python_binary.write_bytes(b"fixture python")
            python_binary.chmod(0o700)
            return CommandResult(0)
        if encoded == (str(python_binary), "--version"):
            return CommandResult(0, f"Python {self.lock.gateway.python}\n")
        if encoded[1:2] == ("sync",):
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(python_binary)
            return CommandResult(0)
        if encoded == (str(venv_python), "--version"):
            return CommandResult(0, f"Python {self.lock.gateway.python}\n")
        raise AssertionError(f"unexpected argv: {encoded!r}")


class NoCallRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandResult:
        del argv, cwd, env
        raise AssertionError("dry-run 不可執行 subprocess")


def _python_binary(project_root: Path, version: str) -> Path:
    major_minor = ".".join(version.split(".")[:2])
    return (
        project_root
        / ".python"
        / f"cpython-{version}-linux-x86_64-gnu"
        / "bin"
        / f"python{major_minor}"
    )


def _prepare_project_inputs(project_root: Path) -> None:
    (project_root / "runtime").mkdir(parents=True)
    for relative in (
        "pyproject.toml",
        "uv.lock",
        "runtime/runtime-lock.json",
        "runtime/models.lock.json",
    ):
        (project_root / relative).write_text("fixture\n", encoding="utf-8")


def _setup_with_fakes(
    project_root: Path,
    lock: RuntimeLock,
    runner: SpyRunner | NoCallRunner,
    *,
    executable: Path | None = None,
) -> tuple[DevSetup, FakeBootstrap]:
    uv_binary = executable or project_root / ".python/tools/uv"
    bootstrap = FakeBootstrap(uv_binary)

    def bootstrap_factory(tools_root: Path, tool_lock: ToolLock) -> FakeBootstrap:
        assert tools_root == project_root / ".python/tools"
        assert tool_lock == lock.uv
        return bootstrap

    return (
        DevSetup(
            project_root,
            lock=lock,
            runner=runner,
            uv_bootstrap_factory=bootstrap_factory,
            environ={
                "HOME": "/home/fixture",
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "must-not-reach-child",
                "CODEX_HOME": "/home/fixture/.codex",
                "UV_CACHE_DIR": "/tmp/untrusted-cache",
            },
        ),
        bootstrap,
    )


def test_setup_uses_pinned_fixed_argv_and_only_local_dev_roots(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _prepare_project_inputs(project_root)
    runtime_sentinel = project_root / ".runtime/models/model.bin"
    runtime_sentinel.parent.mkdir(parents=True)
    runtime_sentinel.write_bytes(b"do not touch")
    local_data_sentinel = project_root / ".local-data/sentinel"
    local_data_sentinel.parent.mkdir()
    local_data_sentinel.write_bytes(b"do not touch")

    lock = load_runtime_lock()
    runner = SpyRunner(project_root, lock)
    setup, bootstrap = _setup_with_fakes(project_root, lock, runner)

    plan = setup.setup()

    uv_binary = project_root / ".python/tools/uv"
    python_binary = _python_binary(project_root, lock.gateway.python)
    expected_argv = [
        (
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            "-C",
            str(project_root),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            ".python/.fpmvp-dev-ignore-probe",
        ),
        (
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            "-C",
            str(project_root),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            ".venv/.fpmvp-dev-ignore-probe",
        ),
        (str(uv_binary), "--version"),
        (
            str(uv_binary),
            "python",
            "install",
            "--install-dir",
            str(project_root / ".python"),
            "--no-bin",
            "3.12.10",
        ),
        (str(python_binary), "--version"),
        (
            str(uv_binary),
            "sync",
            "--locked",
            "--dev",
            "--python",
            str(python_binary),
        ),
        (str(project_root / ".venv/bin/python"), "--version"),
    ]
    assert [call[0] for call in runner.calls] == expected_argv
    assert all(call[1] == project_root for call in runner.calls)
    uv_environments = [call[2] for call in runner.calls[2:]]
    assert uv_environments
    assert all(
        environment["UV_CACHE_DIR"] == str(project_root / ".python/cache/uv")
        and environment["UV_PYTHON_INSTALL_DIR"] == str(project_root / ".python")
        and environment["UV_PROJECT_ENVIRONMENT"] == str(project_root / ".venv")
        and "OPENAI_API_KEY" not in environment
        and "CODEX_HOME" not in environment
        for environment in uv_environments
    )
    assert bootstrap.ensure_calls == 1
    assert plan.uv_version == "0.11.29"
    assert plan.python_version == "3.12.10"
    assert runtime_sentinel.read_bytes() == b"do not touch"
    assert local_data_sentinel.read_bytes() == b"do not touch"


def test_dry_run_has_fixed_plan_and_zero_writes_or_subprocesses(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "empty-repo"
    project_root.mkdir()
    lock = load_runtime_lock()
    setup, bootstrap = _setup_with_fakes(project_root, lock, NoCallRunner())

    before = tuple(project_root.iterdir())
    plan = setup.setup(dry_run=True)

    assert before == tuple(project_root.iterdir()) == ()
    assert bootstrap.ensure_calls == 0
    actions = {action.action_id: action for action in plan.actions}
    assert actions["install-python"].argv[-1] == "3.12.10"
    assert actions["sync-dev-dependencies"].argv[1:4] == (
        "sync",
        "--locked",
        "--dev",
    )
    assert all(
        ".runtime" not in argument
        for action in plan.actions
        for argument in action.argv
    )


@pytest.mark.parametrize("managed_root", [".python", ".venv"])
def test_setup_rejects_managed_root_symlink_before_subprocess_or_write(
    tmp_path: Path,
    managed_root: str,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _prepare_project_inputs(project_root)
    external = tmp_path / "external"
    external.mkdir()
    (project_root / managed_root).symlink_to(external, target_is_directory=True)
    lock = load_runtime_lock()
    runner = SpyRunner(project_root, lock)
    setup, bootstrap = _setup_with_fakes(project_root, lock, runner)

    with pytest.raises(SetupError, match="非 symlink 目錄") as caught:
        setup.setup()

    assert caught.value.code == "MANAGED_PATH_INVALID"
    assert runner.calls == []
    assert bootstrap.ensure_calls == 0
    assert tuple(external.iterdir()) == ()


def test_setup_stops_when_dev_roots_are_not_git_ignored(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _prepare_project_inputs(project_root)
    lock = load_runtime_lock()
    runner = SpyRunner(project_root, lock, ignored=False)
    setup, bootstrap = _setup_with_fakes(project_root, lock, runner)

    with pytest.raises(SetupError, match="Git ignore") as caught:
        setup.setup()

    assert caught.value.code == "GIT_IGNORE_MISSING"
    assert len(runner.calls) == 1
    assert bootstrap.ensure_calls == 0
    assert not (project_root / ".python").exists()
    assert not (project_root / ".venv").exists()


def test_setup_rejects_uv_outside_managed_tools_root(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _prepare_project_inputs(project_root)
    lock = load_runtime_lock()
    runner = SpyRunner(project_root, lock)
    setup, bootstrap = _setup_with_fakes(
        project_root,
        lock,
        runner,
        executable=tmp_path / "untrusted-uv",
    )

    with pytest.raises(SetupError, match=".python/tools/uv") as caught:
        setup.setup()

    assert caught.value.code == "UV_PATH_INVALID"
    assert bootstrap.ensure_calls == 1
    assert len(runner.calls) == 2
    assert not (project_root / ".venv").exists()


def test_failed_python_install_stops_before_sync_and_preserves_runtime(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _prepare_project_inputs(project_root)
    runtime_sentinel = project_root / ".runtime/models/model.bin"
    runtime_sentinel.parent.mkdir(parents=True)
    runtime_sentinel.write_bytes(b"unchanged")
    lock = load_runtime_lock()
    runner = SpyRunner(project_root, lock, fail_python_install=True)
    setup, _ = _setup_with_fakes(project_root, lock, runner)

    with pytest.raises(SetupError, match="exit code 17") as caught:
        setup.setup()

    assert caught.value.code == "COMMAND_FAILED"
    assert all(call[0][1:2] != ("sync",) for call in runner.calls)
    assert not (project_root / ".venv").exists()
    assert runtime_sentinel.read_bytes() == b"unchanged"


def test_subprocess_runner_uses_argv_and_shell_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SubprocessCommandRunner().run(
        ("fixed-tool", "fixed-argument"),
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
    )

    assert result == CommandResult(0, "ok", "")
    assert captured["argv"] == ["fixed-tool", "fixed-argument"]
    assert captured["shell"] is False
    assert captured["cwd"] == tmp_path
    assert captured["env"] == {"PATH": "/usr/bin"}
