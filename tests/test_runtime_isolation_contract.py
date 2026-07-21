from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from runtime.manager import (
    CommandResult,
    ModelsMode,
    ProcessRecord,
    RuntimeCheck,
    RuntimeConfig,
    RuntimeManager,
    RuntimeMode,
    RuntimeOperationError,
)
from runtime.spec import ModelLock, load_runtime_lock

REPO_ROOT = Path(__file__).resolve().parents[1]


class _SpawnRecordingRunner:
    def __init__(self) -> None:
        self.spawn_calls: list[tuple[tuple[str, ...], Path, dict[str, str], Path]] = []

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
        self.spawn_calls.append(
            (tuple(str(item) for item in argv), cwd, dict(env), log_path)
        )
        return 4100 + len(self.spawn_calls)


def _config(manager: RuntimeManager, *, model_root: Path) -> RuntimeConfig:
    return RuntimeConfig(
        instance_id="1" * 32,
        project_root=manager.project_root,
        comfy_mode=RuntimeMode.ADOPTED,
        models_mode=ModelsMode.EXTERNAL,
        comfyui_root=manager.state_dir / "fixture-comfy",
        comfyui_python=manager.state_dir / "fixture-comfy/.venv/bin/python",
        model_root=model_root,
        gateway_port=manager.lock.gateway.port,
        comfyui_port=manager.lock.comfyui.port,
    )


def _record(name: str, pid: int) -> ProcessRecord:
    return ProcessRecord(
        name=name,
        pid=pid,
        pgid=pid,
        start_ticks="100",
        boot_id="fixture",
        executable="/usr/bin/python3",
        argv_sha256="0" * 64,
    )


def test_full_start_spawns_comfy_from_runtime_base_before_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _SpawnRecordingRunner()
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runner=runner,
        environ={
            "HOME": "/home/tester",
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "gateway-only-secret",
            "CODEX_HOME": "/home/tester/.codex",
        },
    )
    config = _config(manager, model_root=tmp_path / "models")
    checks = [
        RuntimeCheck("platform", "pass", "PLATFORM_OK", "ok"),
        RuntimeCheck("gateway-python", "pass", "PYTHON_VERSION_OK", "ok"),
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
    monkeypatch.setattr(
        manager,
        "_record_process",
        lambda name, pid, argv: _record(name, pid),
    )
    monkeypatch.setattr(manager, "_write_processes", lambda records: None)
    monkeypatch.setattr(
        manager,
        "_wait_for_health",
        lambda port, path, timeout_seconds: True,
    )
    monkeypatch.setattr(manager, "_service_owned", lambda port, record: True)

    report = manager.start(open_browser=False, wait_seconds=0.1)

    assert report.ok
    assert [call[1] for call in runner.spawn_calls] == [
        manager.data_root,
        REPO_ROOT,
    ]
    comfy_argv, _, comfy_env, _ = runner.spawn_calls[0]
    gateway_argv, _, gateway_env, _ = runner.spawn_calls[1]
    assert comfy_argv[comfy_argv.index("--base-directory") + 1] == str(
        manager.data_root
    )
    assert "--disable-all-custom-nodes" in comfy_argv
    assert comfy_env["HOME"] == str(manager.state_dir / "home")
    assert "OPENAI_API_KEY" not in comfy_env
    assert "CODEX_HOME" not in comfy_env
    assert gateway_argv[gateway_argv.index("--host") + 1] == "127.0.0.1"
    assert gateway_env["OPENAI_API_KEY"] == "gateway-only-secret"
    assert gateway_env["STORYBOARD_WORKFLOW_COMFYUI_BASE_URL"].endswith(":8188")


def test_gateway_only_never_connects_to_comfyui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _SpawnRecordingRunner()
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runner=runner,
    )
    config = _config(manager, model_root=tmp_path / "models")
    written: list[dict[str, ProcessRecord]] = []
    checks = [
        RuntimeCheck("platform", "pass", "PLATFORM_OK", "ok"),
        RuntimeCheck("gateway-python", "pass", "PYTHON_VERSION_OK", "ok"),
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
    monkeypatch.setattr(
        manager,
        "_record_process",
        lambda name, pid, argv: _record(name, pid),
    )
    monkeypatch.setattr(
        manager,
        "_write_processes",
        lambda records: written.append(dict(records)),
    )
    monkeypatch.setattr(
        manager,
        "_wait_for_health",
        lambda port, path, timeout_seconds: True,
    )
    monkeypatch.setattr(manager, "_service_owned", lambda port, record: True)

    report = manager.start(
        gateway_only=True,
        open_browser=False,
        wait_seconds=0.1,
    )

    assert report.ok
    assert len(runner.spawn_calls) == 1
    gateway_env = runner.spawn_calls[0][2]
    assert gateway_env["STORYBOARD_WORKFLOW_COMFYUI_BASE_URL"] == ("http://127.0.0.1:1")
    assert written[-1]["gateway"].comfy_enabled is False


def test_full_start_gateway_health_timeout_returns_failed_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _SpawnRecordingRunner()
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runner=runner,
    )
    config = _config(manager, model_root=tmp_path / "models")
    checks = [
        RuntimeCheck("platform", "pass", "PLATFORM_OK", "ok"),
        RuntimeCheck("gateway-python", "pass", "PYTHON_VERSION_OK", "ok"),
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
    monkeypatch.setattr(
        manager,
        "_record_process",
        lambda name, pid, argv: _record(name, pid),
    )
    monkeypatch.setattr(manager, "_write_processes", lambda records: None)
    monkeypatch.setattr(
        manager,
        "_wait_for_health",
        lambda port, path, timeout_seconds: port == config.comfyui_port,
    )
    monkeypatch.setattr(manager, "_service_owned", lambda port, record: True)

    report = manager.start(open_browser=False, wait_seconds=0.1)

    assert report.ok is False
    assert report.overall == "failed"


def test_process_state_round_trip_records_gateway_comfy_link(
    tmp_path: Path,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    manager._prepare_state_dirs()
    gateway = replace(_record("gateway", 4101), comfy_enabled=True)

    manager._write_processes({"gateway": gateway})

    assert manager._load_processes(required=True) == {"gateway": gateway}


def test_generated_model_config_is_exact_regular_file(
    tmp_path: Path,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    model_root = tmp_path / "models"
    comfy_root = tmp_path / "comfy"
    manager._prepare_state_dirs()
    manager._write_extra_model_paths(model_root, comfy_root)
    config = _config(manager, model_root=model_root)
    config = replace(config, comfyui_root=comfy_root)

    assert manager._extra_model_paths_check(config).passed
    content = manager.extra_models_path.read_text(encoding="utf-8")
    assert "is_default: true" in content
    assert "diffusion_models: |" in content
    assert "text_encoders: clip" in content
    assert "custom_nodes: custom_nodes" in content

    target = tmp_path / "copied-config.yaml"
    target.write_bytes(manager.extra_models_path.read_bytes())
    manager.extra_models_path.unlink()
    manager.extra_models_path.symlink_to(target)
    assert not manager._extra_model_paths_check(config).passed


def test_runtime_rejects_source_yaml_and_custom_node_shadow(
    tmp_path: Path,
) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    comfy_root = tmp_path / "comfy"
    comfy_root.mkdir()
    (comfy_root / "extra_model_paths.yaml").write_text(
        "foreign:\n  base_path: /tmp\n  unet: models\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeOperationError) as source_error:
        manager._reject_source_extra_model_paths(comfy_root)
    assert source_error.value.code == "SOURCE_MODEL_CONFIG_CONFLICT"

    source_config = comfy_root / "extra_model_paths.yaml"
    source_config.unlink()
    external_config = tmp_path / "foreign.yaml"
    external_config.write_text("foreign: {}\n", encoding="utf-8")
    source_config.symlink_to(external_config)
    with pytest.raises(RuntimeOperationError) as symlink_error:
        manager._reject_source_extra_model_paths(comfy_root)
    assert symlink_error.value.code == "SOURCE_MODEL_CONFIG_CONFLICT"

    custom_nodes = manager.data_root / "custom_nodes"
    custom_nodes.mkdir(parents=True)
    (custom_nodes / "ComfyUI-GGUF").mkdir()
    assert not manager._runtime_custom_nodes_check().passed


def test_model_auto_discovers_only_canonical_base_path_models(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    comfy_root = home / "ai/ComfyUI"
    comfy_root.mkdir(parents=True)
    canonical_base = tmp_path / "shared-comfy-data"
    (comfy_root / "extra_model_paths.yaml").write_text(
        "shared:\n"
        f"  base_path: {canonical_base}\n"
        "  unet: models/unet\n"
        "relative:\n"
        "  base_path: ../unsafe\n"
        "  unet: models/unet\n",
        encoding="utf-8",
    )
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        environ={"HOME": str(home), "PATH": "/usr/bin"},
    )

    candidates = manager._external_model_candidates(
        None,
        manager.managed_comfy_root,
    )

    assert (canonical_base / "models").resolve() in candidates
    assert not any("unsafe" in str(candidate) for candidate in candidates)


def test_runtime_data_rejects_symlinked_mutation_root(tmp_path: Path) -> None:
    manager = RuntimeManager(REPO_ROOT, state_dir=tmp_path / "state")
    outside = tmp_path / "outside"
    outside.mkdir()
    manager.data_root.mkdir(parents=True)
    (manager.data_root / "output").symlink_to(outside, target_is_directory=True)

    assert not manager._runtime_data_check().passed
    with pytest.raises(RuntimeOperationError) as caught:
        manager._prepare_runtime_data()
    assert caught.value.code == "RUNTIME_DATA_PATH_INVALID"


def test_selected_cross_alias_model_mismatch_is_rejected(tmp_path: Path) -> None:
    content = b"correct-model"
    original = load_runtime_lock()
    model = ModelLock(
        filename="model.bin",
        subdir="unet",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        required_by=("docs/workflows/wf_dual_B1.json",),
        url="https://download.example/model.bin",
        source=original.models[0].source,
        license="Apache-2.0",
    )
    manager = RuntimeManager(
        REPO_ROOT,
        state_dir=tmp_path / "state",
        runtime_lock=replace(original, models=(model,)),
    )
    selected = tmp_path / "models"
    (selected / "unet").mkdir(parents=True)
    (selected / "unet/model.bin").write_bytes(content)
    (selected / "diffusion_models").mkdir()
    (selected / "diffusion_models/model.bin").write_bytes(b"wrong-model!")

    with pytest.raises(RuntimeOperationError) as caught:
        manager._validate_model_search_identity(selected)
    assert caught.value.code == "MODEL_SEARCH_SHADOW_MISMATCH"

    (selected / "diffusion_models/model.bin").unlink()
    default_shadow = manager.data_root / "models/unet/model.bin"
    default_shadow.parent.mkdir(parents=True)
    default_shadow.write_bytes(b"wrong-model!")
    with pytest.raises(RuntimeOperationError) as data_error:
        manager._validate_model_search_identity(selected)
    assert data_error.value.code == "MODEL_SEARCH_SHADOW_MISMATCH"


def test_active_workflow_model_loaders_are_exactly_covered_by_lock() -> None:
    lock = load_runtime_lock()
    loader_keys = {"unet_name", "clip_name", "vae_name", "model_name"}
    actual: set[str] = set()
    for relative in (
        "docs/workflows/wf_dual_B1.json",
        "docs/workflows/wf_dual_B2.json",
        "docs/workflows/wf10_upscale_opt2.json",
    ):
        graph = json.loads((REPO_ROOT / relative).read_bytes())
        for node in graph.values():
            inputs = node.get("inputs", {})
            actual.update(
                value
                for key, value in inputs.items()
                if key in loader_keys and isinstance(value, str)
            )

    assert actual == {model.filename for model in lock.models}
    assert {required for model in lock.models for required in model.required_by} == {
        "docs/workflows/wf_dual_B1.json",
        "docs/workflows/wf_dual_B2.json",
        "docs/workflows/wf10_upscale_opt2.json",
    }
