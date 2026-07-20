from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from app.core.gateway_settings import GatewaySettings
from app.services.codex_gateway.client import (
    CodexAppServerClient,
    CodexAppServerError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

FAKE_APP_SERVER = """#!/usr/bin/env python3
import json
import pathlib
import sys

turn_count = 0
for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        response = {"id": request_id, "result": {"userAgent": "fake"}}
        print(json.dumps(response), flush=True)
    elif method == "initialized":
        continue
    elif method == "thread/start":
        params = message["params"]
        if params.get("cwd") != str(pathlib.Path.cwd()):
            raise RuntimeError("gateway changed the configured cwd")
        if params.get("sandbox") != "read-only":
            raise RuntimeError("gateway did not lock the sandbox")
        if params.get("approvalPolicy") != "never":
            raise RuntimeError("gateway did not lock approval policy")
        if params.get("ephemeral") is not True:
            raise RuntimeError("gateway did not create an ephemeral thread")
        print(
            json.dumps(
                {
                    "id": request_id,
                    "result": {"thread": {"id": "thr_protocol_test"}},
                }
            ),
            flush=True,
        )
    elif method == "turn/start":
        turn_count += 1
        turn_id = f"turn_protocol_{turn_count}"
        thread_id = message["params"]["threadId"]
        print(
            json.dumps(
                {
                    "id": request_id,
                    "result": {
                        "turn": {
                            "id": turn_id,
                            "status": "inProgress",
                            "items": [],
                        }
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "id": 900,
                    "method": "item/commandExecution/requestApproval",
                    "params": {"threadId": thread_id, "turnId": turn_id},
                }
            ),
            flush=True,
        )
        approval = json.loads(sys.stdin.readline())
        if approval.get("result", {}).get("decision") != "decline":
            raise RuntimeError("gateway did not fail closed")
        print(
            json.dumps(
                {
                    "id": 901,
                    "method": "item/fileChange/requestApproval",
                    "params": {"threadId": thread_id, "turnId": turn_id},
                }
            ),
            flush=True,
        )
        file_approval = json.loads(sys.stdin.readline())
        if file_approval.get("result", {}).get("decision") != "decline":
            raise RuntimeError("gateway approved a file change")
        print(
            json.dumps(
                {
                    "id": 902,
                    "method": "applyPatchApproval",
                    "params": {"threadId": thread_id, "turnId": turn_id},
                }
            ),
            flush=True,
        )
        legacy_approval = json.loads(sys.stdin.readline())
        if legacy_approval.get("result", {}).get("decision") != "denied":
            raise RuntimeError("gateway approved a legacy patch")
        print(
            json.dumps(
                {
                    "id": 903,
                    "method": "item/permissions/requestApproval",
                    "params": {"threadId": thread_id, "turnId": turn_id},
                }
            ),
            flush=True,
        )
        permission_approval = json.loads(sys.stdin.readline())
        if permission_approval.get("error", {}).get("code") != -32601:
            raise RuntimeError("gateway granted additional permissions")
        text = "Codex protocol fake 回覆。"
        print(
            json.dumps(
                {
                    "method": "item/completed",
                    "params": {
                        "completedAtMs": 1,
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "id": "item_agent",
                            "type": "agentMessage",
                            "text": text,
                        },
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {
                            "id": turn_id,
                            "status": "completed",
                            "items": [],
                        },
                    },
                }
            ),
            flush=True,
        )
"""

TIMEOUT_APP_SERVER = """#!/usr/bin/env python3
import json
import pathlib
import sys

turn_start_count = 0
for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        print(json.dumps({"id": request_id, "result": {}}), flush=True)
    elif method == "initialized":
        continue
    elif method == "thread/start":
        result = {"thread": {"id": "thr_timeout_test"}}
        print(json.dumps({"id": request_id, "result": result}), flush=True)
    elif method == "turn/start":
        turn_start_count += 1
        pathlib.Path("turn-start-count").write_text(
            str(turn_start_count),
            encoding="utf-8",
        )
        result = {
            "turn": {
                "id": "turn_timeout_test",
                "status": "inProgress",
                "items": [],
            }
        }
        print(json.dumps({"id": request_id, "result": result}), flush=True)
    elif method == "turn/interrupt":
        pathlib.Path("interrupt-seen").write_text("yes", encoding="utf-8")
        print(json.dumps({"id": request_id, "result": {}}), flush=True)
"""

MALFORMED_APP_SERVER = """#!/usr/bin/env python3
import sys

for raw_line in sys.stdin:
    print("SENSITIVE_SENTINEL=must-not-escape", file=sys.stderr, flush=True)
    print("{not-valid-json", flush=True)
"""

LARGE_LINE_APP_SERVER = """#!/usr/bin/env python3
import json
import sys

for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        print(json.dumps({"id": request_id, "result": {}}), flush=True)
    elif method == "initialized":
        continue
    elif method == "thread/start":
        result = {"thread": {"id": "thr_large_line"}}
        print(json.dumps({"id": request_id, "result": result}), flush=True)
    elif method == "turn/start":
        thread_id = message["params"]["threadId"]
        turn = {
            "id": "turn_large_line",
            "status": "inProgress",
            "items": [],
        }
        print(json.dumps({"id": request_id, "result": {"turn": turn}}), flush=True)
        completed_turn = {
            "id": "turn_large_line",
            "status": "completed",
            "items": [
                {
                    "type": "agentMessage",
                    "text": "長" * 70_000,
                }
            ],
        }
        notification = {
            "method": "turn/completed",
            "params": {"threadId": thread_id, "turn": completed_turn},
        }
        print(json.dumps(notification), flush=True)
"""

LATE_NOTIFICATION_APP_SERVER = """#!/usr/bin/env python3
import json
import pathlib
import sys

turn_count = 0
for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        print(json.dumps({"id": request_id, "result": {}}), flush=True)
    elif method == "initialized":
        continue
    elif method == "thread/start":
        result = {"thread": {"id": "thr_late_test"}}
        print(json.dumps({"id": request_id, "result": result}), flush=True)
    elif method == "turn/start":
        turn_count += 1
        pathlib.Path("turn-start-count").write_text(
            str(turn_count),
            encoding="utf-8",
        )
        turn_id = "turn_old" if turn_count == 1 else "turn_new"
        thread_id = message["params"]["threadId"]
        turn = {"id": turn_id, "status": "inProgress", "items": []}
        print(json.dumps({"id": request_id, "result": {"turn": turn}}), flush=True)
        if turn_count == 2:
            for completed_id, text in (
                ("turn_old", "不應污染新回覆"),
                ("turn_new", "新的安全回覆"),
            ):
                item = {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": completed_id,
                        "item": {"type": "agentMessage", "text": text},
                    },
                }
                print(json.dumps(item), flush=True)
                completed = {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {
                            "id": completed_id,
                            "status": "completed",
                            "items": [],
                        },
                    },
                }
                print(json.dumps(completed), flush=True)
    elif method == "turn/interrupt":
        print(json.dumps({"id": request_id, "result": {}}), flush=True)
"""


def test_codex_app_server_jsonl_lifecycle_and_fail_closed_approval(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "fake-codex"
    executable.write_text(FAKE_APP_SERVER, encoding="utf-8")
    executable.chmod(0o700)
    settings = GatewaySettings(
        repo_root=REPO_ROOT,
        frontend_root=REPO_ROOT / "frontend/gateway",
        codex_cwd=REPO_ROOT,
        codex_binary=str(executable),
        startup_timeout_seconds=2,
        turn_timeout_seconds=2,
        shutdown_timeout_seconds=2,
    )

    async def exercise_client() -> None:
        client = CodexAppServerClient(settings)
        before = await client.status()
        assert before.is_available is True
        assert before.is_connected is False

        thread = await client.start_thread()
        assert thread.thread_id == "thr_protocol_test"
        connected = await client.status()
        assert connected.is_connected is True

        turn = await client.run_turn(thread.thread_id, "只需回覆測試文字。")
        assert turn.turn_id == "turn_protocol_1"
        assert turn.response == "Codex protocol fake 回覆。"
        await client.close()

    asyncio.run(exercise_client())


def test_codex_app_server_start_failure_is_sanitized() -> None:
    settings = GatewaySettings(
        repo_root=REPO_ROOT,
        frontend_root=REPO_ROOT / "frontend/gateway",
        codex_cwd=REPO_ROOT,
        codex_binary=sys.executable,
    )

    async def failing_process_factory(
        *args: object,
        **kwargs: object,
    ) -> asyncio.subprocess.Process:
        del args, kwargs
        raise OSError("SENSITIVE_SENTINEL=must-not-escape")

    async def exercise_client() -> None:
        client = CodexAppServerClient(
            settings,
            process_factory=failing_process_factory,
        )
        with pytest.raises(CodexAppServerError) as captured:
            await client.start_thread()
        assert captured.value.code == "CODEX_START_FAILED"
        assert "must-not-escape" not in captured.value.message
        await client.close()

    asyncio.run(exercise_client())


def test_codex_app_server_rejects_malformed_protocol_without_stderr_leak(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "malformed-codex"
    executable.write_text(MALFORMED_APP_SERVER, encoding="utf-8")
    executable.chmod(0o700)
    settings = GatewaySettings(
        repo_root=REPO_ROOT,
        frontend_root=REPO_ROOT / "frontend/gateway",
        codex_cwd=REPO_ROOT,
        codex_binary=str(executable),
        startup_timeout_seconds=2,
        shutdown_timeout_seconds=2,
    )

    async def exercise_client() -> None:
        client = CodexAppServerClient(settings)
        with pytest.raises(CodexAppServerError) as captured:
            await client.start_thread()
        assert captured.value.code == "CODEX_PROTOCOL_ERROR"
        assert "must-not-escape" not in captured.value.message
        await client.close()

    asyncio.run(exercise_client())


def test_codex_turn_timeout_interrupts_once_without_resubmission(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "timeout-codex"
    executable.write_text(TIMEOUT_APP_SERVER, encoding="utf-8")
    executable.chmod(0o700)
    settings = GatewaySettings(
        repo_root=tmp_path,
        frontend_root=tmp_path,
        codex_cwd=tmp_path,
        codex_binary=str(executable),
        startup_timeout_seconds=2,
        turn_timeout_seconds=0.05,
        shutdown_timeout_seconds=2,
    )

    async def exercise_client() -> None:
        client = CodexAppServerClient(settings)
        thread = await client.start_thread()
        with pytest.raises(CodexAppServerError) as captured:
            await client.run_turn(thread.thread_id, "逾時測試")
        assert captured.value.code == "CODEX_TURN_TIMEOUT"
        await client.close()

    asyncio.run(exercise_client())
    assert (tmp_path / "interrupt-seen").read_text(encoding="utf-8") == "yes"
    assert (tmp_path / "turn-start-count").read_text(encoding="utf-8") == "1"


def test_codex_app_server_accepts_bounded_jsonl_larger_than_default_stream_limit(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "large-line-codex"
    executable.write_text(LARGE_LINE_APP_SERVER, encoding="utf-8")
    executable.chmod(0o700)
    settings = GatewaySettings(
        repo_root=REPO_ROOT,
        frontend_root=REPO_ROOT / "frontend/gateway",
        codex_cwd=REPO_ROOT,
        codex_binary=str(executable),
        startup_timeout_seconds=2,
        turn_timeout_seconds=2,
        shutdown_timeout_seconds=2,
    )

    async def exercise_client() -> None:
        client = CodexAppServerClient(settings)
        thread = await client.start_thread()
        turn = await client.run_turn(thread.thread_id, "大型 JSONL 測試")
        assert turn.response == "長" * 70_000
        await client.close()

    asyncio.run(exercise_client())


def test_codex_app_server_rejects_jsonl_over_configured_safety_limit(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "oversize-line-codex"
    executable.write_text(LARGE_LINE_APP_SERVER, encoding="utf-8")
    executable.chmod(0o700)
    settings = GatewaySettings(
        repo_root=REPO_ROOT,
        frontend_root=REPO_ROOT / "frontend/gateway",
        codex_cwd=REPO_ROOT,
        codex_binary=str(executable),
        startup_timeout_seconds=2,
        turn_timeout_seconds=2,
        shutdown_timeout_seconds=2,
        protocol_line_limit_bytes=64 * 1024,
    )

    async def exercise_client() -> None:
        client = CodexAppServerClient(settings)
        thread = await client.start_thread()
        with pytest.raises(CodexAppServerError) as captured:
            await client.run_turn(thread.thread_id, "超出 JSONL 上限測試")
        assert captured.value.code == "CODEX_PROTOCOL_ERROR"
        await client.close()

    asyncio.run(exercise_client())


def test_late_notification_from_timed_out_turn_cannot_complete_next_turn(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "late-notification-codex"
    executable.write_text(LATE_NOTIFICATION_APP_SERVER, encoding="utf-8")
    executable.chmod(0o700)
    settings = GatewaySettings(
        repo_root=tmp_path,
        frontend_root=tmp_path,
        codex_cwd=tmp_path,
        codex_binary=str(executable),
        startup_timeout_seconds=2,
        turn_timeout_seconds=0.1,
        shutdown_timeout_seconds=2,
    )

    async def exercise_client() -> None:
        client = CodexAppServerClient(settings)
        thread = await client.start_thread()
        with pytest.raises(CodexAppServerError) as captured:
            await client.run_turn(thread.thread_id, "第一輪會逾時")
        assert captured.value.code == "CODEX_TURN_TIMEOUT"

        turn = await client.run_turn(thread.thread_id, "第二輪必須獨立")
        assert turn.turn_id == "turn_new"
        assert turn.response == "新的安全回覆"
        await client.close()

    asyncio.run(exercise_client())
    assert (tmp_path / "turn-start-count").read_text(encoding="utf-8") == "2"
