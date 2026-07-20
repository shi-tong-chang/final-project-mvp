"""從固定 release asset 安全 bootstrap uv，不使用 curl-pipe latest。"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tarfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from runtime.downloads import HTTPResponse, URLOpener
from runtime.spec import ToolLock

_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)")
_VERSION_TIMEOUT_SECONDS = 10.0


class BootstrapError(RuntimeError):
    """uv asset 下載、雜湊或 archive 結構不安全。"""


class PinnedUvBootstrap:
    """下載、驗證並只解出 archive 內的 `uv` executable。"""

    def __init__(
        self,
        tools_root: Path,
        tool_lock: ToolLock,
        *,
        opener: URLOpener | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.tools_root = Path(os.path.abspath(tools_root.expanduser()))
        self.tool_lock = tool_lock
        self.opener = opener or urllib.request.build_opener()
        self.timeout_seconds = timeout_seconds
        self.archive = self.tools_root / f"uv-{tool_lock.version}.tar.gz"
        self.executable = self.tools_root / "uv"

    def ensure(self) -> Path:
        """回傳已由固定 archive 驗證並抽出的 uv。"""

        if self.tools_root.is_symlink() or (
            self.tools_root.exists() and not self.tools_root.is_dir()
        ):
            raise BootstrapError("uv managed tools root 必須是非 symlink 目錄")
        try:
            self.tools_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise BootstrapError("無法建立 managed tools 目錄") from exc
        if self.archive.is_symlink() or self.executable.is_symlink():
            raise BootstrapError("uv managed tool 不可是 symlink")
        if not self.archive.exists() or not self._hash_matches(self.archive):
            self._download_archive()
        if not self._hash_matches(self.archive):
            raise BootstrapError("uv archive SHA-256 與 runtime lock 不一致")
        executable_digest, executable_size = self._archive_executable_identity()
        if not self._executable_matches(executable_digest, executable_size):
            self._extract_executable(executable_digest, executable_size)
        if not self._executable_matches(executable_digest, executable_size):
            raise BootstrapError("uv executable 與釘定 archive 不一致")
        self._validate_version()
        return self.executable

    def _download_archive(self, *, allow_restart: bool = True) -> None:
        part = self.archive.with_name(f"{self.archive.name}.part")
        if part.is_symlink():
            raise BootstrapError("uv archive .part 不可是 symlink")
        part_exists = part.exists()
        try:
            offset = part.stat().st_size if part_exists else 0
        except OSError as exc:
            raise BootstrapError("無法讀取 uv archive .part 狀態") from exc
        if part_exists and self._hash_matches(part):
            try:
                os.replace(part, self.archive)
            except OSError as exc:
                raise BootstrapError("無法發布已驗證的 uv archive") from exc
            return
        if offset > _MAX_ARCHIVE_BYTES:
            raise BootstrapError("uv archive .part 超過安全上限")
        request = urllib.request.Request(
            self.tool_lock.url,
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": "final-project-mvp-runtime/1",
            },
            method="GET",
        )
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        restart_from_zero = False
        expected_total = 0
        try:
            with self.opener.open(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                if offset and response.status == 416 and allow_restart:
                    restart_from_zero = True
                else:
                    effective_offset = (
                        0 if offset and response.status == 200 else offset
                    )
                    expected_total = self._validate_response(
                        response,
                        effective_offset,
                    )
                    self._append_response(
                        part,
                        response,
                        append=bool(effective_offset),
                        replace_empty=part_exists and effective_offset == 0,
                    )
        except BootstrapError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset and allow_restart:
                self._reset_partial(part)
                self._download_archive(allow_restart=False)
                return
            raise BootstrapError("uv 下載中斷，可保留 .part 續傳") from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise BootstrapError("uv 下載中斷，可保留 .part 續傳") from exc
        if restart_from_zero:
            self._reset_partial(part)
            self._download_archive(allow_restart=False)
            return
        try:
            actual_size = part.stat().st_size
        except OSError as exc:
            raise BootstrapError("無法讀取 uv archive .part 狀態") from exc
        if actual_size != expected_total:
            raise BootstrapError("uv archive 下載大小與 HTTP metadata 不一致")
        if not self._hash_matches(part):
            if offset and allow_restart:
                self._reset_partial(part)
                self._download_archive(allow_restart=False)
                return
            raise BootstrapError("uv archive SHA-256 與 runtime lock 不一致")
        try:
            os.replace(part, self.archive)
        except OSError as exc:
            raise BootstrapError("無法發布已驗證的 uv archive") from exc

    @staticmethod
    def _reset_partial(part: Path) -> None:
        try:
            descriptor = os.open(
                part,
                os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
            )
        except OSError as exc:
            raise BootstrapError("無法安全重設損壞的 uv .part") from exc
        os.close(descriptor)

    def _validate_response(self, response: HTTPResponse, offset: int) -> int:
        if not response.geturl().startswith("https://"):
            raise BootstrapError("uv redirect 不再是 HTTPS")
        raw_length = response.headers.get("Content-Length")
        if raw_length is None:
            raise BootstrapError("uv response 缺少合法 Content-Length")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise BootstrapError("uv response 缺少合法 Content-Length") from exc
        if offset:
            if response.status != 206:
                raise BootstrapError("uv 下載來源不支援安全 Range 續傳")
            raw_range = response.headers.get("Content-Range")
            if not isinstance(raw_range, str):
                raise BootstrapError("uv Range response 缺少 Content-Range")
            match = _CONTENT_RANGE.fullmatch(raw_range.strip())
            if match is None or int(match.group(1)) != offset:
                raise BootstrapError("uv Content-Range 起點不一致")
            total = int(match.group(3))
            if int(match.group(2)) + 1 != total or length != total - offset:
                raise BootstrapError("uv Content-Range 大小不一致")
        else:
            if response.status != 200:
                raise BootstrapError("uv download HTTP status 不正確")
            total = length
        if total < 1 or total > _MAX_ARCHIVE_BYTES:
            raise BootstrapError("uv archive 大小超過安全上限")
        return total

    @staticmethod
    def _append_response(
        path: Path,
        response: HTTPResponse,
        *,
        append: bool,
        replace_empty: bool,
    ) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
        if append:
            flags |= os.O_APPEND
        elif replace_empty:
            flags |= os.O_TRUNC
        else:
            flags |= os.O_EXCL
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise BootstrapError("uv archive .part 狀態衝突") from exc
        total = path.stat().st_size if append else 0
        with os.fdopen(descriptor, "ab" if append else "wb") as output:
            while chunk := response.read(_CHUNK_BYTES):
                total += len(chunk)
                if total > _MAX_ARCHIVE_BYTES:
                    raise BootstrapError("uv archive 超過安全上限")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())

    def _archive_executable_identity(self) -> tuple[str, int]:
        """從已驗證 archive 推導唯一 uv 的雜湊與大小。"""

        try:
            with tarfile.open(self.archive, mode="r:gz") as archive:
                member = self._single_executable_member(archive)
                source = archive.extractfile(member)
                if source is None:
                    raise BootstrapError("無法讀取 uv executable")
                digest = hashlib.sha256()
                copied = 0
                while chunk := source.read(_CHUNK_BYTES):
                    copied += len(chunk)
                    if copied > member.size:
                        raise BootstrapError("uv executable 解壓大小不一致")
                    digest.update(chunk)
                if copied != member.size:
                    raise BootstrapError("uv executable 解壓大小不一致")
                return digest.hexdigest(), copied
        except BootstrapError:
            raise
        except (OSError, tarfile.TarError) as exc:
            raise BootstrapError("uv archive 無法安全讀取") from exc

    @staticmethod
    def _single_executable_member(archive: tarfile.TarFile) -> tarfile.TarInfo:
        candidates = [
            member
            for member in archive.getmembers()
            if member.isfile()
            and not member.issym()
            and not member.islnk()
            and Path(member.name).name == "uv"
        ]
        if len(candidates) != 1:
            raise BootstrapError("uv archive executable 結構不符合預期")
        member = candidates[0]
        if member.size < 1 or member.size > _MAX_ARCHIVE_BYTES:
            raise BootstrapError("uv executable 大小不安全")
        return member

    def _executable_matches(self, expected_digest: str, expected_size: int) -> bool:
        if self.executable.is_symlink():
            raise BootstrapError("uv managed tool 不可是 symlink")
        try:
            executable_stat = self.executable.stat()
            if (
                not stat.S_ISREG(executable_stat.st_mode)
                or executable_stat.st_size != expected_size
                or not executable_stat.st_mode & stat.S_IXUSR
            ):
                return False
            digest = hashlib.sha256()
            with self.executable.open("rb") as source:
                while chunk := source.read(_CHUNK_BYTES):
                    digest.update(chunk)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise BootstrapError("無法驗證 uv executable") from exc
        return digest.hexdigest() == expected_digest

    def _extract_executable(
        self,
        expected_digest: str,
        expected_size: int,
    ) -> None:
        try:
            with tarfile.open(self.archive, mode="r:gz") as archive:
                member = self._single_executable_member(archive)
                if member.size != expected_size:
                    raise BootstrapError("uv executable 身分在驗證期間改變")
                source = archive.extractfile(member)
                if source is None:
                    raise BootstrapError("無法讀取 uv executable")
                temporary = self.executable.with_name(f".uv.{uuid.uuid4().hex}.tmp")
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o700,
                )
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        copied = 0
                        digest = hashlib.sha256()
                        while chunk := source.read(_CHUNK_BYTES):
                            copied += len(chunk)
                            if copied > member.size:
                                raise BootstrapError("uv executable 解壓大小不一致")
                            output.write(chunk)
                            digest.update(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    if copied != member.size or digest.hexdigest() != expected_digest:
                        raise BootstrapError("uv executable 解壓大小不一致")
                    os.replace(temporary, self.executable)
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, tarfile.TarError) as exc:
            raise BootstrapError("uv archive 無法安全解壓") from exc

    def _validate_version(self) -> None:
        """執行已驗證 binary，並比對完整 version token。"""

        try:
            completed = subprocess.run(
                [str(self.executable), "--version"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                shell=False,
                timeout=_VERSION_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise BootstrapError("uv --version 無法安全執行") from exc
        tokens = completed.stdout.strip().split()
        if (
            completed.returncode != 0
            or len(tokens) < 2
            or tokens[0] != "uv"
            or tokens[1] != self.tool_lock.version
        ):
            raise BootstrapError("uv --version 與 runtime lock 不一致")

    def _hash_matches(self, path: Path) -> bool:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                while chunk := source.read(_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError:
            return False
        return digest.hexdigest() == self.tool_lock.sha256
