from __future__ import annotations

import hashlib
import tarfile
from io import BytesIO
from pathlib import Path
from urllib.request import Request

import pytest

from runtime.bootstrap import BootstrapError, PinnedUvBootstrap
from runtime.downloads import (
    DownloadError,
    HTTPHeaders,
    HTTPResponse,
    ModelDownloader,
)
from runtime.spec import ModelLock, ToolLock, load_runtime_lock


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
        status: int = 200,
        url: str = "https://download.example/asset",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers: HTTPHeaders = FakeHeaders(
            headers or {"Content-Length": str(len(body))}
        )
        self._body = BytesIO(body)
        self._url = url

    def read(self, amount: int = -1) -> bytes:
        return self._body.read(amount)

    def geturl(self) -> str:
        return self._url

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
        if not self.responses:
            raise OSError("offline")
        return self.responses.pop(0)


def _model(content: bytes) -> ModelLock:
    return ModelLock(
        filename="model.bin",
        subdir="unet",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        required_by=("docs/workflows/wf_dual_B1.json",),
        url="https://download.example/model.bin",
        source=load_runtime_lock().models[0].source,
        license="Apache-2.0",
    )


def _uv_archive(script: bytes) -> bytes:
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        member = tarfile.TarInfo("uv-x86_64-unknown-linux-gnu/uv")
        member.mode = 0o755
        member.size = len(script)
        archive.addfile(member, BytesIO(script))
    return output.getvalue()


def _uv_lock(archive: bytes) -> ToolLock:
    return ToolLock(
        version="0.11.29",
        url="https://download.example/uv.tar.gz",
        sha256=hashlib.sha256(archive).hexdigest(),
    )


@pytest.mark.parametrize("invalid", [b"X", b"XXXXXXXX"])
def test_invalid_final_is_quarantined_then_downloaded_without_overwrite(
    tmp_path: Path,
    invalid: bytes,
) -> None:
    content = b"abcdefgh"
    model = _model(content)
    target = tmp_path / "unet/model.bin"
    target.parent.mkdir()
    target.write_bytes(invalid)
    digest_prefix = hashlib.sha256(invalid).hexdigest()[:16]
    reserved = target.with_name(
        f".model.bin.fpmvp-rejected-{len(invalid)}-{digest_prefix}"
    )
    reserved.write_bytes(b"user-owned collision sentinel")
    opener = FakeOpener([FakeResponse(content)])

    result = ModelDownloader(tmp_path, opener=opener).download(model)

    assert result.path.read_bytes() == content
    assert result.already_present is False
    assert reserved.read_bytes() == b"user-owned collision sentinel"
    rejected = sorted(target.parent.glob(".model.bin.fpmvp-rejected-*"))
    assert len(rejected) == 2
    assert rejected[1].name.endswith(".1")
    assert rejected[1].read_bytes() == invalid


def test_complete_bad_sha_part_is_quarantined_then_redownloaded(
    tmp_path: Path,
) -> None:
    content = b"abcdefgh"
    invalid = b"12345678"
    model = _model(content)
    part = tmp_path / "unet/model.bin.part"
    part.parent.mkdir()
    part.write_bytes(invalid)
    opener = FakeOpener([FakeResponse(content)])

    result = ModelDownloader(tmp_path, opener=opener).download(model)

    assert result.path.read_bytes() == content
    assert not part.exists()
    rejected = list(part.parent.glob(".model.bin.part.fpmvp-rejected-*"))
    assert len(rejected) == 1
    assert rejected[0].read_bytes() == invalid
    assert "Range" not in opener.requests[0].headers


def test_model_transport_failure_remains_typed(tmp_path: Path) -> None:
    model = _model(b"abcdefgh")

    with pytest.raises(DownloadError, match="下載中斷"):
        ModelDownloader(tmp_path, opener=FakeOpener([])).download(model)


def test_bootstrap_revalidates_and_repairs_tampered_executable(
    tmp_path: Path,
) -> None:
    script = b"#!/bin/sh\nprintf 'uv 0.11.29 (fixture)\\n'\n"
    archive = _uv_archive(script)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "uv-0.11.29.tar.gz").write_bytes(archive)
    bootstrap = PinnedUvBootstrap(tools, _uv_lock(archive))

    executable = bootstrap.ensure()
    executable.write_bytes(b"#!/bin/sh\nprintf 'uv 0.11.290\\n'\n")
    executable.chmod(0o700)

    assert bootstrap.ensure() == executable
    assert executable.read_bytes() == script


def test_bootstrap_revalidates_archive_before_trusting_existing_uv(
    tmp_path: Path,
) -> None:
    script = b"#!/bin/sh\nprintf 'uv 0.11.29\\n'\n"
    archive = _uv_archive(script)
    tools = tmp_path / "tools"
    tools.mkdir()
    archive_path = tools / "uv-0.11.29.tar.gz"
    archive_path.write_bytes(archive)
    lock = _uv_lock(archive)
    PinnedUvBootstrap(tools, lock).ensure()
    archive_path.write_bytes(b"tampered archive")
    opener = FakeOpener([FakeResponse(archive)])

    executable = PinnedUvBootstrap(tools, lock, opener=opener).ensure()

    assert executable.read_bytes() == script
    assert archive_path.read_bytes() == archive
    assert len(opener.requests) == 1


def test_bootstrap_publishes_complete_verified_partial_without_network(
    tmp_path: Path,
) -> None:
    script = b"#!/bin/sh\nprintf 'uv 0.11.29\\n'\n"
    archive = _uv_archive(script)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "uv-0.11.29.tar.gz.part").write_bytes(archive)

    executable = PinnedUvBootstrap(
        tools,
        _uv_lock(archive),
        opener=FakeOpener([]),
    ).ensure()

    assert executable.is_file()
    assert (tools / "uv-0.11.29.tar.gz").read_bytes() == archive
    assert not (tools / "uv-0.11.29.tar.gz.part").exists()


def test_bootstrap_restarts_once_when_resumed_prefix_has_bad_hash(
    tmp_path: Path,
) -> None:
    script = b"#!/bin/sh\nprintf 'uv 0.11.29\\n'\n"
    archive = _uv_archive(script)
    corrupt_prefix = b"X" * 8
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "uv-0.11.29.tar.gz.part").write_bytes(corrupt_prefix)
    opener = FakeOpener(
        [
            FakeResponse(
                archive[len(corrupt_prefix) :],
                status=206,
                headers={
                    "Content-Length": str(len(archive) - len(corrupt_prefix)),
                    "Content-Range": (
                        f"bytes {len(corrupt_prefix)}-{len(archive) - 1}/{len(archive)}"
                    ),
                },
            ),
            FakeResponse(archive),
        ]
    )

    executable = PinnedUvBootstrap(
        tools,
        _uv_lock(archive),
        opener=opener,
    ).ensure()

    assert executable.is_file()
    assert len(opener.requests) == 2
    assert opener.requests[0].headers["Range"] == f"bytes={len(corrupt_prefix)}-"
    assert "Range" not in opener.requests[1].headers


def test_bootstrap_version_requires_exact_token(tmp_path: Path) -> None:
    script = b"#!/bin/sh\nprintf 'uv 0.11.290 (not-the-pin)\\n'\n"
    archive = _uv_archive(script)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "uv-0.11.29.tar.gz").write_bytes(archive)

    with pytest.raises(BootstrapError, match="--version"):
        PinnedUvBootstrap(tools, _uv_lock(archive)).ensure()


def test_bootstrap_transport_failure_remains_typed(tmp_path: Path) -> None:
    archive = _uv_archive(b"#!/bin/sh\nprintf 'uv 0.11.29\\n'\n")

    with pytest.raises(BootstrapError, match="下載中斷"):
        PinnedUvBootstrap(
            tmp_path / "tools",
            _uv_lock(archive),
            opener=FakeOpener([]),
        ).ensure()
