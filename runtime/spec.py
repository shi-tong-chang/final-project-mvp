"""不依賴專案虛擬環境的 runtime lock parser。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_VERSION_PATTERN = re.compile(r"[0-9A-Za-z.+-]{1,64}")


class RuntimeLockError(ValueError):
    """runtime lock 缺欄位、含未知欄位或值不安全。"""


@dataclass(frozen=True, slots=True)
class GatewayLock:
    """Gateway process 的固定 runtime。"""

    python: str
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class ToolLock:
    """bootstrap tool 的固定 release asset。"""

    version: str
    url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ComfyUILock:
    """Managed ComfyUI 的來源與 Python/CUDA wheel pins。"""

    repository: str
    commit: str
    python: str
    host: str
    port: int
    torch: str
    torchvision: str
    torchaudio: str
    pytorch_index_url: str


@dataclass(frozen=True, slots=True)
class CustomNodeLock:
    """唯一允許載入的 custom node pin。"""

    name: str
    repository: str
    commit: str


@dataclass(frozen=True, slots=True)
class ModelLock:
    """模型以相對安裝位置、大小與 SHA-256 識別。"""

    filename: str
    subdir: str
    size_bytes: int
    sha256: str
    required_by: tuple[str, ...]
    url: str
    source: ModelSource
    license: str

    @property
    def relative_path(self) -> PurePosixPath:
        """回傳模型根目錄下的固定相對位置。"""

        return PurePosixPath(self.subdir, self.filename)


@dataclass(frozen=True, slots=True)
class ModelSource:
    """可稽核的模型來源 revision。"""

    provider: str
    repository: str
    revision: str
    path: str


@dataclass(frozen=True, slots=True)
class AgentLock:
    """尚未接入的 agent 不阻擋現有網站與 workflow。"""

    name: str
    status: str
    blocks_start: bool


@dataclass(frozen=True, slots=True)
class RuntimeLock:
    """整套 clone-to-run 的 machine-readable authority。"""

    schema_version: int
    uv: ToolLock
    gateway: GatewayLock
    comfyui: ComfyUILock
    custom_nodes: tuple[CustomNodeLock, ...]
    models: tuple[ModelLock, ...]
    agents: tuple[AgentLock, ...]


def load_runtime_lock(
    path: Path | None = None,
    *,
    models_path: Path | None = None,
) -> RuntimeLock:
    """載入並嚴格驗證 repository 內的 runtime lock。"""

    lock_path = path or Path(__file__).with_name("runtime-lock.json")
    try:
        payload = json.loads(lock_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeLockError("無法讀取 runtime lock") from exc
    root = _mapping(payload, "runtime lock")
    _exact_keys(
        root,
        {
            "schema_version",
            "tools",
            "gateway",
            "comfyui",
            "custom_nodes",
            "agents",
        },
        "runtime lock",
    )
    schema_version = _integer(root["schema_version"], "schema_version", minimum=1)
    if schema_version != 1:
        raise RuntimeLockError("不支援的 runtime lock schema")

    tools_raw = _mapping(root["tools"], "tools")
    _exact_keys(tools_raw, {"uv"}, "tools")
    uv_raw = _mapping(tools_raw["uv"], "tools.uv")
    _exact_keys(uv_raw, {"version", "url", "sha256"}, "tools.uv")
    uv = ToolLock(
        version=_version(uv_raw["version"], "tools.uv.version"),
        url=_https_url(uv_raw["url"], "tools.uv.url"),
        sha256=_sha256(uv_raw["sha256"], "tools.uv.sha256"),
    )

    gateway_raw = _mapping(root["gateway"], "gateway")
    _exact_keys(gateway_raw, {"python", "host", "port"}, "gateway")
    gateway = GatewayLock(
        python=_version(gateway_raw["python"], "gateway.python"),
        host=_loopback_host(gateway_raw["host"], "gateway.host"),
        port=_port(gateway_raw["port"], "gateway.port"),
    )

    comfy_raw = _mapping(root["comfyui"], "comfyui")
    _exact_keys(
        comfy_raw,
        {
            "repository",
            "commit",
            "python",
            "host",
            "port",
            "torch",
            "torchvision",
            "torchaudio",
            "pytorch_index_url",
        },
        "comfyui",
    )
    comfyui = ComfyUILock(
        repository=_https_git_url(comfy_raw["repository"], "comfyui.repository"),
        commit=_commit(comfy_raw["commit"], "comfyui.commit"),
        python=_version(comfy_raw["python"], "comfyui.python"),
        host=_loopback_host(comfy_raw["host"], "comfyui.host"),
        port=_port(comfy_raw["port"], "comfyui.port"),
        torch=_version(comfy_raw["torch"], "comfyui.torch"),
        torchvision=_version(comfy_raw["torchvision"], "comfyui.torchvision"),
        torchaudio=_version(comfy_raw["torchaudio"], "comfyui.torchaudio"),
        pytorch_index_url=_https_url(
            comfy_raw["pytorch_index_url"],
            "comfyui.pytorch_index_url",
        ),
    )

    custom_nodes = tuple(
        _parse_custom_node(item, index)
        for index, item in enumerate(_list(root["custom_nodes"], "custom_nodes"))
    )
    if tuple(item.name for item in custom_nodes) != ("ComfyUI-GGUF",):
        raise RuntimeLockError("custom_nodes 必須只包含 ComfyUI-GGUF")

    models = _load_models_lock(models_path or lock_path.with_name("models.lock.json"))

    agents = tuple(
        _parse_agent(item, index)
        for index, item in enumerate(_list(root["agents"], "agents"))
    )
    if {item.name for item in agents} != {"character", "scene"}:
        raise RuntimeLockError("agent lock 必須保留 character 與 scene")
    if any(item.status != "pending" or item.blocks_start for item in agents):
        raise RuntimeLockError("未接入 agent 必須是 non-blocking pending")

    return RuntimeLock(
        schema_version=schema_version,
        uv=uv,
        gateway=gateway,
        comfyui=comfyui,
        custom_nodes=custom_nodes,
        models=models,
        agents=agents,
    )


def _parse_custom_node(value: object, index: int) -> CustomNodeLock:
    label = f"custom_nodes[{index}]"
    item = _mapping(value, label)
    _exact_keys(item, {"name", "repository", "commit"}, label)
    return CustomNodeLock(
        name=_safe_name(item["name"], f"{label}.name"),
        repository=_https_git_url(item["repository"], f"{label}.repository"),
        commit=_commit(item["commit"], f"{label}.commit"),
    )


def _parse_model(value: object, index: int) -> ModelLock:
    label = f"models[{index}]"
    item = _mapping(value, label)
    _exact_keys(
        item,
        {
            "filename",
            "subdir",
            "bytes",
            "sha256",
            "required_by",
            "url",
            "source",
            "license",
        },
        label,
    )
    filename = _safe_name(item["filename"], f"{label}.filename")
    subdir = _safe_name(item["subdir"], f"{label}.subdir")
    if subdir not in {"unet", "clip", "vae", "diffusion_models", "upscale_models"}:
        raise RuntimeLockError(f"{label}.subdir 不合法")
    required_by = tuple(
        _workflow_path(entry, f"{label}.required_by")
        for entry in _list(item["required_by"], f"{label}.required_by")
    )
    if not required_by:
        raise RuntimeLockError(f"{label}.required_by 不合法")
    source_raw = _mapping(item["source"], f"{label}.source")
    _exact_keys(
        source_raw,
        {"provider", "repository", "revision", "path"},
        f"{label}.source",
    )
    provider = _safe_name(source_raw["provider"], f"{label}.source.provider")
    if provider not in {"github", "huggingface"}:
        raise RuntimeLockError(f"{label}.source.provider 不合法")
    source = ModelSource(
        provider=provider,
        repository=_repository_name(
            source_raw["repository"],
            f"{label}.source.repository",
        ),
        revision=_source_revision(
            source_raw["revision"],
            f"{label}.source.revision",
        ),
        path=_source_path(source_raw["path"], f"{label}.source.path"),
    )
    return ModelLock(
        filename=filename,
        subdir=subdir,
        size_bytes=_integer(
            item["bytes"],
            f"{label}.bytes",
            minimum=1,
        ),
        sha256=_sha256(item["sha256"], f"{label}.sha256"),
        required_by=required_by,
        url=_https_url(item["url"], f"{label}.url"),
        source=source,
        license=_license(item["license"], f"{label}.license"),
    )


def _load_models_lock(path: Path) -> tuple[ModelLock, ...]:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeLockError("無法讀取 models lock") from exc
    root = _mapping(payload, "models lock")
    _exact_keys(root, {"schema_version", "models"}, "models lock")
    if _integer(root["schema_version"], "models.schema_version", minimum=1) != 1:
        raise RuntimeLockError("不支援的 models lock schema")
    models = tuple(
        _parse_model(item, index)
        for index, item in enumerate(_list(root["models"], "models"))
    )
    if len(models) != 8:
        raise RuntimeLockError("models lock 必須包含八顆固定模型")
    if len({item.relative_path for item in models}) != len(models):
        raise RuntimeLockError("模型安裝位置不可重複")
    return models


def _parse_agent(value: object, index: int) -> AgentLock:
    label = f"agents[{index}]"
    item = _mapping(value, label)
    _exact_keys(item, {"name", "status", "blocks_start"}, label)
    status = _safe_name(item["status"], f"{label}.status")
    blocks_start = item["blocks_start"]
    if not isinstance(blocks_start, bool):
        raise RuntimeLockError(f"{label}.blocks_start 必須是 boolean")
    return AgentLock(
        name=_safe_name(item["name"], f"{label}.name"),
        status=status,
        blocks_start=blocks_start,
    )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RuntimeLockError(f"{label} 必須是 object")
    return cast(dict[str, Any], value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeLockError(f"{label} 必須是 array")
    return cast(list[object], value)


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise RuntimeLockError(f"{label} 欄位不符合 schema")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RuntimeLockError(f"{label} 必須是非空白字串")
    return value


def _safe_name(value: object, label: str) -> str:
    name = _string(value, label)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", name) is None:
        raise RuntimeLockError(f"{label} 格式不安全")
    return name


def _version(value: object, label: str) -> str:
    version = _string(value, label)
    if _VERSION_PATTERN.fullmatch(version) is None:
        raise RuntimeLockError(f"{label} 版本格式不安全")
    return version


def _commit(value: object, label: str) -> str:
    commit = _string(value, label)
    if _COMMIT_PATTERN.fullmatch(commit) is None:
        raise RuntimeLockError(f"{label} 必須是完整 Git commit")
    return commit


def _sha256(value: object, label: str) -> str:
    digest = _string(value, label)
    if _SHA256_PATTERN.fullmatch(digest) is None:
        raise RuntimeLockError(f"{label} 必須是小寫 SHA-256")
    return digest


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RuntimeLockError(f"{label} 必須是 >= {minimum} 的 integer")
    return value


def _port(value: object, label: str) -> int:
    port = _integer(value, label, minimum=1024)
    if port > 65535:
        raise RuntimeLockError(f"{label} 超出合法 port 範圍")
    return port


def _loopback_host(value: object, label: str) -> str:
    host = _string(value, label)
    if host != "127.0.0.1":
        raise RuntimeLockError(f"{label} 必須固定為 127.0.0.1")
    return host


def _https_url(value: object, label: str) -> str:
    url = _string(value, label)
    if (
        not url.startswith("https://")
        or any(character.isspace() for character in url)
        or len(url) > 512
    ):
        raise RuntimeLockError(f"{label} 必須是安全 HTTPS URL")
    return url


def _https_git_url(value: object, label: str) -> str:
    url = _https_url(value, label)
    if not url.endswith(".git"):
        raise RuntimeLockError(f"{label} 必須以 .git 結尾")
    return url


def _workflow_path(value: object, label: str) -> str:
    encoded = _source_path(value, label)
    if encoded not in {
        "docs/workflows/wf_dual_B1.json",
        "docs/workflows/wf10_upscale_opt2.json",
    }:
        raise RuntimeLockError(f"{label} 不是現役 workflow")
    return encoded


def _source_path(value: object, label: str) -> str:
    encoded = _string(value, label)
    if "\\" in encoded or encoded.startswith("/") or len(encoded) > 512:
        raise RuntimeLockError(f"{label} 必須是安全 POSIX 相對路徑")
    path = PurePosixPath(encoded)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeLockError(f"{label} 必須是安全 POSIX 相對路徑")
    return encoded


def _repository_name(value: object, label: str) -> str:
    encoded = _string(value, label)
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", encoded) is None:
        raise RuntimeLockError(f"{label} 必須是 owner/repository")
    return encoded


def _source_revision(value: object, label: str) -> str:
    encoded = _string(value, label)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", encoded) is None:
        raise RuntimeLockError(f"{label} revision 格式不安全")
    return encoded


def _license(value: object, label: str) -> str:
    encoded = _string(value, label)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", encoded) is None:
        raise RuntimeLockError(f"{label} license 格式不安全")
    return encoded
