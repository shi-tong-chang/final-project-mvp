from __future__ import annotations

from pathlib import Path

import pytest
from app.core.gateway_settings import GatewaySettings
from app.gateway_main import create_gateway_app
from app.schemas.api.codex_gateway import (
    CatalogItem,
    CharacterStyleItem,
    GatewayTurnRequest,
    WorkspaceKind,
)
from app.services.codex_gateway.client import (
    CodexClientStatus,
    CodexThread,
    CodexTurn,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeCodexClient:
    def __init__(self) -> None:
        self.turn_messages: list[tuple[str, str]] = []
        self.is_closed = False

    async def status(self) -> CodexClientStatus:
        return CodexClientStatus(
            is_available=True,
            is_connected=True,
            binary_name="codex",
            detail="Codex fake 已連線。",
        )

    async def start_thread(self) -> CodexThread:
        return CodexThread(thread_id="thr_gateway_test")

    async def run_turn(self, thread_id: str, message: str) -> CodexTurn:
        self.turn_messages.append((thread_id, message))
        return CodexTurn(
            turn_id="turn_gateway_test",
            response="我已整理目前的角色方向。",
        )

    async def close(self) -> None:
        self.is_closed = True


def _settings() -> GatewaySettings:
    return GatewaySettings(
        repo_root=REPO_ROOT,
        frontend_root=REPO_ROOT / "frontend/gateway",
        codex_cwd=REPO_ROOT,
    )


def test_gateway_schema_rejects_blank_message_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GatewayTurnRequest.model_validate(
            {
                "message": " \n ",
                "workspace": WorkspaceKind.CHARACTER,
                "context": {},
            }
        )

    with pytest.raises(ValidationError):
        GatewayTurnRequest.model_validate(
            {
                "message": "請整理角色方向",
                "workspace": WorkspaceKind.CHARACTER,
                "context": {},
                "sandbox": "danger-full-access",
            }
        )

    with pytest.raises(ValidationError):
        GatewayTurnRequest.model_validate(
            {
                "message": "請整理角色方向",
                "workspace": WorkspaceKind.CHARACTER,
                "context": {"reference_ids": ["x" * 257]},
            }
        )

    with pytest.raises(ValidationError):
        CatalogItem.model_validate(
            {
                "item_id": "unsafe-preview",
                "title": "不安全預覽",
                "description": "不得讓 catalog 指向任意外站。",
                "preview_kind": "image",
                "preview_url": "https://example.invalid/image.png",
            }
        )

    with pytest.raises(ValidationError):
        CharacterStyleItem.model_validate(
            {
                "item_id": "missing-prompt",
                "title": "沒有提示詞的風格",
                "description": "角色風格卡必須與提示詞一對一。",
                "preview_kind": "cel",
            }
        )


def test_gateway_api_serves_workspace_catalog_and_codex_turn() -> None:
    fake = FakeCodexClient()
    app = create_gateway_app(_settings(), client=fake)

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "故事工作台" in page.text
        assert 'id="character-history"' in page.text
        assert 'id="scene-history"' in page.text
        assert 'id="confirm-character-generation"' in page.text
        assert 'id="confirm-scene-generation"' in page.text
        assert "複製完整提示詞" not in page.text
        assert page.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/openapi.json").status_code == 200

        status = client.get("/api/v1/gateway/status")
        assert status.status_code == 200
        assert status.json() == {
            "status": "ready",
            "available": True,
            "connected": True,
            "codex_binary": "codex",
            "sandbox": "read-only",
            "approval_policy": "never",
            "detail": "Codex fake 已連線。",
        }

        catalog = client.get("/api/v1/gateway/catalog")
        assert catalog.status_code == 200
        catalog_payload = catalog.json()
        assert catalog_payload["schema_version"] == "storyboard-studio.catalog.v2"
        character_styles = catalog_payload["character_styles"]
        assert len(character_styles) == 20
        assert len(catalog_payload["scene_showcase"]) == 3
        assert len(catalog_payload["storyboard_showcase"]) == 3
        assert {item["status"] for item in character_styles} == {"placeholder"}
        assert len({item["item_id"] for item in character_styles}) == 20
        assert all(item["prompt_fragment"].strip() for item in character_styles)
        assert {
            "manga-ink",
            "clean-line-art",
            "dark-fairytale",
            "classical-oil",
            "cinematic-film",
            "photoreal",
        } <= {item["item_id"] for item in character_styles}

        created = client.post(
            "/api/v1/gateway/threads",
            json={
                "workspace": "character",
                "context": {
                    "selected_item_id": "cinematic-film",
                    "prompt_draft": "銀白短髮的鐘錶修復師",
                    "reference_ids": [],
                },
            },
        )
        assert created.status_code == 201
        assert created.json() == {
            "thread_id": "thr_gateway_test",
            "workspace": "character",
            "status": "ready",
        }

        turn = client.post(
            "/api/v1/gateway/threads/thr_gateway_test/turns",
            json={
                "message": "請先幫我整理角色輪廓。",
                "workspace": "character",
                "context": {
                    "selected_item_id": "cinematic-film",
                    "prompt_draft": "銀白短髮的鐘錶修復師",
                    "reference_ids": ["ref_character_1"],
                },
            },
        )
        assert turn.status_code == 200
        assert turn.json() == {
            "thread_id": "thr_gateway_test",
            "turn_id": "turn_gateway_test",
            "response": "我已整理目前的角色方向。",
            "status": "completed",
        }
        sent_thread_id, sent_message = fake.turn_messages[-1]
        assert sent_thread_id == "thr_gateway_test"
        assert "目前工作區：生成角色" in sent_message
        assert "cinematic-film" in sent_message
        assert "銀白短髮的鐘錶修復師" in sent_message
        assert sent_message.endswith("請先幫我整理角色輪廓。")

        unknown = client.post(
            "/api/v1/gateway/threads/thr_unknown/turns",
            json={
                "message": "繼續",
                "workspace": "scene",
                "context": {},
            },
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "GATEWAY_THREAD_NOT_FOUND"

        unsafe = client.post(
            "/api/v1/gateway/threads",
            json={
                "workspace": "character",
                "context": {},
                "cwd": "/tmp",
            },
        )
        assert unsafe.status_code == 422
        assert unsafe.json() == {
            "error": {
                "code": "GATEWAY_INVALID_REQUEST",
                "message": "請求內容未通過驗證。",
            }
        }

    assert fake.is_closed is True


def test_gateway_rejects_catalog_item_from_another_workspace() -> None:
    fake = FakeCodexClient()
    app = create_gateway_app(_settings(), client=fake)

    with TestClient(app) as client:
        client.post(
            "/api/v1/gateway/threads",
            json={"workspace": "scene", "context": {}},
        )
        response = client.post(
            "/api/v1/gateway/threads/thr_gateway_test/turns",
            json={
                "message": "規劃場景",
                "workspace": "scene",
                "context": {"selected_item_id": "cinematic-film"},
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "GATEWAY_CATALOG_ITEM_NOT_FOUND"
    assert fake.turn_messages == []
