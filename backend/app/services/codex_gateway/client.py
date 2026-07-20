"""Codex app-server JSONL stdio adapter。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.gateway_settings import GatewaySettings


@dataclass(frozen=True, slots=True)
class CodexClientStatus:
    """Codex binary 與 child process 狀態。"""

    is_available: bool
    is_connected: bool
    binary_name: str
    detail: str


@dataclass(frozen=True, slots=True)
class CodexThread:
    """Codex 核發的 opaque thread identity。"""

    thread_id: str


@dataclass(frozen=True, slots=True)
class CodexTurn:
    """完成 turn 的最終文字。"""

    turn_id: str
    response: str


class CodexGatewayClient(Protocol):
    """Application service 可注入 fake 的 Codex client 邊界。"""

    async def status(self) -> CodexClientStatus:
        """回傳不含秘密的可用狀態。"""

    async def start_thread(self) -> CodexThread:
        """建立唯讀 Codex thread。"""

    async def run_turn(self, thread_id: str, message: str) -> CodexTurn:
        """完成一個 turn 並回傳最終 agent message。"""

    async def close(self) -> None:
        """關閉 client 擁有的資源。"""


class CodexAppServerError(RuntimeError):
    """Codex process、transport 或 protocol 的安全錯誤。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _ProtocolThread(_ProtocolModel):
    id: str = Field(min_length=1)


class _ThreadStartPayload(_ProtocolModel):
    thread: _ProtocolThread


class _ProtocolItem(_ProtocolModel):
    type: str
    text: str | None = None


class _ProtocolTurn(_ProtocolModel):
    id: str = Field(min_length=1)
    status: str
    items: tuple[_ProtocolItem, ...] = ()
    error: object | None = None


class _TurnStartPayload(_ProtocolModel):
    turn: _ProtocolTurn


class _TurnCompletedParams(_ProtocolModel):
    threadId: str = Field(min_length=1)
    turn: _ProtocolTurn


class _ItemCompletedParams(_ProtocolModel):
    threadId: str = Field(min_length=1)
    turnId: str = Field(min_length=1)
    item: _ProtocolItem


class _AgentMessageDeltaParams(_ProtocolModel):
    threadId: str = Field(min_length=1)
    turnId: str = Field(min_length=1)
    delta: str


ProcessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]
TurnKey = tuple[str, str]


class CodexAppServerClient:
    """管理單一 `codex app-server` child process 與 JSONL lifecycle。

    Side effects:
        第一次建立 thread 時才啟動本機 Codex child process。所有背景 task
        都由本 instance 保存 handle，`close()` 時會取消並回收。
    """

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self._settings = settings
        self._process_factory = process_factory or asyncio.create_subprocess_exec
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending_requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._turn_waiters: dict[
            TurnKey,
            asyncio.Future[_TurnCompletedParams],
        ] = {}
        self._turn_messages: dict[TurnKey, list[str]] = {}
        self._turn_deltas: dict[TurnKey, list[str]] = {}
        self._early_turn_completions: dict[TurnKey, _TurnCompletedParams] = {}
        self._starting_thread_id: str | None = None
        self._active_turn_key: TurnKey | None = None
        self._request_id = 0
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._is_closed = False

    async def status(self) -> CodexClientStatus:
        """檢查 binary 與目前 child process，不觸發登入或模型請求。"""

        executable = shutil.which(self._settings.codex_binary)
        is_connected = self._is_transport_alive()
        if executable is None:
            return CodexClientStatus(
                is_available=False,
                is_connected=False,
                binary_name=Path(self._settings.codex_binary).name,
                detail="找不到本機 Codex CLI；請先安裝並完成登入。",
            )
        return CodexClientStatus(
            is_available=True,
            is_connected=is_connected,
            binary_name=Path(executable).name,
            detail=(
                "Codex 已連線。"
                if is_connected
                else "已找到 Codex CLI；將在首則對話時驗證登入並連線。"
            ),
        )

    async def start_thread(self) -> CodexThread:
        """lazy start app-server，再建立唯讀且 fail-closed 的 thread。"""

        await self._ensure_started()
        params: dict[str, object] = {
            "cwd": str(self._settings.codex_cwd),
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": self._settings.thread_ephemeral,
        }
        if self._settings.codex_model is not None:
            params["model"] = self._settings.codex_model
        try:
            payload = await asyncio.wait_for(
                self._request("thread/start", params),
                timeout=self._settings.startup_timeout_seconds,
            )
        except TimeoutError as exc:
            raise CodexAppServerError(
                "CODEX_THREAD_START_TIMEOUT",
                "Codex 建立對話逾時；請確認本機登入與 CLI 狀態。",
            ) from exc
        try:
            validated = _ThreadStartPayload.model_validate(payload)
        except ValidationError as exc:
            raise CodexAppServerError(
                "CODEX_PROTOCOL_ERROR",
                "Codex thread/start 回應格式與目前 Gateway 不相容。",
            ) from exc
        return CodexThread(thread_id=validated.thread.id)

    async def run_turn(self, thread_id: str, message: str) -> CodexTurn:
        """傳送 UTF-8 文字並等待同一 thread 的 terminal notification。"""

        await self._ensure_started()
        async with self._turn_lock:
            started_turn_id: str | None = None
            turn_key: TurnKey | None = None
            completion: asyncio.Future[_TurnCompletedParams] | None = None
            self._discard_thread_buffers(thread_id)
            self._starting_thread_id = thread_id
            try:
                payload = await asyncio.wait_for(
                    self._request(
                        "turn/start",
                        {
                            "threadId": thread_id,
                            "input": [{"type": "text", "text": message}],
                        },
                    ),
                    timeout=self._settings.startup_timeout_seconds,
                )
                try:
                    started = _TurnStartPayload.model_validate(payload)
                except ValidationError as exc:
                    raise CodexAppServerError(
                        "CODEX_PROTOCOL_ERROR",
                        "Codex turn/start 回應格式與目前 Gateway 不相容。",
                    ) from exc
                started_turn_id = started.turn.id
                turn_key = (thread_id, started_turn_id)
                self._starting_thread_id = None
                self._active_turn_key = turn_key
                self._discard_thread_buffers(thread_id, keep=turn_key)
                completion = asyncio.get_running_loop().create_future()
                self._turn_waiters[turn_key] = completion
                early_completion = self._early_turn_completions.pop(turn_key, None)
                if early_completion is not None:
                    completion.set_result(early_completion)
                completed = await asyncio.wait_for(
                    completion,
                    timeout=self._settings.turn_timeout_seconds,
                )
                if completed.turn.id != started.turn.id:
                    raise CodexAppServerError(
                        "CODEX_PROTOCOL_ERROR",
                        "Codex turn identity 不一致，已停止回傳內容。",
                    )
                if completed.turn.status != "completed":
                    raise CodexAppServerError(
                        "CODEX_TURN_FAILED",
                        "Codex 未能完成這一輪對話；請檢查本機登入與用量狀態。",
                    )
                response = self._extract_response(turn_key, completed.turn)
                if not response:
                    raise CodexAppServerError(
                        "CODEX_EMPTY_RESPONSE",
                        "Codex 已完成 turn，但沒有可顯示的文字回覆。",
                    )
                return CodexTurn(turn_id=completed.turn.id, response=response)
            except TimeoutError as exc:
                if started_turn_id is not None:
                    await self._interrupt_timed_out_turn(thread_id, started_turn_id)
                raise CodexAppServerError(
                    "CODEX_TURN_TIMEOUT",
                    "Codex 回覆逾時；本輪不會由 Gateway 自動重送。",
                ) from exc
            finally:
                self._starting_thread_id = None
                self._active_turn_key = None
                if turn_key is not None:
                    self._turn_waiters.pop(turn_key, None)
                    self._turn_messages.pop(turn_key, None)
                    self._turn_deltas.pop(turn_key, None)
                    self._early_turn_completions.pop(turn_key, None)
                self._discard_thread_buffers(thread_id)

    async def close(self) -> None:
        """終止 child process 並等待全部 owned tasks 收尾。"""

        if self._is_closed:
            return
        self._is_closed = True
        process = self._process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self._settings.shutdown_timeout_seconds,
                )
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

        tasks = [
            task for task in (self._reader_task, self._stderr_task) if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._fail_pending(
            CodexAppServerError("CODEX_CLOSED", "Codex Gateway 已關閉。")
        )
        self._process = None

    async def _ensure_started(self) -> None:
        if self._is_closed:
            raise CodexAppServerError("CODEX_CLOSED", "Codex Gateway 已關閉。")
        if self._is_transport_alive():
            return
        async with self._start_lock:
            if self._is_transport_alive():
                return
            if self._process is not None:
                await self._stop_failed_process()
            executable = shutil.which(self._settings.codex_binary)
            if executable is None:
                raise CodexAppServerError(
                    "CODEX_UNAVAILABLE",
                    "找不到本機 Codex CLI；請先安裝並完成登入。",
                )
            try:
                process = await self._process_factory(
                    executable,
                    "app-server",
                    "--listen",
                    "stdio://",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self._settings.codex_cwd,
                    limit=self._settings.protocol_line_limit_bytes,
                )
            except OSError as exc:
                raise CodexAppServerError(
                    "CODEX_START_FAILED",
                    "Codex app-server 無法啟動；請檢查本機 CLI 與執行權限。",
                ) from exc
            if (
                process.stdin is None
                or process.stdout is None
                or process.stderr is None
            ):
                process.terminate()
                await process.wait()
                raise CodexAppServerError(
                    "CODEX_START_FAILED",
                    "Codex app-server 未提供完整 stdio transport。",
                )
            self._process = process
            self._reader_task = asyncio.create_task(
                self._reader_loop(process),
                name="codex-gateway-jsonl-reader",
            )
            self._stderr_task = asyncio.create_task(
                self._drain_stderr(process),
                name="codex-gateway-stderr-drainer",
            )
            try:
                await asyncio.wait_for(
                    self._request(
                        "initialize",
                        {
                            "clientInfo": {
                                "name": "storyboard_codex_gateway",
                                "title": "Storyboard Codex Gateway",
                                "version": "0.1.0",
                            }
                        },
                    ),
                    timeout=self._settings.startup_timeout_seconds,
                )
                await self._send(
                    {"method": "initialized", "params": {}},
                )
            except (TimeoutError, CodexAppServerError) as exc:
                await self._stop_failed_process()
                if isinstance(exc, CodexAppServerError):
                    raise
                raise CodexAppServerError(
                    "CODEX_START_TIMEOUT",
                    "Codex app-server 初始化逾時；請確認本機 CLI 狀態。",
                ) from exc

    async def _request(
        self,
        method: str,
        params: dict[str, object],
    ) -> dict[str, Any]:
        process = self._process
        if process is None or process.returncode is not None:
            raise CodexAppServerError(
                "CODEX_DISCONNECTED",
                "Codex app-server 尚未連線或已意外停止。",
            )
        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_requests[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending_requests.pop(request_id, None)

    async def _send(self, message: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CodexAppServerError(
                "CODEX_DISCONNECTED",
                "Codex app-server transport 已關閉。",
            )
        encoded = (
            json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode()
        async with self._write_lock:
            process.stdin.write(encoded)
            try:
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise CodexAppServerError(
                    "CODEX_DISCONNECTED",
                    "Codex app-server transport 已中斷。",
                ) from exc

    async def _reader_loop(self, process: asyncio.subprocess.Process) -> None:
        stdout = cast(asyncio.StreamReader, process.stdout)
        try:
            while True:
                try:
                    line = await stdout.readline()
                except ValueError as exc:
                    error = CodexAppServerError(
                        "CODEX_PROTOCOL_ERROR",
                        "Codex app-server 的單筆 JSONL 超過安全大小上限。",
                    )
                    self._fail_pending(error)
                    raise error from exc
                if not line:
                    break
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    self._fail_pending(
                        CodexAppServerError(
                            "CODEX_PROTOCOL_ERROR",
                            "Codex app-server 傳回無效 JSONL。",
                        )
                    )
                    raise CodexAppServerError(
                        "CODEX_PROTOCOL_ERROR",
                        "Codex app-server 傳回無效 JSONL。",
                    ) from exc
                if not isinstance(message, dict):
                    continue
                await self._dispatch_message(cast(dict[str, Any], message))
        except asyncio.CancelledError:
            raise
        except CodexAppServerError:
            return
        finally:
            if not self._is_closed:
                self._fail_pending(
                    CodexAppServerError(
                        "CODEX_DISCONNECTED",
                        "Codex app-server 已意外停止。",
                    )
                )
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.terminate()

    async def _dispatch_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if isinstance(method, str) and request_id is not None:
            await self._deny_server_request(request_id, method)
            return
        if request_id is not None:
            if not isinstance(request_id, int):
                return
            future = self._pending_requests.get(request_id)
            if future is None or future.done():
                return
            error = message.get("error")
            if error is not None:
                future.set_exception(
                    CodexAppServerError(
                        "CODEX_REQUEST_FAILED",
                        "Codex 拒絕請求；請檢查本機登入、設定與用量狀態。",
                    )
                )
                return
            result = message.get("result")
            if not isinstance(result, dict):
                future.set_exception(
                    CodexAppServerError(
                        "CODEX_PROTOCOL_ERROR",
                        "Codex response 缺少 object result。",
                    )
                )
                return
            future.set_result(cast(dict[str, Any], result))
            return
        if isinstance(method, str):
            await self._handle_notification(method, message.get("params"))

    async def _handle_notification(self, method: str, params: object) -> None:
        if method == "item/completed":
            try:
                completed = _ItemCompletedParams.model_validate(params)
            except ValidationError:
                return
            turn_key = (completed.threadId, completed.turnId)
            if not self._should_accept_turn_event(turn_key):
                return
            if completed.item.type == "agentMessage" and completed.item.text:
                self._turn_messages.setdefault(turn_key, []).append(completed.item.text)
            return
        if method == "item/agentMessage/delta":
            try:
                delta = _AgentMessageDeltaParams.model_validate(params)
            except ValidationError:
                return
            turn_key = (delta.threadId, delta.turnId)
            if not self._should_accept_turn_event(turn_key):
                return
            self._turn_deltas.setdefault(turn_key, []).append(delta.delta)
            return
        if method != "turn/completed":
            return
        try:
            completed_turn = _TurnCompletedParams.model_validate(params)
        except ValidationError:
            return
        turn_key = (completed_turn.threadId, completed_turn.turn.id)
        if not self._should_accept_turn_event(turn_key):
            return
        waiter = self._turn_waiters.get(turn_key)
        if waiter is not None and not waiter.done():
            waiter.set_result(completed_turn)
        elif self._starting_thread_id == completed_turn.threadId:
            self._early_turn_completions[turn_key] = completed_turn

    async def _deny_server_request(self, request_id: object, method: str) -> None:
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            await self._send({"id": request_id, "result": {"decision": "decline"}})
            return
        if method in {"execCommandApproval", "applyPatchApproval"}:
            await self._send({"id": request_id, "result": {"decision": "denied"}})
            return
        await self._send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "Gateway 安全模式不支援此互動請求。",
                },
            }
        )

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        """持續讀取 stderr 防止 child 阻塞，但不保存或記錄可能的秘密。"""

        stderr = cast(asyncio.StreamReader, process.stderr)
        try:
            while await stderr.read(64 * 1024):
                pass
        except asyncio.CancelledError:
            raise

    def _extract_response(self, turn_key: TurnKey, turn: _ProtocolTurn) -> str:
        item_messages = [
            item.text
            for item in turn.items
            if item.type == "agentMessage" and item.text
        ]
        if item_messages:
            return item_messages[-1].strip()
        completed_messages = self._turn_messages.get(turn_key, [])
        if completed_messages:
            return completed_messages[-1].strip()
        return "".join(self._turn_deltas.get(turn_key, [])).strip()

    async def _interrupt_timed_out_turn(
        self,
        thread_id: str,
        turn_id: str,
    ) -> None:
        """逾時只中止既有 turn，禁止重新提交相同使用者訊息。"""

        with contextlib.suppress(CodexAppServerError, TimeoutError):
            await asyncio.wait_for(
                self._request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                ),
                timeout=self._settings.startup_timeout_seconds,
            )

    def _fail_pending(self, error: CodexAppServerError) -> None:
        for request_future in self._pending_requests.values():
            if not request_future.done():
                request_future.set_exception(error)
        for turn_future in self._turn_waiters.values():
            if not turn_future.done():
                turn_future.set_exception(error)

    def _is_transport_alive(self) -> bool:
        process = self._process
        reader_task = self._reader_task
        return (
            process is not None
            and process.returncode is None
            and reader_task is not None
            and not reader_task.done()
        )

    def _should_accept_turn_event(self, turn_key: TurnKey) -> bool:
        return (
            turn_key == self._active_turn_key
            or turn_key in self._turn_waiters
            or turn_key[0] == self._starting_thread_id
        )

    def _discard_thread_buffers(
        self,
        thread_id: str,
        *,
        keep: TurnKey | None = None,
    ) -> None:
        for buffer in (
            self._turn_messages,
            self._turn_deltas,
            self._early_turn_completions,
        ):
            stale_keys = [
                turn_key
                for turn_key in buffer
                if turn_key[0] == thread_id and turn_key != keep
            ]
            for turn_key in stale_keys:
                buffer.pop(turn_key, None)

    async def _stop_failed_process(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self._settings.shutdown_timeout_seconds,
                )
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        tasks = [
            task for task in (self._reader_task, self._stderr_task) if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._process = None
        self._reader_task = None
        self._stderr_task = None
