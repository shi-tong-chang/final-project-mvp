"""Managed models 的 HTTPS 續傳與完成後完整性驗證。"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from runtime.spec import ModelLock

_CHUNK_BYTES = 8 * 1024 * 1024
_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)")


class DownloadError(RuntimeError):
    """下載失敗、server 不支援安全續傳或 blob 身分不符。"""


class HTTPHeaders(Protocol):
    """HTTP headers 的最小讀取介面。"""

    def get(self, name: str) -> str | None:
        """取得單一 header。"""


class HTTPResponse(Protocol):
    """urllib response 的最小測試介面。"""

    status: int
    headers: HTTPHeaders

    def read(self, amount: int = -1) -> bytes:
        """讀取一段 response body。"""

    def geturl(self) -> str:
        """取得 redirect 後 URL。"""

    def __enter__(self) -> HTTPResponse:
        """進入 response context。"""

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """離開 response context。"""


class URLOpener(Protocol):
    """可由測試 fake 取代的 HTTPS opener。"""

    def open(
        self,
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> HTTPResponse:
        """開啟固定 lock URL。"""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """單顆模型下載或已存在驗證結果。"""

    path: Path
    downloaded_bytes: int
    resumed_from: int
    already_present: bool


class ModelDownloader:
    """只寫入 managed model root，使用 `.part` 並於 SHA 成功後發布。"""

    def __init__(
        self,
        model_root: Path,
        *,
        opener: URLOpener | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._model_root = Path(os.path.abspath(model_root.expanduser()))
        self._opener = opener or urllib.request.build_opener()
        self._timeout_seconds = timeout_seconds

    def download(self, model: ModelLock) -> DownloadResult:
        """下載一顆模型；不覆蓋任何既有 final 檔案。"""

        if self._model_root.is_symlink() or (
            self._model_root.exists() and not self._model_root.is_dir()
        ):
            raise DownloadError("managed model root 必須是非 symlink 目錄")
        target = self._model_root / Path(*model.relative_path.parts)
        if target.parent.is_symlink():
            raise DownloadError("managed model 子目錄不可是 symlink")
        if not target.resolve().is_relative_to(self._model_root.resolve()):
            raise DownloadError("模型目標超出 managed model root")
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise DownloadError("無法建立 managed model 目錄") from exc
        part = target.with_name(f"{target.name}.part")
        self._reject_symlink(target)
        self._reject_symlink(part)

        if target.exists():
            try:
                self._verify_file(target, model)
            except DownloadError:
                self._quarantine_invalid_file(target)
            else:
                return DownloadResult(
                    path=target,
                    downloaded_bytes=0,
                    resumed_from=0,
                    already_present=True,
                )

        part_exists = part.exists()
        try:
            resumed_from = part.stat().st_size if part_exists else 0
        except OSError as exc:
            raise DownloadError("無法讀取模型 .part 狀態") from exc
        if resumed_from > model.size_bytes:
            self._quarantine_invalid_file(part)
            part_exists = False
            resumed_from = 0
        if resumed_from == model.size_bytes:
            try:
                self._verify_file(part, model)
            except DownloadError:
                self._quarantine_invalid_file(part)
                part_exists = False
                resumed_from = 0
            else:
                self._publish_part(part, target)
                return DownloadResult(
                    path=target,
                    downloaded_bytes=0,
                    resumed_from=resumed_from,
                    already_present=False,
                )

        request = urllib.request.Request(
            model.url,
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": "final-project-mvp-runtime/1",
            },
            method="GET",
        )
        if resumed_from:
            request.add_header("Range", f"bytes={resumed_from}-")
        try:
            response_context = self._opener.open(
                request,
                timeout=self._timeout_seconds,
            )
            with response_context as response:
                effective_offset = (
                    0 if resumed_from and response.status == 200 else resumed_from
                )
                self._validate_response(response, model, effective_offset)
                downloaded = self._append_response(
                    response,
                    part,
                    append=bool(effective_offset),
                    replace_empty=part_exists and effective_offset == 0,
                    max_bytes=model.size_bytes - effective_offset,
                )
        except DownloadError:
            raise
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise DownloadError(
                f"{model.filename} 下載中斷，可保留 .part 續傳"
            ) from exc

        if effective_offset + downloaded != model.size_bytes:
            raise DownloadError(f"{model.filename} 下載大小不符合 lock")
        self._verify_file(part, model)
        self._publish_part(part, target)
        return DownloadResult(
            path=target,
            downloaded_bytes=downloaded,
            resumed_from=effective_offset,
            already_present=False,
        )

    @staticmethod
    def verify(model_root: Path, model: ModelLock) -> Path:
        """完整計算既有模型 SHA-256，不寫 cache。"""

        root = Path(os.path.abspath(model_root.expanduser()))
        if root.is_symlink() or not root.is_dir():
            raise DownloadError("model root 必須是非 symlink 目錄")
        target = root / Path(*model.relative_path.parts)
        if target.parent.is_symlink():
            raise DownloadError("模型子目錄不可是 symlink")
        if not target.resolve().is_relative_to(root.resolve()):
            raise DownloadError("模型路徑超出 model root")
        ModelDownloader._reject_symlink(target)
        ModelDownloader._verify_file(target, model)
        return target

    @staticmethod
    def _validate_response(
        response: HTTPResponse,
        model: ModelLock,
        resumed_from: int,
    ) -> None:
        final_url = response.geturl()
        if not final_url.startswith("https://"):
            raise DownloadError("模型下載 redirect 不再是 HTTPS")
        if resumed_from:
            if response.status != 206:
                raise DownloadError("下載來源不支援安全 Range 續傳")
            raw_content_range = response.headers.get("Content-Range")
            if not isinstance(raw_content_range, str):
                raise DownloadError("Range response 缺少 Content-Range")
            match = _CONTENT_RANGE.fullmatch(raw_content_range.strip())
            if (
                match is None
                or int(match.group(1)) != resumed_from
                or int(match.group(2)) != model.size_bytes - 1
                or int(match.group(3)) != model.size_bytes
            ):
                raise DownloadError("Range response 與 models lock 不一致")
        elif response.status != 200:
            raise DownloadError("模型下載 server 回應狀態不正確")

        raw_length = response.headers.get("Content-Length")
        expected = model.size_bytes - resumed_from
        if isinstance(raw_length, str):
            try:
                content_length = int(raw_length)
            except ValueError as exc:
                raise DownloadError("模型 Content-Length 格式錯誤") from exc
            if content_length != expected:
                raise DownloadError("模型 Content-Length 與 lock 不一致")

    @staticmethod
    def _append_response(
        response: HTTPResponse,
        path: Path,
        *,
        append: bool,
        replace_empty: bool,
        max_bytes: int,
    ) -> int:
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
            raise DownloadError("下載中的 .part 狀態發生衝突") from exc
        total = 0
        with os.fdopen(descriptor, "ab" if append else "wb") as output:
            total = ModelDownloader._copy_response(
                response,
                output,
                max_bytes=max_bytes,
            )
            output.flush()
            os.fsync(output.fileno())
        return total

    @staticmethod
    def _copy_response(
        response: HTTPResponse,
        output: BinaryIO,
        *,
        max_bytes: int,
    ) -> int:
        total = 0
        while True:
            chunk = response.read(_CHUNK_BYTES)
            if not chunk:
                return total
            total += len(chunk)
            if total > max_bytes:
                raise DownloadError("模型 response 超過 models lock 固定大小")
            output.write(chunk)

    @staticmethod
    def _verify_file(path: Path, model: ModelLock) -> None:
        try:
            stat_result = path.stat()
        except OSError as exc:
            raise DownloadError(f"找不到模型：{model.filename}") from exc
        if not path.is_file() or stat_result.st_size != model.size_bytes:
            raise DownloadError(f"{model.filename} 大小與 models lock 不一致")
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                while chunk := source.read(_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError as exc:
            raise DownloadError(f"無法讀取模型：{model.filename}") from exc
        if digest.hexdigest() != model.sha256:
            raise DownloadError(f"{model.filename} SHA-256 與 models lock 不一致")

    @staticmethod
    def _publish_part(part: Path, target: Path) -> None:
        """以 no-replace 語意發布已驗證的 `.part`。"""

        try:
            os.link(part, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise DownloadError("模型 final 檔案在發布時發生衝突") from exc
        except OSError as exc:
            raise DownloadError("無法安全發布已驗證的模型") from exc
        try:
            part.unlink()
        except OSError as exc:
            raise DownloadError("模型已發布，但無法清理 .part") from exc

    @staticmethod
    def _quarantine_invalid_file(path: Path) -> Path:
        """將不符 lock 的 managed 檔案移到同目錄隔離名稱。

        先建立 hard link，再移除原名稱；新名稱以內容雜湊與大小推導，
        並使用 exclusive link 避免覆寫任何既有檔案。
        """

        ModelDownloader._reject_symlink(path)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise DownloadError("無法讀取待隔離的 managed 檔案") from exc
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "rb") as source:
                file_stat = os.fstat(source.fileno())
                if not stat.S_ISREG(file_stat.st_mode):
                    raise DownloadError("待隔離的 managed 路徑不是一般檔案")
                while chunk := source.read(_CHUNK_BYTES):
                    digest.update(chunk)
        except DownloadError:
            raise
        except OSError as exc:
            raise DownloadError("無法讀取待隔離的 managed 檔案") from exc

        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", path.name)[:80] or "blob"
        base_name = (
            f".{safe_name}.fpmvp-rejected-{file_stat.st_size}-{digest.hexdigest()[:16]}"
        )
        for suffix in range(10_000):
            candidate = path.with_name(
                base_name if suffix == 0 else f"{base_name}.{suffix}"
            )
            try:
                os.link(path, candidate, follow_symlinks=False)
            except FileExistsError:
                continue
            except OSError as exc:
                raise DownloadError("無法安全隔離 managed 檔案") from exc

            try:
                current = path.stat(follow_symlinks=False)
                quarantined = candidate.stat(follow_symlinks=False)
                identity = (file_stat.st_dev, file_stat.st_ino)
                if (current.st_dev, current.st_ino) != identity or (
                    quarantined.st_dev,
                    quarantined.st_ino,
                ) != identity:
                    candidate.unlink(missing_ok=True)
                    raise DownloadError("managed 檔案隔離時發生競態")
                path.unlink()
            except DownloadError:
                raise
            except OSError as exc:
                with suppress(OSError):
                    candidate.unlink(missing_ok=True)
                raise DownloadError("無法安全隔離 managed 檔案") from exc
            return candidate
        raise DownloadError("managed 檔案隔離名稱已用盡")

    @staticmethod
    def _reject_symlink(path: Path) -> None:
        if path.is_symlink():
            raise DownloadError("managed model 檔案不可是 symlink")
