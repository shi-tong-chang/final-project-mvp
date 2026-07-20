"""Loopback-only ComfyUI HTTP client。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol, cast

import httpx

from app.core.workflow_settings import WorkflowSettings

WorkflowGraph = dict[str, Any]
_MAX_PROTOCOL_BYTES = 8 * 1024 * 1024
_REQUIRED_NODE_CLASSES = (
    "CFGNorm",
    "CLIPLoader",
    "CLIPTextEncodeLumina2",
    "ConditioningZeroOut",
    "FluxKontextImageScale",
    "FluxKontextMultiReferenceLatentMethod",
    "ImageScale",
    "ImageUpscaleWithModel",
    "KSampler",
    "LoadImage",
    "ModelSamplingAuraFlow",
    "SaveImage",
    "TextEncodeQwenImageEditPlus",
    "UNETLoader",
    "UnetLoaderGGUF",
    "UpscaleModelLoader",
    "VAEDecode",
    "VAEDecodeTiled",
    "VAEEncode",
    "VAEEncodeTiled",
    "VAELoader",
)


@dataclass(frozen=True, slots=True)
class ComfyUIStatus:
    """不含路徑或硬體細節的 ComfyUI 狀態。"""

    available: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ComfyImageReference:
    """ComfyUI 核發的相對圖片 identity。"""

    filename: str
    subfolder: str
    folder_type: str

    @property
    def load_image_value(self) -> str:
        """轉成 LoadImage 接受的 server input 相對名稱。"""

        return (
            f"{self.subfolder.rstrip('/')}/{self.filename}"
            if self.subfolder
            else self.filename
        )


class ComfyUIClient(Protocol):
    """可由 unit test fake 取代的 ComfyUI 邊界。"""

    async def status(self) -> ComfyUIStatus:
        """檢查 loopback server，不啟動或修改它。"""

    async def upload_image(
        self,
        filename: str,
        image_bytes: bytes,
        *,
        subfolder: str,
    ) -> ComfyImageReference:
        """上傳 server-owned PNG。"""

    async def queue_prompt(
        self,
        prompt: WorkflowGraph,
        *,
        prompt_id: str,
    ) -> str:
        """送出 server-owned API-format graph 並回傳 prompt ID。"""

    async def wait_for_output(
        self,
        prompt_id: str,
        *,
        output_node_id: str,
    ) -> ComfyImageReference:
        """輪詢 history，直到指定 SaveImage node 完成。"""

    async def download_image(self, reference: ComfyImageReference) -> bytes:
        """抓取已驗證的 ComfyUI output。"""

    async def cancel_prompt(self, prompt_id: str) -> None:
        """只取消指定 prompt，不清空其他使用者 queue。"""

    async def close(self) -> None:
        """關閉持有的 HTTP 資源。"""


class ComfyUIClientError(RuntimeError):
    """對外只提供安全訊息的 ComfyUI transport／protocol 錯誤。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class HttpComfyUIClient:
    """以 HTTP upload → prompt → history → view 驅動固定 workflow。"""

    def __init__(
        self,
        settings: WorkflowSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        timeout = httpx.Timeout(
            settings.request_timeout_seconds,
            connect=settings.connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=settings.comfyui_base_url,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._closed = False

    async def status(self) -> ComfyUIStatus:
        """讀取最小 system status，並隱藏 argv、路徑及硬體細節。"""

        try:
            payload = await self._json_request("GET", "/system_stats")
        except ComfyUIClientError:
            return ComfyUIStatus(
                available=False,
                detail="ComfyUI 尚未啟動或無法連線。",
            )
        if not isinstance(payload.get("system"), dict):
            return ComfyUIStatus(
                available=False,
                detail="ComfyUI 回應格式不相容。",
            )
        try:
            node_payloads = await asyncio.gather(
                *(
                    self._json_request(
                        "GET",
                        f"/object_info/{node_class}",
                    )
                    for node_class in _REQUIRED_NODE_CLASSES
                )
            )
        except ComfyUIClientError:
            return ComfyUIStatus(
                available=False,
                detail="ComfyUI 已連線，但必要節點無法驗證。",
            )
        for node_class, node_info in zip(
            _REQUIRED_NODE_CLASSES,
            node_payloads,
            strict=True,
        ):
            if not isinstance(node_info.get(node_class), dict):
                return ComfyUIStatus(
                    available=False,
                    detail="ComfyUI 已連線，但缺少必要工作流節點。",
                )
        return ComfyUIStatus(
            available=True,
            detail="ComfyUI 已在本機連線，必要節點可用。",
        )

    async def upload_image(
        self,
        filename: str,
        image_bytes: bytes,
        *,
        subfolder: str,
    ) -> ComfyImageReference:
        """以唯一名稱上傳 PNG，絕不覆蓋既有 input。"""

        self._validate_component(filename, allow_slash=False)
        self._validate_component(subfolder, allow_slash=True)
        payload = await self._json_request(
            "POST",
            "/upload/image",
            data={
                "type": "input",
                "subfolder": subfolder,
                "overwrite": "false",
            },
            files={"image": (filename, image_bytes, "image/png")},
        )
        returned_name = payload.get("name")
        returned_subfolder = payload.get("subfolder")
        returned_type = payload.get("type")
        if (
            not isinstance(returned_name, str)
            or not isinstance(returned_subfolder, str)
            or returned_type != "input"
        ):
            raise ComfyUIClientError(
                "COMFYUI_PROTOCOL_ERROR",
                "ComfyUI 圖片上傳回應格式不相容。",
            )
        self._validate_component(returned_name, allow_slash=False)
        self._validate_component(returned_subfolder, allow_slash=True)
        if returned_name != filename or returned_subfolder != subfolder:
            raise ComfyUIClientError(
                "COMFYUI_UPLOAD_IDENTITY_MISMATCH",
                "ComfyUI 沒有保留本次上傳的唯一圖片名稱。",
            )
        return ComfyImageReference(
            filename=returned_name,
            subfolder=returned_subfolder,
            folder_type="input",
        )

    async def queue_prompt(
        self,
        prompt: WorkflowGraph,
        *,
        prompt_id: str,
    ) -> str:
        """送出固定 graph；400 response 不把 node error 洩漏至 browser。"""

        self._validate_prompt_id(prompt_id)
        payload = await self._json_request(
            "POST",
            "/prompt",
            json={"prompt": prompt, "prompt_id": prompt_id},
        )
        returned_prompt_id = payload.get("prompt_id")
        if returned_prompt_id != prompt_id:
            raise ComfyUIClientError(
                "COMFYUI_PROTOCOL_ERROR",
                "ComfyUI 回傳的任務識別碼與請求不一致。",
            )
        return prompt_id

    async def wait_for_output(
        self,
        prompt_id: str,
        *,
        output_node_id: str,
    ) -> ComfyImageReference:
        """以 bounded polling 等待 history terminal item。"""

        self._validate_prompt_id(prompt_id)
        deadline = time.monotonic() + self._settings.prompt_timeout_seconds
        while time.monotonic() < deadline:
            payload = await self._json_request("GET", f"/history/{prompt_id}")
            history_item = payload.get(prompt_id)
            if history_item is None:
                await asyncio.sleep(self._settings.poll_interval_seconds)
                continue
            if not isinstance(history_item, dict):
                raise ComfyUIClientError(
                    "COMFYUI_PROTOCOL_ERROR",
                    "ComfyUI history 回應格式不相容。",
                )
            status = history_item.get("status")
            if isinstance(status, dict) and status.get("status_str") == "error":
                raise ComfyUIClientError(
                    "COMFYUI_EXECUTION_FAILED",
                    "ComfyUI 無法完成圖片工作流。",
                )
            outputs = history_item.get("outputs")
            if not isinstance(outputs, dict):
                await asyncio.sleep(self._settings.poll_interval_seconds)
                continue
            node_output = outputs.get(output_node_id)
            reference = self._extract_output_reference(node_output)
            if reference is not None:
                return reference
            if isinstance(status, dict) and status.get("status_str") == "success":
                raise ComfyUIClientError(
                    "COMFYUI_OUTPUT_MISSING",
                    "ComfyUI 已完成工作流，但沒有預期的圖片輸出。",
                )
            await asyncio.sleep(self._settings.poll_interval_seconds)
        raise ComfyUIClientError(
            "COMFYUI_PROMPT_TIMEOUT",
            "ComfyUI 圖片工作逾時；本次不會自動重送。",
        )

    async def download_image(self, reference: ComfyImageReference) -> bytes:
        """只允許 history 核發的安全相對 output reference。"""

        self._validate_reference(reference)
        return await self._bytes_request(
            "GET",
            "/view",
            params={
                "filename": reference.filename,
                "subfolder": reference.subfolder,
                "type": reference.folder_type,
            },
            max_bytes=self._settings.max_output_bytes,
        )

    async def cancel_prompt(self, prompt_id: str) -> None:
        """分別刪除 pending 與中止 running prompt；兩者皆以 ID 為限。"""

        self._validate_prompt_id(prompt_id)
        with contextlib.suppress(ComfyUIClientError):
            await self._json_request(
                "POST",
                "/queue",
                json={"delete": [prompt_id]},
                allow_empty=True,
            )
        with contextlib.suppress(ComfyUIClientError):
            await self._json_request(
                "POST",
                "/interrupt",
                json={"prompt_id": prompt_id},
                allow_empty=True,
            )

    async def close(self) -> None:
        """關閉 client；可安全重複呼叫。"""

        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    async def _json_request(
        self,
        method: str,
        path: str,
        *,
        allow_empty: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raw = await self._bytes_request(
            method,
            path,
            max_bytes=_MAX_PROTOCOL_BYTES,
            **kwargs,
        )
        if allow_empty and not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ComfyUIClientError(
                "COMFYUI_PROTOCOL_ERROR",
                "ComfyUI 傳回無效 JSON。",
            ) from exc
        if not isinstance(payload, dict):
            raise ComfyUIClientError(
                "COMFYUI_PROTOCOL_ERROR",
                "ComfyUI JSON 回應格式不相容。",
            )
        return cast(dict[str, Any], payload)

    async def _bytes_request(
        self,
        method: str,
        path: str,
        *,
        max_bytes: int,
        **kwargs: Any,
    ) -> bytes:
        if self._closed:
            raise ComfyUIClientError("COMFYUI_CLOSED", "ComfyUI client 已關閉。")
        try:
            async with self._client.stream(method, path, **kwargs) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise ComfyUIClientError(
                        "COMFYUI_REQUEST_FAILED",
                        "ComfyUI 拒絕或無法處理本次請求。",
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ComfyUIClientError(
                            "COMFYUI_RESPONSE_TOO_LARGE",
                            "ComfyUI 回應超過安全大小上限。",
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        except ComfyUIClientError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise ComfyUIClientError(
                "COMFYUI_UNAVAILABLE",
                "ComfyUI 尚未啟動或連線已中斷。",
            ) from exc

    @classmethod
    def _extract_output_reference(
        cls,
        node_output: object,
    ) -> ComfyImageReference | None:
        if not isinstance(node_output, dict):
            return None
        images = node_output.get("images")
        if not isinstance(images, list) or not images:
            return None
        first = images[0]
        if not isinstance(first, dict):
            return None
        filename = first.get("filename")
        subfolder = first.get("subfolder", "")
        folder_type = first.get("type")
        if (
            not isinstance(filename, str)
            or not isinstance(subfolder, str)
            or folder_type != "output"
        ):
            return None
        cls._validate_component(filename, allow_slash=False)
        cls._validate_component(subfolder, allow_slash=True)
        return ComfyImageReference(filename, subfolder, folder_type)

    @classmethod
    def _validate_reference(cls, reference: ComfyImageReference) -> None:
        cls._validate_component(reference.filename, allow_slash=False)
        cls._validate_component(reference.subfolder, allow_slash=True)
        if reference.folder_type != "output":
            raise ComfyUIClientError(
                "COMFYUI_UNSAFE_REFERENCE",
                "ComfyUI 圖片參照類型不安全。",
            )

    @staticmethod
    def _validate_component(value: str, *, allow_slash: bool) -> None:
        if allow_slash and value == "":
            return
        max_length = 512 if allow_slash else 255
        if (
            not value
            or len(value) > max_length
            or "\x00" in value
            or "\\" in value
            or value.startswith("/")
        ):
            raise ComfyUIClientError(
                "COMFYUI_UNSAFE_REFERENCE",
                "ComfyUI 傳回不安全的圖片參照。",
            )
        path = PurePosixPath(value)
        if any(
            part in {"", ".", ".."}
            or len(part) > 128
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part) is None
            for part in path.parts
        ):
            raise ComfyUIClientError(
                "COMFYUI_UNSAFE_REFERENCE",
                "ComfyUI 傳回不安全的圖片參照。",
            )
        if not allow_slash and len(path.parts) != 1:
            raise ComfyUIClientError(
                "COMFYUI_UNSAFE_REFERENCE",
                "ComfyUI 傳回不安全的圖片參照。",
            )

    @staticmethod
    def _validate_prompt_id(prompt_id: str) -> None:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id) is None:
            raise ComfyUIClientError(
                "COMFYUI_UNSAFE_PROMPT_ID",
                "ComfyUI 任務識別碼格式不安全。",
            )
