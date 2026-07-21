from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

from runtime.cli import build_parser, main
from runtime.downloads import (
    DownloadError,
    HTTPHeaders,
    HTTPResponse,
    ModelDownloader,
)
from runtime.manager import (
    CommandResult,
    ModelsMode,
    RuntimeCheck,
    RuntimeConfig,
    RuntimeManager,
    RuntimeMode,
    RuntimeOperationError,
)
from runtime.spec import ModelLock, RuntimeLock, load_runtime_lock

REPO_ROOT = Path(__file__).resolve().parents[1]


def _passing_platform_check() -> RuntimeCheck:
    return RuntimeCheck("platform", "pass", "PLATFORM_OK", "ok")


class SpyRunner:
    def __init__(self) -> None:
        self.which_calls: list[str] = []
        self.run_calls: list[tuple[tuple[str, ...], Path | None, object]] = []
        self.spawn_calls: list[tuple[str, ...]] = []

    def which(self, name: str) -> str | None:
        self.which_calls.append(name)
        return "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None

    def run(
        self,
        argv: Any,
        *,
        cwd: Path | None = None,
        env: object = None,
    ) -> CommandResult:
        encoded = tuple(str(item) for item in argv)
        self.run_calls.append((encoded, cwd, env))
        if encoded[-1:] == ("--version",):
            if Path(encoded[0]).name == "uv":
                return CommandResult(0, "uv 0.11.29 (x86_64-unknown-linux-gnu)\n")
            version = "3.12.3" if "comfy" in encoded[0].lower() else "3.12.10"
            return CommandResult(0, f"Python {version}\n")
        if encoded and encoded[0] == "git" and "rev-parse" in encoded:
            root = encoded[encoded.index("-C") + 1]
            commit = (
                "cf0573351ac260d629d460d97f09b09ac17d3726"
                if "ComfyUI-GGUF" in root
                else "ab0d8a9203fbad76b0ccca723bbf9ba0c257ddfe"
            )
            return CommandResult(0, f"{commit}\n")
        if encoded and encoded[0] == "git" and "status" in encoded:
            return CommandResult(0, "")
        if encoded and encoded[0].endswith("nvidia-smi"):
            return CommandResult(0, "NVIDIA GeForce RTX 5070 Ti, 16303\n")
        if "-c" in encoded:
            code = encoded[encoded.index("-c") + 1]
            if "aiohappyeyeballs" in code:
                return CommandResult(
                    0,
                    json.dumps(
                        RuntimeManager._locked_requirement_versions(
                            REPO_ROOT / "runtime/comfy-requirements.lock.txt"
                        )
                    ),
                )
            if "fastapi" in code:
                return CommandResult(
                    0,
                    json.dumps(
                        {
                            "fastapi": "0.139.2",
                            "httpx": "0.28.1",
                            "pillow": "12.3.0",
                            "pydantic": "2.13.4",
                            "pydantic-settings": "2.14.2",
                            "python-multipart": "0.0.32",
                            "starlette": "1.3.1",
                            "uvicorn": "0.51.0",
                        }
                    ),
                )
            return CommandResult(
                0,
                json.dumps(
                    {
                        "torch": "2.12.0+cu130",
                        "torchaudio": "2.11.0+cu130",
                        "torchvision": "0.27.0+cu130",
                    }
                ),
            )
        if len(encoded) >= 3 and encoded[1:3] == ("python", "install"):
            install_root = Path(encoded[encoded.index("--install-dir") + 1])
            version = encoded[-1]
            major_minor = ".".join(version.split(".")[:2])
            executable = (
                install_root
                / f"cpython-{version}-linux-x86_64-gnu"
                / "bin"
                / f"python{major_minor}"
            )
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("", encoding="utf-8")
        return CommandResult(0)

    def spawn(
        self,
        argv: Any,
        *,
        cwd: Path,
        env: object,
        log_path: Path,
    ) -> int:
        del cwd, env, log_path
        encoded = tuple(str(item) for item in argv)
        self.spawn_calls.append(encoded)
        raise AssertionError("這個測試不應啟動 process")


class ManagedInstallRunner(SpyRunner):
    def run(
        self,
        argv: Any,
        *,
        cwd: Path | None = None,
        env: object = None,
    ) -> CommandResult:
        encoded = tuple(str(item) for item in argv)
        if encoded[:2] == ("git", "clone"):
            target = Path(encoded[-1])
            (target / ".git").mkdir(parents=True)
            if target.name == "ComfyUI-GGUF":
                (target / "nodes.py").write_text("NODE = True\n", encoding="utf-8")
            else:
                (target / "custom_nodes").mkdir()
                (target / "main.py").write_text("pass\n", encoding="utf-8")
            self.run_calls.append((encoded, cwd, env))
            return CommandResult(0)
        if "venv" in encoded and encoded[0].endswith("/uv"):
            target = Path(encoded[-1])
            (target / "bin").mkdir(parents=True)
            (target / "bin/python").write_text("", encoding="utf-8")
        return super().run(encoded, cwd=cwd, env=env)


class FakeUvBootstrap:
    def __init__(self, tools_root: Path, lock: object) -> None:
        del lock
        self.executable = tools_root / "uv"

    def ensure(self) -> Path:
        return self.executable


class FakeHeaders:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, name: str) -> str | None:
        return self.values.get(name)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int,
        headers: dict[str, str],
    ) -> None:
        self.status = status
        self.headers: HTTPHeaders = FakeHeaders(headers)
        self._source = BytesIO(body)

    def read(self, amount: int = -1) -> bytes:
        return self._source.read(amount)

    def geturl(self) -> str:
        return "https://download.example/model.bin"

    def __enter__(self) -> HTTPResponse:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[Request] = []

    def open(self, request: Request, *, timeout: float) -> HTTPResponse:
        del timeout
        self.requests.append(request)
        return self.responses.pop(0)


def _small_runtime_lock() -> tuple[RuntimeLock, dict[str, bytes]]:
    original = load_runtime_lock()
    source = original.models[0].source
    contents = {f"model-{index}.bin": f"blob-{index}".encode() for index in range(8)}
    models = tuple(
        ModelLock(
            filename=filename,
            subdir="unet",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            required_by=("docs/workflows/wf_dual_B1.json",),
            url=f"https://download.example/{filename}",
            source=source,
            license="Apache-2.0",
        )
        for filename, content in contents.items()
    )
    return replace(original, models=models), contents


def _write_models(root: Path, contents: dict[str, bytes]) -> None:
    (root / "unet").mkdir(parents=True)
    for filename, content in contents.items():
        (root / "unet" / filename).write_bytes(content)


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_runtime_locks_have_exact_environment_and_nonblocking_agents() -> None:
    lock = load_runtime_lock()

    assert lock.uv.version == "0.11.29"
    assert lock.uv.sha256 == (
        "04f8b82f5d47f0512dcd32c67a4a6f16a0ea27c81537c338fd0ad6b23cebe829"
    )
    assert lock.gateway.python == "3.12.10"
    assert lock.comfyui.python == "3.12.3"
    assert lock.comfyui.commit == "ab0d8a9203fbad76b0ccca723bbf9ba0c257ddfe"
    assert lock.custom_nodes[0].commit == ("cf0573351ac260d629d460d97f09b09ac17d3726")
    assert len(lock.models) == 8
    assert all(model.url.startswith("https://") for model in lock.models)
    assert {agent.name for agent in lock.agents} == {"character", "scene"}
    assert all(agent.status == "pending" for agent in lock.agents)
    assert all(agent.blocks_start is False for agent in lock.agents)


def test_all_commands_dry_run_without_writes_or_subprocesses(tmp_path: Path) -> None:
    state_dir = tmp_path / "state-that-must-not-exist"
    runner = SpyRunner()
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=state_dir,
        runner=runner,
    )

    reports = [
        manager.install(dry_run=True),
        manager.preflight(dry_run=True),
        manager.start(dry_run=True),
        manager.stop(dry_run=True),
        manager.status(dry_run=True),
    ]

    assert all(report.ok and report.overall == "planned" for report in reports)
    assert not state_dir.exists()
    assert runner.which_calls == []
    assert runner.run_calls == []
    assert runner.spawn_calls == []


def test_dry_run_plans_pinned_local_bootstrap_and_safe_child_argv(
    tmp_path: Path,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "runtime")

    install = manager.install(dry_run=True)
    action_by_id = {action.action_id: action for action in install.actions}
    python_install = action_by_id["install-gateway-python"].argv
    gateway_sync = action_by_id["sync-gateway"].argv
    comfy_sync = action_by_id["sync-comfy-requirements"].argv
    comfy_venv = action_by_id["create-comfy-venv"].argv

    assert "--install-dir" in python_install
    assert "--no-bin" in python_install
    assert gateway_sync[1:4] == ("sync", "--locked", "--dev")
    assert comfy_sync[1:3] == ("pip", "sync")
    assert "--require-hashes" in comfy_sync
    assert comfy_venv[-1].endswith("/.venv")
    assert not comfy_venv[-1].endswith("/bin/python")
    assert all("curl" not in item.argv for item in install.actions)

    start = manager.start(dry_run=True)
    start_actions = {action.action_id: action for action in start.actions}
    comfy = start_actions["start-comfyui"].argv
    gateway = start_actions["start-gateway"].argv
    assert (
        comfy[comfy.index("--listen")],
        comfy[comfy.index("--listen") + 1],
    ) == ("--listen", "127.0.0.1")
    assert "--disable-all-custom-nodes" in comfy
    assert "--whitelist-custom-nodes" in comfy
    assert "--extra-model-paths-config" in comfy
    assert "--enable-manager" not in comfy
    assert "--enable-cors-header" not in comfy
    assert "0.0.0.0" not in comfy
    assert gateway[gateway.index("--host") + 1] == "127.0.0.1"
    assert gateway[gateway.index("--workers") + 1] == "1"


def test_cli_accepts_json_after_command_and_preflight_defaults_quick(
    tmp_path: Path,
) -> None:
    output = StringIO()
    code = main(
        [
            "--state-dir",
            str(tmp_path / "state"),
            "status",
            "--dry-run",
            "--json",
        ],
        stdout=output,
    )
    payload = json.loads(output.getvalue())
    parser = build_parser()

    assert code == 0
    assert payload["command"] == "status"
    assert payload["dry_run"] is True
    assert parser.parse_args(["preflight"]).full is False
    assert parser.parse_args(["preflight", "--full"]).full is True
    assert not (tmp_path / "state").exists()


def test_entrypoint_from_arbitrary_cwd_dry_run_creates_no_bytecode_or_state(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    bytecode_before = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for root in (REPO_ROOT / "runtime", REPO_ROOT / "scripts")
        for path in root.rglob("*.pyc")
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/fpmvp_runtime.py"),
            "--state-dir",
            str(state_dir),
            "status",
            "--dry-run",
            "--json",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key != "PYTHONDONTWRITEBYTECODE"
        },
    )
    bytecode_after = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for root in (REPO_ROOT / "runtime", REPO_ROOT / "scripts")
        for path in root.rglob("*.pyc")
    }

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["overall"] == "planned"
    assert completed.stderr == ""
    assert bytecode_after == bytecode_before
    assert not state_dir.exists()


def test_adopted_dry_run_is_explicit_and_rejects_windows_unc(tmp_path: Path) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    adopted = manager.install(
        comfy_mode=RuntimeMode.ADOPTED,
        comfyui_root=tmp_path / "ComfyUI",
        models_mode=ModelsMode.AUTO,
        dry_run=True,
    )

    assert adopted.mode == "adopted"
    assert any(
        action.action_id == "validate-adopted-comfyui" for action in adopted.actions
    )
    assert not (tmp_path / "state").exists()

    with pytest.raises(RuntimeOperationError, match="UNC"):
        manager.install(
            comfy_mode=RuntimeMode.ADOPTED,
            comfyui_root=Path(r"\\wsl.localhost\Ubuntu\home\user\ComfyUI"),
            dry_run=True,
        )
    with pytest.raises(RuntimeOperationError, match="model root"):
        manager.install(models_mode=ModelsMode.EXTERNAL, dry_run=True)


@pytest.mark.parametrize("existing_part", [b"", b"abc"])
def test_model_download_handles_empty_part_and_full_200_restart(
    tmp_path: Path,
    existing_part: bytes,
) -> None:
    content = b"abcdefgh"
    model = ModelLock(
        filename="model.bin",
        subdir="unet",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        required_by=("docs/workflows/wf_dual_B1.json",),
        url="https://download.example/model.bin",
        source=load_runtime_lock().models[0].source,
        license="Apache-2.0",
    )
    part = tmp_path / "unet/model.bin.part"
    part.parent.mkdir()
    part.write_bytes(existing_part)
    opener = FakeOpener(
        [
            FakeResponse(
                content,
                status=200,
                headers={"Content-Length": str(len(content))},
            )
        ]
    )

    result = ModelDownloader(tmp_path, opener=opener).download(model)

    assert result.path.read_bytes() == content
    assert result.resumed_from == 0
    assert not part.exists()
    if existing_part:
        assert opener.requests[0].headers["Range"] == f"bytes={len(existing_part)}-"


def test_model_download_resumes_206_and_keeps_bad_sha_as_part(
    tmp_path: Path,
) -> None:
    content = b"abcdefgh"
    model = ModelLock(
        filename="model.bin",
        subdir="unet",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        required_by=("docs/workflows/wf_dual_B1.json",),
        url="https://download.example/model.bin",
        source=load_runtime_lock().models[0].source,
        license="Apache-2.0",
    )
    part = tmp_path / "unet/model.bin.part"
    part.parent.mkdir()
    part.write_bytes(content[:3])
    opener = FakeOpener(
        [
            FakeResponse(
                content[3:],
                status=206,
                headers={
                    "Content-Length": str(len(content) - 3),
                    "Content-Range": f"bytes 3-{len(content) - 1}/{len(content)}",
                },
            )
        ]
    )
    result = ModelDownloader(tmp_path, opener=opener).download(model)
    assert result.resumed_from == 3
    assert result.path.read_bytes() == content

    bad_root = tmp_path / "bad"
    bad_model = replace(model, sha256="0" * 64)
    bad_opener = FakeOpener(
        [
            FakeResponse(
                content,
                status=200,
                headers={"Content-Length": str(len(content))},
            )
        ]
    )
    with pytest.raises(DownloadError, match="SHA-256"):
        ModelDownloader(bad_root, opener=bad_opener).download(bad_model)
    assert not (bad_root / "unet/model.bin").exists()
    assert (bad_root / "unet/model.bin.part").read_bytes() == content


def test_model_receipt_invalidates_when_metadata_changes(tmp_path: Path) -> None:
    runtime_lock, contents = _small_runtime_lock()
    model_root = tmp_path / "models"
    _write_models(model_root, contents)
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runtime_lock=runtime_lock,
    )

    manager._verify_all_models(model_root)
    manager._write_model_receipt(model_root)
    assert manager._model_receipt_check(model_root).passed

    first = model_root / "unet/model-0.bin"
    stat_result = first.stat()
    os.utime(
        first,
        ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000_000),
    )
    assert not manager._model_receipt_check(model_root).passed


def test_managed_install_is_pinned_idempotent_and_uses_external_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_lock, contents = _small_runtime_lock()
    models = tmp_path / "external-models"
    _write_models(models, contents)
    runner = ManagedInstallRunner()
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runtime_lock=runtime_lock,
        runner=runner,
        uv_bootstrap_factory=FakeUvBootstrap,
    )
    monkeypatch.setattr(manager, "_platform_check", _passing_platform_check)
    monkeypatch.setattr(manager, "_http_health", lambda port, path: "free")

    first = manager.install(
        comfy_mode=RuntimeMode.MANAGED,
        models_mode=ModelsMode.EXTERNAL,
        model_root=models,
    )
    clone_count = sum(call[0][:2] == ("git", "clone") for call in runner.run_calls)
    second = manager.install(
        comfy_mode=RuntimeMode.MANAGED,
        models_mode=ModelsMode.EXTERNAL,
        model_root=models,
    )

    assert first.ok and second.ok
    assert first.models_mode == "external"
    assert clone_count == 2
    assert (
        sum(call[0][:2] == ("git", "clone") for call in runner.run_calls) == clone_count
    )
    marker = manager.managed_comfy_root / ".git/fpmvp-managed.json"
    assert marker.is_file()
    flattened = [argument for call, _, _ in runner.run_calls for argument in call]
    assert "reset" not in flattened
    assert "pull" not in flattened
    uv_calls = [
        (call, env)
        for call, _, env in runner.run_calls
        if call and call[0].endswith("/uv")
    ]
    assert uv_calls
    assert all(
        isinstance(env, dict)
        and env["UV_CACHE_DIR"] == str(manager.state_dir / "cache/uv")
        for _, env in uv_calls
    )


def test_adopted_install_never_mutates_source_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_lock, contents = _small_runtime_lock()
    adopted = tmp_path / "adopted-comfy"
    (adopted / ".git").mkdir(parents=True)
    (adopted / "main.py").write_text("pass\n", encoding="utf-8")
    node = adopted / "custom_nodes/ComfyUI-GGUF"
    (node / ".git").mkdir(parents=True)
    (node / "nodes.py").write_text("NODE = True\n", encoding="utf-8")
    python_path = adopted / "venv/bin/python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("", encoding="utf-8")
    models = tmp_path / "models"
    _write_models(models, contents)
    before = _snapshot_files(adopted)
    runner = SpyRunner()
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runtime_lock=runtime_lock,
        runner=runner,
        uv_bootstrap_factory=FakeUvBootstrap,
    )
    monkeypatch.setattr(manager, "_platform_check", _passing_platform_check)
    monkeypatch.setattr(manager, "_http_health", lambda port, path: "free")

    result = manager.install(
        comfy_mode=RuntimeMode.ADOPTED,
        comfyui_root=adopted,
        comfyui_python=python_path,
        models_mode=ModelsMode.EXTERNAL,
        model_root=models,
    )

    assert result.ok
    assert _snapshot_files(adopted) == before
    mutating_git = {"clone", "checkout", "fetch", "pull", "reset"}
    assert not any(
        any(argument in mutating_git for argument in call)
        and (cwd == adopted or str(adopted) in call)
        for call, cwd, _ in runner.run_calls
    )
    assert not any(
        call and call[0].endswith("/uv") and cwd == adopted
        for call, cwd, _ in runner.run_calls
    )


def test_start_rejects_healthy_unowned_gateway_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    config = RuntimeConfig(
        instance_id="1" * 32,
        project_root=REPO_ROOT,
        comfy_mode=RuntimeMode.MANAGED,
        models_mode=ModelsMode.MANAGED,
        comfyui_root=manager.managed_comfy_root,
        comfyui_python=manager.managed_comfy_root / ".venv/bin/python",
        model_root=manager.managed_model_root,
        gateway_port=8010,
        comfyui_port=8188,
    )
    manager._prepare_state_dirs()
    manager._atomic_json(manager.config_path, config.to_dict())
    monkeypatch.setattr(
        manager,
        "_preflight_checks",
        lambda config, full_model_hash: [
            _passing_platform_check(),
            RuntimeCheck(
                "gateway-python",
                "pass",
                "PYTHON_VERSION_OK",
                "ok",
            ),
        ],
    )
    monkeypatch.setattr(manager, "_http_health", lambda port, path: "healthy")

    with pytest.raises(RuntimeOperationError) as error:
        manager.start(gateway_only=True, open_browser=False)

    assert error.value.code == "GATEWAY_PORT_NOT_OWNED"


def test_powershell_wrapper_targets_existing_python_entrypoint() -> None:
    wrapper = (REPO_ROOT / "scripts/runtime.ps1").read_text(encoding="utf-8")

    assert "fpmvp_runtime.py" in wrapper
    assert '"runtime.py"' not in wrapper
