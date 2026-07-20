#!/usr/bin/env python3
"""以釘定 uv 重新產生 WSL x86_64 的 ComfyUI dependency lock。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

UV_VERSION = "0.11.29"


def main() -> int:
    """驗證 uv 版本並以固定 resolver 參數更新 requirements lock。"""

    repo_root = Path(__file__).resolve().parents[1]
    uv_binary = _find_uv(repo_root)
    version = subprocess.run(
        [str(uv_binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != f"uv {UV_VERSION} (x86_64-unknown-linux-gnu)":
        raise SystemExit(f"需要 uv {UV_VERSION} x86_64 Linux，目前是：{version}")

    environment = os.environ.copy()
    environment.setdefault(
        "UV_CACHE_DIR",
        str(repo_root / ".runtime" / "cache" / "uv"),
    )
    subprocess.run(
        [
            str(uv_binary),
            "pip",
            "compile",
            "runtime/comfy-core-requirements.txt",
            "runtime/comfy-gguf-requirements.txt",
            "--overrides",
            "runtime/comfy-overrides.txt",
            "--python-version",
            "3.12.3",
            "--python-platform",
            "x86_64-manylinux_2_28",
            "--extra-index-url",
            "https://download.pytorch.org/whl/cu130",
            "--index-strategy",
            "unsafe-best-match",
            "--only-binary",
            ":all:",
            "--generate-hashes",
            "--emit-index-url",
            "--emit-index-annotation",
            "--custom-compile-command",
            "python3 scripts/lock_comfy_requirements.py",
            "--output-file",
            "runtime/comfy-requirements.lock.txt",
        ],
        cwd=repo_root,
        env=environment,
        check=True,
    )
    return 0


def _find_uv(repo_root: Path) -> Path:
    configured = os.environ.get("UV_BINARY")
    discovered = shutil.which("uv")
    candidates = [
        Path(configured).expanduser() if configured else None,
        repo_root / ".runtime" / "tools" / "uv",
        Path(discovered) if discovered else None,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise SystemExit("找不到釘定 uv；請先執行 runtime install，或以 UV_BINARY 指定。")


if __name__ == "__main__":
    raise SystemExit(main())
