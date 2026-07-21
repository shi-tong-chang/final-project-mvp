from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from app.core.gateway_settings import GatewaySettings
from app.core.workflow_settings import WorkflowSettings
from app.gateway_main import create_gateway_app
from app.schemas.api.assets import CharacterView
from app.services.assets import AssetLibraryService
from app.services.codex_gateway.client import (
    CodexClientStatus,
    CodexThread,
    CodexTurn,
)
from app.services.workflows.adapters import (
    StoryboardWorkflowAdapter,
    WorkflowAdapterError,
)
from app.services.workflows.client import (
    ComfyImageReference,
    ComfyUIClientError,
    ComfyUIStatus,
    HttpComfyUIClient,
    WorkflowGraph,
)
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]


def _image_bytes(
    width: int,
    height: int,
    color: tuple[int, int, int],
    *,
    image_format: str = "PNG",
) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, format=image_format)
    return output.getvalue()


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    return _image_bytes(width, height, color)


def _pixel(raw: bytes) -> tuple[int, int, int]:
    with Image.open(BytesIO(raw)) as image:
        return cast(tuple[int, int, int], image.convert("RGB").getpixel((0, 0)))


class QuietCodexClient:
    def __init__(self) -> None:
        self.closed = False

    async def status(self) -> CodexClientStatus:
        return CodexClientStatus(False, False, "codex", "測試未使用 Codex。")

    async def start_thread(self) -> CodexThread:
        raise AssertionError("workflow UI 不應建立 Codex thread")

    async def run_turn(self, thread_id: str, message: str) -> CodexTurn:
        del thread_id, message
        raise AssertionError("workflow UI 不應建立 Codex turn")

    async def close(self) -> None:
        self.closed = True


class FakeComfyUIClient:
    def __init__(self, *, available: bool = True) -> None:
        self.uploads: list[tuple[str, str]] = []
        self.upload_contents: list[bytes] = []
        self.prompts: list[WorkflowGraph] = []
        self.prompt_ids: list[str] = []
        self.waited_nodes: list[str] = []
        self.canceled: list[str] = []
        self.closed = False
        self.available = available
        self._compose_png = _png(1392, 752, (35, 60, 90))
        self._upscale_png = _png(3840, 2160, (45, 80, 120))

    async def status(self) -> ComfyUIStatus:
        return ComfyUIStatus(
            self.available,
            (
                "ComfyUI 測試服務已連線。"
                if self.available
                else "ComfyUI 測試服務未連線。"
            ),
        )

    async def upload_image(
        self,
        filename: str,
        image_bytes: bytes,
        *,
        subfolder: str,
    ) -> ComfyImageReference:
        assert image_bytes.startswith(b"\x89PNG")
        self.uploads.append((filename, subfolder))
        self.upload_contents.append(image_bytes)
        return ComfyImageReference(filename, subfolder, "input")

    async def queue_prompt(
        self,
        prompt: WorkflowGraph,
        *,
        prompt_id: str,
    ) -> str:
        self.prompts.append(prompt)
        self.prompt_ids.append(prompt_id)
        return prompt_id

    async def wait_for_output(
        self,
        prompt_id: str,
        *,
        output_node_id: str,
    ) -> ComfyImageReference:
        self.waited_nodes.append(output_node_id)
        return ComfyImageReference(
            filename=f"{prompt_id}-{output_node_id}.png",
            subfolder="",
            folder_type="output",
        )

    async def download_image(self, reference: ComfyImageReference) -> bytes:
        return (
            self._upscale_png
            if reference.filename.endswith("-26.png")
            else self._compose_png
        )

    async def cancel_prompt(self, prompt_id: str) -> None:
        self.canceled.append(prompt_id)

    async def close(self) -> None:
        self.closed = True


class BlockingComfyUIClient(FakeComfyUIClient):
    def __init__(self) -> None:
        super().__init__()
        self.wait_started = threading.Event()

    async def wait_for_output(
        self,
        prompt_id: str,
        *,
        output_node_id: str,
    ) -> ComfyImageReference:
        del prompt_id, output_node_id
        self.wait_started.set()
        await asyncio.Future()
        raise AssertionError("cancelled wait 不應恢復")


class QueueBoundaryBlockingComfyUIClient(FakeComfyUIClient):
    """模擬 ComfyUI 已接受 prompt、HTTP response 還沒回到 client。"""

    def __init__(self) -> None:
        super().__init__()
        self.queue_started = threading.Event()
        self.accepted_prompt_id: str | None = None

    async def queue_prompt(
        self,
        prompt: WorkflowGraph,
        *,
        prompt_id: str,
    ) -> str:
        self.prompts.append(prompt)
        self.prompt_ids.append(prompt_id)
        self.accepted_prompt_id = prompt_id
        self.queue_started.set()
        await asyncio.Future()
        raise AssertionError("cancelled queue response 不應恢復")


class SecondStageFailingComfyUIClient(FakeComfyUIClient):
    def __init__(self) -> None:
        super().__init__()
        self.wait_count = 0

    async def wait_for_output(
        self,
        prompt_id: str,
        *,
        output_node_id: str,
    ) -> ComfyImageReference:
        self.wait_count += 1
        if self.wait_count == 2:
            self.waited_nodes.append(output_node_id)
            raise ComfyUIClientError(
                "COMFYUI_EXECUTION_FAILED",
                "ComfyUI 無法完成第二輪圖片工作流。",
            )
        return await super().wait_for_output(
            prompt_id,
            output_node_id=output_node_id,
        )


class SecondStageBlockingComfyUIClient(FakeComfyUIClient):
    def __init__(self) -> None:
        super().__init__()
        self.wait_count = 0
        self.second_stage_started = threading.Event()

    async def wait_for_output(
        self,
        prompt_id: str,
        *,
        output_node_id: str,
    ) -> ComfyImageReference:
        self.wait_count += 1
        if self.wait_count == 2:
            self.waited_nodes.append(output_node_id)
            self.second_stage_started.set()
            await asyncio.Future()
            raise AssertionError("cancelled B2 wait 不應恢復")
        return await super().wait_for_output(
            prompt_id,
            output_node_id=output_node_id,
        )


def _gateway_settings() -> GatewaySettings:
    return GatewaySettings(
        repo_root=REPO_ROOT,
        frontend_root=REPO_ROOT / "frontend/gateway",
        codex_cwd=REPO_ROOT,
    )


def _workflow_settings(**changes: Any) -> WorkflowSettings:
    return WorkflowSettings(
        repo_root=REPO_ROOT,
        workflow_root=REPO_ROOT / "docs/workflows",
        poll_interval_seconds=0.1,
        **changes,
    )


def _isolated_workflow_settings(
    tmp_path: Path,
    **changes: Any,
) -> WorkflowSettings:
    workflow_root = tmp_path / "workflows"
    workflow_root.mkdir()
    for workflow_name in (
        "wf_dual_B1.json",
        "wf_dual_B2.json",
        "wf10_upscale_opt2.json",
    ):
        shutil.copyfile(
            REPO_ROOT / "docs/workflows" / workflow_name,
            workflow_root / workflow_name,
        )
    return WorkflowSettings(
        repo_root=tmp_path,
        workflow_root=workflow_root,
        asset_library_root=tmp_path / "asset-library",
        poll_interval_seconds=0.1,
        **changes,
    )


def _register_library_assets(
    settings: WorkflowSettings,
    *,
    character_count: int,
) -> tuple[str, tuple[str, ...]]:
    names = ("角色甲", "角色乙")
    front_colors = ((170, 20, 30), (20, 30, 170))

    async def register() -> tuple[str, tuple[str, ...]]:
        library = AssetLibraryService(settings)
        await library.start()
        scene = await library.register_scene(
            name="黃金花園",
            description="午後金色陽光照入的花園。",
            image=_png(96, 64, (220, 190, 90)),
        )
        character_ids: list[str] = []
        for index in range(character_count):
            front = front_colors[index]
            character = await library.register_character(
                name=names[index],
                description=f"{names[index]}的完整四視圖角色模板。",
                images={
                    CharacterView.FRONT: _png(48, 64, front),
                    CharacterView.LEFT: _png(48, 64, (20, 170, 30)),
                    CharacterView.RIGHT: _png(48, 64, (30, 20, 170)),
                    CharacterView.BACK: _png(48, 64, (90, 70, 50)),
                },
            )
            character_ids.append(character.asset_id)
        return scene.asset_id, tuple(character_ids)

    return asyncio.run(register())


def _assert_fixed_composition_template(
    graph: WorkflowGraph,
    template_name: str,
) -> None:
    template = json.loads((REPO_ROOT / "docs/workflows" / template_name).read_bytes())
    for node_id, input_name in (
        ("9", "filename_prefix"),
        ("41", "image"),
        ("42", "image"),
        ("170:151", "prompt"),
        ("170:169", "seed"),
    ):
        template[node_id]["inputs"][input_name] = graph[node_id]["inputs"][input_name]
    assert graph == template


def _wait_for_status(
    client: TestClient,
    run_url: str,
    expected: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(run_url)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] == expected:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"run 未進入 {expected}")


def test_storyboard_compose_select_and_upscale_api() -> None:
    codex = QuietCodexClient()
    comfy = FakeComfyUIClient()
    app = create_gateway_app(
        _gateway_settings(),
        client=codex,
        workflow_settings=_workflow_settings(),
        comfyui_client=comfy,
    )

    with TestClient(app) as client:
        status_response = client.get("/api/v1/gateway/workflows/status")
        assert status_response.status_code == 200
        assert status_response.json() == {
            "status": "ready",
            "available": True,
            "detail": "ComfyUI 測試服務已連線。",
        }

        created = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={
                "request": json.dumps(
                    {
                        "prompt": "把角色放入場景，保持人物與場景特徵。",
                        "candidate_count": 2,
                    },
                    ensure_ascii=False,
                )
            },
            files={
                "scene_image": (
                    "scene.png",
                    _png(1392, 752, (20, 40, 70)),
                    "image/png",
                ),
                "character_image": (
                    "character.webp",
                    _image_bytes(
                        512,
                        768,
                        (120, 80, 50),
                        image_format="WEBP",
                    ),
                    "image/webp",
                ),
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        expected_candidate_id = created.json()["candidates"][0]["candidate_id"]
        run_url = f"/api/v1/gateway/workflows/storyboards/{run_id}"

        before_selection = client.post(
            f"{run_url}/upscale",
            json={
                "refine_prompt": "維持目前構圖並增加細節。",
                "expected_candidate_id": expected_candidate_id,
            },
        )
        assert before_selection.status_code == 409
        assert before_selection.json()["error"]["code"] == (
            "WORKFLOW_SELECTION_REQUIRED"
        )

        ready = _wait_for_status(client, run_url, "awaiting_selection")
        assert len(ready["candidates"]) == 2
        assert {item["status"] for item in ready["candidates"]} == {"completed"}
        candidate = ready["candidates"][0]
        assert candidate["image_url"].startswith("/")
        assert candidate["download_url"].startswith("/")

        image = client.get(candidate["image_url"])
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert Image.open(BytesIO(image.content)).size == (1392, 752)
        download = client.get(candidate["download_url"])
        assert download.status_code == 200
        assert download.headers["content-disposition"].startswith("attachment;")

        selected = client.post(
            f"{run_url}/selection",
            json={"candidate_id": candidate["candidate_id"]},
        )
        assert selected.status_code == 200
        assert selected.json()["status"] == "completed"
        assert selected.json()["selected_candidate_id"] == candidate["candidate_id"]

        stale_selection = next(
            item
            for item in ready["candidates"]
            if item["candidate_id"] != candidate["candidate_id"]
        )
        stale_upscale = client.post(
            f"{run_url}/upscale",
            json={
                "refine_prompt": "這個 request 使用過期的選片狀態。",
                "expected_candidate_id": stale_selection["candidate_id"],
            },
        )
        assert stale_upscale.status_code == 409
        assert stale_upscale.json()["error"]["code"] == ("WORKFLOW_SELECTION_CHANGED")

        upscale = client.post(
            f"{run_url}/upscale",
            json={
                "refine_prompt": (
                    "奇幻山谷中角色站在樹旁，維持人物服裝、場景與金色光線，"
                    "畫面柔順細膩。"
                ),
                "expected_candidate_id": candidate["candidate_id"],
            },
        )
        assert upscale.status_code == 202
        assert upscale.json()["status"] == "upscaling"
        finished = _wait_for_status(client, run_url, "completed")
        assert finished["upscale"]["status"] == "completed"
        assert finished["upscale"]["image_url"].startswith("/")

        image_4k = client.get(finished["upscale"]["image_url"])
        assert image_4k.status_code == 200
        assert Image.open(BytesIO(image_4k.content)).size == (3840, 2160)

        duplicate_upscale = client.post(
            f"{run_url}/upscale",
            json={
                "refine_prompt": "不應重複排程。",
                "expected_candidate_id": candidate["candidate_id"],
            },
        )
        assert duplicate_upscale.status_code == 409
        assert duplicate_upscale.json()["error"]["code"] == (
            "WORKFLOW_UPSCALE_ALREADY_QUEUED"
        )

        same_selection = client.post(
            f"{run_url}/selection",
            json={"candidate_id": candidate["candidate_id"]},
        )
        assert same_selection.status_code == 200
        other_candidate = next(
            item
            for item in finished["candidates"]
            if item["candidate_id"] != candidate["candidate_id"]
        )
        changed_selection = client.post(
            f"{run_url}/selection",
            json={"candidate_id": other_candidate["candidate_id"]},
        )
        assert changed_selection.status_code == 409
        assert changed_selection.json()["error"]["code"] == (
            "WORKFLOW_SELECTION_LOCKED"
        )

        assert len(comfy.prompts) == 3
        for graph in comfy.prompts[:2]:
            assert graph["41"]["inputs"]["image"].endswith("-scene.png")
            assert graph["42"]["inputs"]["image"].endswith("-character.png")
            compose_prompt = graph["170:151"]["inputs"]["prompt"]
            assert "角色的身份、五官、髮型" in compose_prompt
            assert "使用者描述：把角色放入" in compose_prompt
            assert compose_prompt != "把角色放入場景，保持人物與場景特徵。"
            assert graph["9"]["inputs"]["filename_prefix"].startswith(
                f"final-project-mvp/{run_id}/"
            )
        upscale_graph = comfy.prompts[-1]
        upscale_prompt = upscale_graph["4"]["inputs"]["user_prompt"]
        assert "不得新增或刪除角色" in upscale_prompt
        assert "奇幻山谷" in upscale_prompt
        assert (
            "【當前畫面內容具體描述"
            not in (upscale_graph["4"]["inputs"]["user_prompt"])
        )
        assert upscale_graph["10"]["inputs"]["image"].endswith("-upscale-source.png")
        assert comfy.waited_nodes == ["9", "9", "26"]

    assert comfy.closed is True
    assert codex.closed is True


def test_asset_library_api_lists_complete_views_and_is_read_only(
    tmp_path: Path,
) -> None:
    settings = _isolated_workflow_settings(tmp_path)
    scene_id, character_ids = _register_library_assets(
        settings,
        character_count=1,
    )
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=settings,
        comfyui_client=FakeComfyUIClient(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/gateway/assets")
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"characters", "scenes"}
        assert len(payload["characters"]) == 1
        assert len(payload["scenes"]) == 1

        character = payload["characters"][0]
        assert set(character) == {
            "asset_id",
            "name",
            "description",
            "created_at",
            "views",
        }
        assert character["asset_id"] == character_ids[0]
        assert character["name"] == "角色甲"
        assert set(character["views"]) == {"front", "left", "right", "back"}
        for view, image_url in character["views"].items():
            assert image_url == (
                f"/api/v1/gateway/assets/characters/{character_ids[0]}/{view}"
            )
            image = client.get(image_url)
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/png"
            assert image.headers["cache-control"] == "no-store"
            assert Image.open(BytesIO(image.content)).size == (48, 64)

        scene = payload["scenes"][0]
        assert set(scene) == {
            "asset_id",
            "name",
            "description",
            "created_at",
            "image_url",
        }
        assert scene["asset_id"] == scene_id
        assert scene["name"] == "黃金花園"
        scene_image = client.get(scene["image_url"])
        assert scene_image.status_code == 200
        assert Image.open(BytesIO(scene_image.content)).size == (96, 64)

        assert client.post("/api/v1/gateway/assets", json={}).status_code == 405
        malformed_id = client.get(
            "/api/v1/gateway/assets/characters/not-an-opaque-id/front"
        )
        assert malformed_id.status_code == 422
        assert malformed_id.json()["error"]["code"] == "GATEWAY_INVALID_REQUEST"
        invalid_view = client.get(
            f"/api/v1/gateway/assets/characters/{character_ids[0]}/profile"
        )
        assert invalid_view.status_code == 422
        assert invalid_view.json()["error"]["code"] == "GATEWAY_INVALID_REQUEST"


def test_storyboard_from_library_request_is_strict(tmp_path: Path) -> None:
    settings = _isolated_workflow_settings(tmp_path)
    scene_id, character_ids = _register_library_assets(
        settings,
        character_count=2,
    )
    comfy = FakeComfyUIClient()
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=settings,
        comfyui_client=comfy,
    )
    path = "/api/v1/gateway/workflows/storyboards/from-library"
    valid = {
        "prompt": "角色甲站在左側，角色乙站在右側。",
        "candidate_count": 1,
        "character_asset_ids": list(character_ids),
        "scene_asset_id": scene_id,
    }

    with TestClient(app) as client:
        for forbidden in (
            {"workflow_route": "dual_character_b1_b2"},
            {"seed": 1234},
            {"node_id": "41"},
            {"server_filename": "b1_winner.png"},
        ):
            response = client.post(path, json={**valid, **forbidden})
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "GATEWAY_INVALID_REQUEST"

        invalid_selections = (
            {**valid, "character_asset_ids": []},
            {
                **valid,
                "character_asset_ids": [character_ids[0], character_ids[0]],
            },
            {
                **valid,
                "character_asset_ids": [
                    "char_00000000000000000000000000000000",
                    "char_11111111111111111111111111111111",
                    "char_22222222222222222222222222222222",
                ],
            },
        )
        for payload in invalid_selections:
            response = client.post(path, json=payload)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "GATEWAY_INVALID_REQUEST"

        unknown_character = client.post(
            path,
            json={
                **valid,
                "character_asset_ids": [
                    "char_ffffffffffffffffffffffffffffffff",
                ],
            },
        )
        assert unknown_character.status_code == 404
        assert unknown_character.json()["error"]["code"] == "ASSET_NOT_FOUND"

        unknown_scene = client.post(
            path,
            json={
                **valid,
                "scene_asset_id": "scene_ffffffffffffffffffffffffffffffff",
            },
        )
        assert unknown_scene.status_code == 404
        assert unknown_scene.json()["error"]["code"] == "ASSET_NOT_FOUND"
        assert comfy.prompts == []


def test_storyboard_from_library_single_character_uses_exact_b1(
    tmp_path: Path,
) -> None:
    settings = _isolated_workflow_settings(tmp_path)
    scene_id, character_ids = _register_library_assets(
        settings,
        character_count=1,
    )
    comfy = FakeComfyUIClient()
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=settings,
        comfyui_client=comfy,
    )
    prompt = "角色甲站在花園中央，面向鏡頭。"

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/gateway/workflows/storyboards/from-library",
            json={
                "prompt": prompt,
                "candidate_count": 1,
                "character_asset_ids": list(character_ids),
                "scene_asset_id": scene_id,
            },
        )
        assert created.status_code == 202
        created_payload = created.json()
        assert created_payload["workflow_route"] == "single_character_b1"
        candidate = created_payload["candidates"][0]
        assert set(candidate["stage_seeds"]) == {"b1", "b2"}
        assert candidate["stage_seeds"]["b2"] is None
        assert candidate["seed"] == candidate["stage_seeds"]["b1"]

        run_id = created_payload["run_id"]
        run_url = f"/api/v1/gateway/workflows/storyboards/{run_id}"
        finished = _wait_for_status(client, run_url, "awaiting_selection")
        final_candidate = finished["candidates"][0]
        assert final_candidate["seed"] == candidate["stage_seeds"]["b1"]
        assert final_candidate["stage_seeds"] == candidate["stage_seeds"]

        subfolder = f"final-project-mvp/{run_id}"
        assert comfy.uploads == [
            (f"{run_id}-scene.png", subfolder),
            (f"{run_id}-character.png", subfolder),
        ]
        assert _pixel(comfy.upload_contents[0]) == (220, 190, 90)
        assert _pixel(comfy.upload_contents[1]) == (170, 20, 30)
        assert len(comfy.prompts) == 1
        assert comfy.waited_nodes == ["9"]

        graph = comfy.prompts[0]
        _assert_fixed_composition_template(graph, "wf_dual_B1.json")
        assert graph["41"]["inputs"]["image"] == (f"{subfolder}/{run_id}-scene.png")
        assert graph["42"]["inputs"]["image"] == (f"{subfolder}/{run_id}-character.png")
        guarded_prompt = graph["170:151"]["inputs"]["prompt"]
        assert "這是第一輪合成" in guarded_prompt
        assert "場景「黃金花園」" in guarded_prompt
        assert "角色一「角色甲」" in guarded_prompt
        assert "不得新增其他角色" in guarded_prompt
        assert f"使用者描述：{prompt}" in guarded_prompt
        assert graph["170:169"]["inputs"]["seed"] == (candidate["stage_seeds"]["b1"])
        assert graph["170:169"]["inputs"]["steps"] == 20
        assert graph["170:169"]["inputs"]["cfg"] == 2.5
        assert graph["9"]["inputs"]["filename_prefix"] == (
            f"final-project-mvp/{run_id}/b1/{candidate['candidate_id']}"
        )


def test_storyboard_from_library_dual_character_runs_b1_then_b2(
    tmp_path: Path,
) -> None:
    settings = _isolated_workflow_settings(
        tmp_path,
        max_retained_image_bytes=5_000,
    )
    scene_id, character_ids = _register_library_assets(
        settings,
        character_count=2,
    )
    comfy = FakeComfyUIClient()
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=settings,
        comfyui_client=comfy,
    )
    prompt = "角色甲站在左側，角色乙站在右側，一起看向鏡頭。"

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/gateway/workflows/storyboards/from-library",
            json={
                "prompt": prompt,
                "candidate_count": 1,
                "character_asset_ids": list(character_ids),
                "scene_asset_id": scene_id,
            },
        )
        assert created.status_code == 202
        created_payload = created.json()
        assert created_payload["workflow_route"] == "dual_character_b1_b2"
        candidate = created_payload["candidates"][0]
        stage_seeds = candidate["stage_seeds"]
        assert set(stage_seeds) == {"b1", "b2"}
        assert isinstance(stage_seeds["b1"], int)
        assert isinstance(stage_seeds["b2"], int)
        assert stage_seeds["b1"] != stage_seeds["b2"]
        assert candidate["seed"] == stage_seeds["b2"]

        run_id = created_payload["run_id"]
        candidate_id = candidate["candidate_id"]
        run_url = f"/api/v1/gateway/workflows/storyboards/{run_id}"
        finished = _wait_for_status(client, run_url, "awaiting_selection")
        final_candidate = finished["candidates"][0]
        assert finished["workflow_route"] == "dual_character_b1_b2"
        assert final_candidate["stage_seeds"] == stage_seeds
        assert final_candidate["seed"] == stage_seeds["b2"]

        subfolder = f"final-project-mvp/{run_id}"
        intermediate_name = f"{run_id}-{candidate_id}-b1.png"
        assert comfy.uploads == [
            (f"{run_id}-scene.png", subfolder),
            (f"{run_id}-character.png", subfolder),
            (f"{run_id}-character-2.png", subfolder),
            (intermediate_name, subfolder),
        ]
        assert _pixel(comfy.upload_contents[0]) == (220, 190, 90)
        assert _pixel(comfy.upload_contents[1]) == (170, 20, 30)
        assert _pixel(comfy.upload_contents[2]) == (20, 30, 170)
        assert _pixel(comfy.upload_contents[3]) == (35, 60, 90)
        assert Image.open(BytesIO(comfy.upload_contents[3])).size == (1392, 752)
        assert len(comfy.prompts) == 2
        assert comfy.waited_nodes == ["9", "9"]

        b1, b2 = comfy.prompts
        _assert_fixed_composition_template(b1, "wf_dual_B1.json")
        _assert_fixed_composition_template(b2, "wf_dual_B2.json")
        assert b1["41"]["inputs"]["image"] == (f"{subfolder}/{run_id}-scene.png")
        assert b1["42"]["inputs"]["image"] == (f"{subfolder}/{run_id}-character.png")
        assert b1["170:169"]["inputs"]["seed"] == stage_seeds["b1"]
        assert b1["9"]["inputs"]["filename_prefix"] == (
            f"final-project-mvp/{run_id}/b1/{candidate_id}"
        )

        assert b2["41"]["inputs"]["image"] == (f"{subfolder}/{intermediate_name}")
        assert b2["42"]["inputs"]["image"] == (f"{subfolder}/{run_id}-character-2.png")
        assert b2["170:169"]["inputs"]["seed"] == stage_seeds["b2"]
        assert b2["9"]["inputs"]["filename_prefix"] == (
            f"final-project-mvp/{run_id}/b2/{candidate_id}"
        )
        b2_prompt = b2["170:151"]["inputs"]["prompt"]
        assert "這是第二輪合成" in b2_prompt
        assert "第一張圖是已完成的 B1 分鏡" in b2_prompt
        assert "場景「黃金花園」與角色一「角色甲」已定稿" in b2_prompt
        assert "配件、姿勢與位置不變" in b2_prompt
        assert "只將第二張圖的角色二「角色乙」新增到畫面" in b2_prompt
        assert "不得替換、重新設計或刪除角色一" in b2_prompt
        assert f"使用者描述：{prompt}" in b2_prompt


def test_dual_character_b2_failure_never_exposes_b1_intermediate(
    tmp_path: Path,
) -> None:
    settings = _isolated_workflow_settings(tmp_path)
    scene_id, character_ids = _register_library_assets(
        settings,
        character_count=2,
    )
    comfy = SecondStageFailingComfyUIClient()
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=settings,
        comfyui_client=comfy,
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/gateway/workflows/storyboards/from-library",
            json={
                "prompt": "先加入角色甲，再只加入角色乙。",
                "candidate_count": 1,
                "character_asset_ids": list(character_ids),
                "scene_asset_id": scene_id,
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        run_url = f"/api/v1/gateway/workflows/storyboards/{run_id}"
        failed = _wait_for_status(client, run_url, "failed")
        candidate = failed["candidates"][0]
        assert candidate["status"] == "failed"
        assert candidate["image_url"] is None
        assert candidate["download_url"] is None
        assert candidate["error"] == "ComfyUI 無法完成第二輪圖片工作流。"
        assert len(comfy.prompts) == 2
        assert len(comfy.uploads) == 4
        assert comfy.waited_nodes == ["9", "9"]
        assert comfy.canceled == [comfy.prompt_ids[1]]

        selection = client.post(
            f"{run_url}/selection",
            json={"candidate_id": candidate["candidate_id"]},
        )
        assert selection.status_code == 409
        assert selection.json()["error"]["code"] == "WORKFLOW_SELECTION_NOT_READY"


def test_app_shutdown_cancels_active_dual_character_b2_prompt(
    tmp_path: Path,
) -> None:
    settings = _isolated_workflow_settings(tmp_path)
    scene_id, character_ids = _register_library_assets(
        settings,
        character_count=2,
    )
    comfy = SecondStageBlockingComfyUIClient()
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=settings,
        comfyui_client=comfy,
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/gateway/workflows/storyboards/from-library",
            json={
                "prompt": "先加入角色甲，再只加入角色乙。",
                "candidate_count": 1,
                "character_asset_ids": list(character_ids),
                "scene_asset_id": scene_id,
            },
        )
        assert created.status_code == 202
        assert comfy.second_stage_started.wait(timeout=2)

    assert len(comfy.prompt_ids) == 2
    assert comfy.canceled == [comfy.prompt_ids[1]]
    assert comfy.closed is True


def test_storyboard_multipart_and_images_are_strict() -> None:
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=_workflow_settings(max_upload_bytes=1024),
        comfyui_client=FakeComfyUIClient(),
    )
    valid_request = json.dumps({"prompt": "合成角色", "candidate_count": 1})
    image = _png(64, 64, (1, 2, 3))

    with TestClient(app) as client:
        unknown_json = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={
                "request": json.dumps(
                    {
                        "prompt": "合成角色",
                        "candidate_count": 1,
                        "workflow": "任意 graph",
                    }
                )
            },
            files={
                "scene_image": ("scene.png", image, "image/png"),
                "character_image": ("character.png", image, "image/png"),
            },
        )
        assert unknown_json.status_code == 422
        assert unknown_json.json()["error"]["code"] == "WORKFLOW_INVALID_REQUEST"

        unknown_form = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={"request": valid_request, "node_id": "41"},
            files={
                "scene_image": ("scene.png", image, "image/png"),
                "character_image": ("character.png", image, "image/png"),
            },
        )
        assert unknown_form.status_code == 422
        assert unknown_form.json()["error"]["code"] == "WORKFLOW_INVALID_MULTIPART"

        corrupt = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={"request": valid_request},
            files={
                "scene_image": ("scene.png", b"not-an-image", "image/png"),
                "character_image": ("character.jpg", image, "image/jpeg"),
            },
        )
        assert corrupt.status_code == 422
        assert corrupt.json()["error"]["code"] == "WORKFLOW_INVALID_IMAGE"

        mime_spoof = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={"request": valid_request},
            files={
                "scene_image": ("scene.jpg", image, "image/jpeg"),
                "character_image": ("character.png", image, "image/png"),
            },
        )
        assert mime_spoof.status_code == 422
        assert mime_spoof.json()["error"]["code"] == "WORKFLOW_INVALID_IMAGE"

        oversized = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={"request": valid_request},
            files={
                "scene_image": (
                    "scene.png",
                    image + (b"x" * 1024),
                    "image/png",
                ),
                "character_image": ("character.png", image, "image/png"),
            },
        )
        assert oversized.status_code == 422
        assert oversized.json()["error"]["code"] == "WORKFLOW_INVALID_IMAGE"


def test_request_boundary_enforces_loopback_same_origin_and_corp() -> None:
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=_workflow_settings(),
        comfyui_client=FakeComfyUIClient(),
    )

    with TestClient(app) as client:
        status = client.get("/api/v1/gateway/workflows/status")
        assert status.headers["cross-origin-resource-policy"] == "same-origin"

        same_origin = client.post(
            "/api/v1/gateway/workflows/storyboards",
            headers={
                "origin": "http://testserver",
                "content-length": "0",
            },
        )
        assert same_origin.status_code == 422

        foreign_origin = client.post(
            "/api/v1/gateway/workflows/storyboards",
            headers={"origin": "https://attacker.example"},
        )
        assert foreign_origin.status_code == 403
        assert foreign_origin.json()["error"]["code"] == (
            "GATEWAY_CROSS_SITE_FORBIDDEN"
        )
        assert foreign_origin.headers["cross-origin-resource-policy"] == "same-origin"

        cross_site = client.post(
            "/api/v1/gateway/workflows/storyboards",
            headers={"sec-fetch-site": "cross-site"},
        )
        assert cross_site.status_code == 403
        assert cross_site.json()["error"]["code"] == ("GATEWAY_CROSS_SITE_FORBIDDEN")

    async def external_request() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app,
            client=("203.0.113.10", 48123),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as async_client:
            return await async_client.get("/")

    external = asyncio.run(external_request())
    assert external.status_code == 403
    assert external.json()["error"]["code"] == "GATEWAY_LOOPBACK_REQUIRED"
    assert external.headers["cross-origin-resource-policy"] == "same-origin"


def test_storyboard_total_body_limit_rejects_declared_and_streamed_overflow() -> None:
    settings = _workflow_settings(
        max_upload_bytes=1024,
        multipart_overhead_bytes=1024,
    )
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=settings,
        comfyui_client=FakeComfyUIClient(),
    )
    path = "/api/v1/gateway/workflows/storyboards"

    with TestClient(app) as client:
        declared = client.post(
            path,
            content=b"",
            headers={"content-length": str(settings.max_storyboard_request_bytes + 1)},
        )
        assert declared.status_code == 413
        assert declared.json()["error"]["code"] == "WORKFLOW_REQUEST_TOO_LARGE"

    async def streamed_request() -> httpx.Response:
        async def chunks() -> Any:
            yield (
                b"--safe\r\n"
                b'Content-Disposition: form-data; name="request"\r\n\r\n'
                + (b"a" * 4096)
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as async_client:
            return await async_client.post(
                path,
                content=chunks(),
                headers={"content-type": "multipart/form-data; boundary=safe"},
            )

    streamed = asyncio.run(streamed_request())
    assert streamed.status_code == 413
    assert streamed.json()["error"]["code"] == "WORKFLOW_REQUEST_TOO_LARGE"


def test_storyboard_run_and_queue_capacity_return_429() -> None:
    image = _png(64, 64, (1, 2, 3))
    request_data = {"request": json.dumps({"prompt": "合成角色", "candidate_count": 1})}
    files = {
        "scene_image": ("scene.png", image, "image/png"),
        "character_image": ("character.png", image, "image/png"),
    }

    run_limited_app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=_workflow_settings(max_runs=1),
        comfyui_client=FakeComfyUIClient(),
    )
    with TestClient(run_limited_app) as client:
        assert (
            client.post(
                "/api/v1/gateway/workflows/storyboards",
                data=request_data,
                files=files,
            ).status_code
            == 202
        )
        rejected = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data=request_data,
            files=files,
        )
        assert rejected.status_code == 429
        assert rejected.json()["error"]["code"] == "WORKFLOW_CAPACITY_EXCEEDED"

    blocking_comfy = QueueBoundaryBlockingComfyUIClient()
    queue_limited_app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=_workflow_settings(max_queue_size=1, max_runs=8),
        comfyui_client=blocking_comfy,
    )
    with TestClient(queue_limited_app) as client:
        first = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data=request_data,
            files=files,
        )
        assert first.status_code == 202
        assert blocking_comfy.queue_started.wait(timeout=2)
        second = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data=request_data,
            files=files,
        )
        assert second.status_code == 202
        third = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data=request_data,
            files=files,
        )
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "WORKFLOW_CAPACITY_EXCEEDED"


def test_storyboard_releases_inputs_and_bounds_retained_outputs() -> None:
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=_workflow_settings(max_retained_image_bytes=5_000),
        comfyui_client=FakeComfyUIClient(),
    )
    image = _png(64, 64, (1, 2, 3))
    request_data = {"request": json.dumps({"prompt": "合成角色", "candidate_count": 1})}
    files = {
        "scene_image": ("scene.png", image, "image/png"),
        "character_image": ("character.png", image, "image/png"),
    }

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data=request_data,
            files=files,
        )
        assert first.status_code == 202
        _wait_for_status(
            client,
            (f"/api/v1/gateway/workflows/storyboards/{first.json()['run_id']}"),
            "awaiting_selection",
        )

        retained_limit = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data=request_data,
            files=files,
        )
        assert retained_limit.status_code == 429
        assert retained_limit.json()["error"]["code"] == ("WORKFLOW_CAPACITY_EXCEEDED")


def test_storyboard_create_fails_safely_when_comfyui_is_unavailable() -> None:
    comfy = FakeComfyUIClient(available=False)
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=_workflow_settings(),
        comfyui_client=comfy,
    )
    image = _png(64, 64, (1, 2, 3))
    with TestClient(app) as client:
        status_response = client.get("/api/v1/gateway/workflows/status")
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "unavailable"

        response = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={"request": json.dumps({"prompt": "合成角色", "candidate_count": 1})},
            files={
                "scene_image": ("scene.png", image, "image/png"),
                "character_image": ("character.png", image, "image/png"),
            },
        )
        assert response.status_code == 503
        assert response.json() == {
            "error": {
                "code": "WORKFLOW_UNAVAILABLE",
                "message": "ComfyUI 尚未啟動或必要節點不可用。",
            }
        }
        assert comfy.prompts == []


def test_app_shutdown_cancels_and_awaits_active_comfy_prompt() -> None:
    comfy = BlockingComfyUIClient()
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=_workflow_settings(),
        comfyui_client=comfy,
    )
    image = _png(64, 64, (1, 2, 3))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={"request": json.dumps({"prompt": "合成角色", "candidate_count": 1})},
            files={
                "scene_image": ("scene.png", image, "image/png"),
                "character_image": ("character.png", image, "image/png"),
            },
        )
        assert response.status_code == 202
        assert comfy.wait_started.wait(timeout=2)

    assert len(comfy.prompt_ids) == 1
    assert comfy.canceled == comfy.prompt_ids
    assert comfy.closed is True


def test_app_shutdown_cancels_prompt_accepted_at_response_boundary() -> None:
    comfy = QueueBoundaryBlockingComfyUIClient()
    app = create_gateway_app(
        _gateway_settings(),
        client=QuietCodexClient(),
        workflow_settings=_workflow_settings(),
        comfyui_client=comfy,
    )
    image = _png(64, 64, (1, 2, 3))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/gateway/workflows/storyboards",
            data={"request": json.dumps({"prompt": "合成角色", "candidate_count": 1})},
            files={
                "scene_image": ("scene.png", image, "image/png"),
                "character_image": ("character.png", image, "image/png"),
            },
        )
        assert response.status_code == 202
        assert comfy.queue_started.wait(timeout=2)

    assert comfy.accepted_prompt_id is not None
    assert comfy.canceled == [comfy.accepted_prompt_id]
    assert comfy.closed is True


def test_http_comfyui_client_uses_safe_history_output_with_empty_subfolder() -> None:
    requests: list[tuple[str, str]] = []
    png = _png(32, 16, (7, 8, 9))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {}, "devices": []})
        if request.url.path.startswith("/object_info/"):
            node_class = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={node_class: {"name": node_class}})
        if request.url.path == "/upload/image":
            return httpx.Response(
                200,
                json={"name": "scene.png", "subfolder": "safe/run", "type": "input"},
            )
        if request.url.path == "/prompt":
            request_payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={"prompt_id": request_payload["prompt_id"]},
            )
        if request.url.path == "/history/abc-123":
            return httpx.Response(
                200,
                json={
                    "abc-123": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {
                                        "filename": "result.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(
                200, content=png, headers={"content-type": "image/png"}
            )
        if request.url.path in {"/queue", "/interrupt"}:
            return httpx.Response(200)
        raise AssertionError(f"unexpected request: {request.url}")

    async def exercise() -> None:
        client = HttpComfyUIClient(
            _workflow_settings(),
            transport=httpx.MockTransport(handler),
        )
        assert (await client.status()).available is True
        upload = await client.upload_image(
            "scene.png",
            png,
            subfolder="safe/run",
        )
        assert upload.load_image_value == "safe/run/scene.png"
        prompt_id = await client.queue_prompt(
            {"9": {"inputs": {}}},
            prompt_id="abc-123",
        )
        output = await client.wait_for_output(prompt_id, output_node_id="9")
        assert output.subfolder == ""
        assert await client.download_image(output) == png
        await client.cancel_prompt(prompt_id)
        await client.close()

    asyncio.run(exercise())
    assert ("GET", "/history/abc-123") in requests
    assert ("POST", "/queue") in requests
    assert ("POST", "/interrupt") in requests


def test_http_comfyui_client_rejects_changed_upload_and_prompt_identity() -> None:
    png = _png(32, 16, (7, 8, 9))

    def renamed_upload(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/upload/image"
        return httpx.Response(
            200,
            json={
                "name": "renamed.png",
                "subfolder": "safe/run",
                "type": "input",
            },
        )

    def changed_prompt(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prompt"
        return httpx.Response(200, json={"prompt_id": "different-id"})

    async def exercise() -> None:
        upload_client = HttpComfyUIClient(
            _workflow_settings(),
            transport=httpx.MockTransport(renamed_upload),
        )
        with pytest.raises(ComfyUIClientError) as upload_error:
            await upload_client.upload_image(
                "scene.png",
                png,
                subfolder="safe/run",
            )
        assert upload_error.value.code == "COMFYUI_UPLOAD_IDENTITY_MISMATCH"
        await upload_client.close()

        prompt_client = HttpComfyUIClient(
            _workflow_settings(),
            transport=httpx.MockTransport(changed_prompt),
        )
        with pytest.raises(ComfyUIClientError) as prompt_error:
            await prompt_client.queue_prompt(
                {"9": {"inputs": {}}},
                prompt_id="requested-id",
            )
        assert prompt_error.value.code == "COMFYUI_PROTOCOL_ERROR"
        await prompt_client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "tampered_name",
    ("wf_dual_B1.json", "wf_dual_B2.json"),
)
def test_fixed_composition_workflow_sha256_fails_closed_after_tampering(
    tmp_path: Path,
    tampered_name: str,
) -> None:
    workflow_root = tmp_path / "workflows"
    workflow_root.mkdir()
    for workflow_name in (
        "wf_dual_B1.json",
        "wf_dual_B2.json",
        "wf10_upscale_opt2.json",
    ):
        shutil.copyfile(
            REPO_ROOT / "docs/workflows" / workflow_name,
            workflow_root / workflow_name,
        )
    settings = WorkflowSettings(
        repo_root=tmp_path,
        workflow_root=workflow_root,
    )

    StoryboardWorkflowAdapter(settings)
    tampered_path = workflow_root / tampered_name
    tampered_path.write_bytes(tampered_path.read_bytes() + b"\n")

    with pytest.raises(WorkflowAdapterError, match="完整性驗證失敗"):
        StoryboardWorkflowAdapter(settings)


def test_workflow_settings_reject_non_loopback_comfyui() -> None:
    with pytest.raises(ValidationError):
        _workflow_settings(comfyui_base_url="http://example.com:8188")

    with pytest.raises(ValidationError):
        _workflow_settings(comfyui_base_url="http://127.0.0.1:8188/unsafe")
