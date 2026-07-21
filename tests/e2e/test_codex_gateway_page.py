from __future__ import annotations

import base64
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlsplit

from app.core.gateway_settings import GatewaySettings
from app.gateway_main import create_gateway_app
from app.services.codex_gateway.client import (
    CodexClientStatus,
    CodexThread,
    CodexTurn,
)
from playwright.sync_api import Page, Request, Route, sync_playwright
from uvicorn import Config, Server

REPO_ROOT = Path(__file__).resolve().parents[2]
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
BROWSER_RUN_ID = f"run_{'a' * 32}"


class BrowserFakeCodexClient:
    """確認目前純展示 UI 不會意外啟動 Codex thread。"""

    def __init__(self) -> None:
        self.thread_count = 0
        self.turn_count = 0

    async def status(self) -> CodexClientStatus:
        return CodexClientStatus(
            is_available=True,
            is_connected=False,
            binary_name="codex",
            detail="測試用 client。",
        )

    async def start_thread(self) -> CodexThread:
        self.thread_count += 1
        return CodexThread(thread_id=f"thr_browser_{self.thread_count}")

    async def run_turn(self, thread_id: str, message: str) -> CodexTurn:
        self.turn_count += 1
        return CodexTurn(
            turn_id=f"turn_browser_{self.turn_count}",
            response="這個純展示頁不應呼叫 turn。",
        )

    async def close(self) -> None:
        return


def _multipart_parts(request: Request) -> dict[str, tuple[str | None, bytes]]:
    content_type = request.headers.get("content-type", "")
    body = request.post_data_buffer or b""
    message = BytesParser(policy=default).parsebytes(
        (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + body
    )
    parsed_parts: dict[str, tuple[str | None, bytes]] = {}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            continue
        raw_filename = part.get_filename()
        filename = raw_filename if isinstance(raw_filename, str) else None
        raw_payload = part.get_payload(decode=True)
        payload = raw_payload if isinstance(raw_payload, bytes) else b""
        parsed_parts[name] = (filename, payload)
    return parsed_parts


def _json_object(request: Request) -> dict[str, object]:
    payload = request.post_data_json
    if not isinstance(payload, dict):
        raise AssertionError("browser mock 預期 JSON object request")
    normalized: dict[str, object] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise AssertionError("browser mock JSON key 必須是字串")
        normalized[key] = value
    return normalized


class StoryboardWorkflowRouteMock:
    """以同源 route 模擬候選選定後才執行 4K 的 typed API。"""

    def __init__(self) -> None:
        self.create_requests: list[dict[str, object]] = []
        self.selection_requests: list[dict[str, object]] = []
        self.upscale_requests: list[dict[str, object]] = []
        self.selected_candidate_id: str | None = None
        self.phase = "compose"
        self.fail_next_create = False
        self.conflict_next_upscale = False
        self.transient_poll_failures_remaining = 0
        self.poll_request_count = 0

    @staticmethod
    def _candidate(candidate_number: int, status: str) -> dict[str, object]:
        candidate_id = f"cand_{candidate_number:032x}"
        base_path = (
            f"/api/v1/gateway/workflows/storyboards/{BROWSER_RUN_ID}/"
            f"candidates/{candidate_id}"
        )
        is_completed = status == "completed"
        return {
            "candidate_id": candidate_id,
            "seed": 7100 + candidate_number,
            "status": status,
            "image_url": f"{base_path}/image" if is_completed else None,
            "download_url": (f"{base_path}/download" if is_completed else None),
            "error": None,
        }

    def _run_payload(
        self,
        status: str,
        *,
        candidate_status: str = "completed",
        upscale_status: str = "idle",
    ) -> dict[str, object]:
        upscale_base = f"/api/v1/gateway/workflows/storyboards/{BROWSER_RUN_ID}/upscale"
        upscale_completed = upscale_status == "completed"
        return {
            "run_id": BROWSER_RUN_ID,
            "status": status,
            "candidates": [
                self._candidate(index, candidate_status) for index in range(1, 4)
            ],
            "selected_candidate_id": self.selected_candidate_id,
            "upscale": {
                "status": upscale_status,
                "image_url": (f"{upscale_base}/image" if upscale_completed else None),
                "download_url": (
                    f"{upscale_base}/download" if upscale_completed else None
                ),
                "error": None,
            },
        }

    @staticmethod
    def _fulfill_json(
        route: Route,
        payload: dict[str, object],
        *,
        status: int = 200,
    ) -> None:
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload),
        )

    def handle(self, route: Route) -> None:
        request = route.request
        path = urlsplit(request.url).path
        method = request.method

        if path.endswith(("/image", "/download")):
            route.fulfill(
                status=200,
                content_type="image/png",
                body=ONE_PIXEL_PNG,
            )
            return

        if method == "POST" and path.endswith("/workflows/storyboards"):
            if self.fail_next_create:
                self.fail_next_create = False
                self._fulfill_json(
                    route,
                    {
                        "error": {
                            "code": "WORKFLOW_UNAVAILABLE",
                            "message": "ComfyUI 目前無法連線。",
                        }
                    },
                    status=503,
                )
                return
            parts = _multipart_parts(request)
            workflow_request = json.loads(parts["request"][1].decode())
            self.create_requests.append(
                {
                    "request": workflow_request,
                    "scene_filename": parts["scene_image"][0],
                    "character_filename": parts["character_image"][0],
                }
            )
            self.phase = "compose"
            self.selected_candidate_id = None
            self._fulfill_json(
                route,
                self._run_payload(
                    "queued",
                    candidate_status="queued",
                ),
                status=202,
            )
            return

        if method == "GET" and path.endswith(
            f"/workflows/storyboards/{BROWSER_RUN_ID}"
        ):
            self.poll_request_count += 1
            if self.transient_poll_failures_remaining > 0:
                self.transient_poll_failures_remaining -= 1
                self._fulfill_json(
                    route,
                    {
                        "error": {
                            "code": "WORKFLOW_TEMPORARY_FAILURE",
                            "message": "暫時無法取得工作進度。",
                        }
                    },
                    status=503,
                )
                return
            if self.phase == "upscale":
                self._fulfill_json(
                    route,
                    self._run_payload(
                        "completed",
                        upscale_status="completed",
                    ),
                )
            else:
                self._fulfill_json(
                    route,
                    self._run_payload("awaiting_selection"),
                )
            return

        if method == "POST" and path.endswith(
            f"/workflows/storyboards/{BROWSER_RUN_ID}/selection"
        ):
            payload = _json_object(request)
            self.selection_requests.append(payload)
            candidate_id = payload.get("candidate_id")
            if not isinstance(candidate_id, str):
                raise AssertionError("selection 缺少 candidate_id")
            self.selected_candidate_id = candidate_id
            self._fulfill_json(
                route,
                self._run_payload("completed"),
            )
            return

        if method == "POST" and path.endswith(
            f"/workflows/storyboards/{BROWSER_RUN_ID}/upscale"
        ):
            payload = _json_object(request)
            if payload.get("expected_candidate_id") != self.selected_candidate_id:
                raise AssertionError("4K request 必須鎖定 server 已選候選")
            self.upscale_requests.append(
                {
                    **payload,
                    "server_selected_candidate_id": self.selected_candidate_id,
                }
            )
            self.phase = "upscale"
            if self.conflict_next_upscale:
                self.conflict_next_upscale = False
                self._fulfill_json(
                    route,
                    {
                        "error": {
                            "code": "WORKFLOW_UPSCALE_ALREADY_ACTIVE",
                            "message": "4K 工作已在處理中。",
                        }
                    },
                    status=409,
                )
                return
            self._fulfill_json(
                route,
                self._run_payload(
                    "upscaling",
                    upscale_status="queued",
                ),
                status=202,
            )
            return

        route.fulfill(status=404, body="not found")


@contextmanager
def _run_gateway_server(
    fake: BrowserFakeCodexClient,
) -> Iterator[str]:
    settings = GatewaySettings(
        repo_root=REPO_ROOT,
        frontend_root=REPO_ROOT / "frontend/gateway",
        codex_cwd=REPO_ROOT,
    )
    app = create_gateway_app(settings, client=fake)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(128)
    port = int(server_socket.getsockname()[1])
    server = Server(
        Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    server_thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [server_socket]},
        name="style-showcase-e2e-uvicorn",
        daemon=False,
    )
    server_thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        if not server_thread.is_alive():
            raise RuntimeError("風格櫥窗 E2E server 在 startup 期間停止")
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        server_thread.join(timeout=2)
        raise RuntimeError("風格櫥窗 E2E server 未在五秒內啟動")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
        server_socket.close()
        if server_thread.is_alive():
            raise RuntimeError("風格櫥窗 E2E server 未正常停止")


def _assert_no_horizontal_overflow(page: Page) -> None:
    metrics = page.evaluate(
        """
        () => ({
          viewportWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          bodyWidth: document.body.scrollWidth
        })
        """
    )
    assert metrics["documentWidth"] <= metrics["viewportWidth"] + 1
    assert metrics["bodyWidth"] <= metrics["viewportWidth"] + 1


def test_gateway_page_generation_confirmation_history_and_responsive_layout(
    tmp_path: Path,
) -> None:
    fake = BrowserFakeCodexClient()
    with (
        _run_gateway_server(fake) as base_url,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 960},
            locale="zh-TW",
        )
        page = context.new_page()
        mutation_requests: list[str] = []

        def record_mutation(request: Request) -> None:
            if request.method != "GET":
                mutation_requests.append(f"{request.method} {request.url}")

        page.on("request", record_mutation)
        page.goto(base_url, wait_until="networkidle")

        assert page.locator("h1").inner_text() == "故事工作台"
        assert page.get_by_role("tab").all_inner_texts() == [
            "生成角色",
            "生成場景",
            "生成分鏡",
        ]
        assert page.locator("#codex-panel").count() == 0
        assert page.locator("#open-chat-button").count() == 0
        assert "Codex" not in page.locator("body").inner_text()

        style_cards = page.locator("#character-style-grid .style-card")
        style_cards.first.wait_for(state="visible")
        assert style_cards.count() == 20
        assert page.locator(".style-card .art-portrait").count() == 20
        assert page.locator(".style-card .art-coat").count() == 20

        page.locator("label.style-card", has_text="黑暗童話").click()
        assert page.locator("#selected-style-name").inner_text() == "黑暗童話"
        assert "哥德" in page.locator("#selected-style-prompt").inner_text()
        assert "style-dark-fairytale" in (
            page.locator("#character-hero-art").get_attribute("class") or ""
        )
        assert page.locator("#copy-character-prompt").count() == 0
        assert page.locator("#confirm-character-generation").inner_text() == "確認生成"
        assert page.locator("#character-history").is_visible()
        assert page.locator("#character-history").get_attribute("data-state") == "empty"
        assert page.locator("#character-history-list .history-empty").count() == 1
        assert page.get_by_text("尚無角色紀錄", exact=True).is_visible()

        character_editor = page.locator(
            "#character-style-form > .editor-stack"
        ).bounding_box()
        character_showcase = page.locator(
            "#character-panel .character-showcase"
        ).bounding_box()
        character_history = page.locator("#character-history").bounding_box()
        assert character_editor and character_showcase and character_history
        assert character_editor["x"] < character_showcase["x"] < character_history["x"]

        character_description = "銀白短髮、琥珀眼睛的鐘錶修復師，穿深藍工作服。"
        page.locator("#character-prompt").fill(character_description)
        page.locator("#confirm-character-generation").click()
        page.get_by_text(
            "角色生成 Agent 尚未接入；目前只確認本頁設定，不會送出或建立圖片。",
            exact=True,
        ).wait_for()
        assert (
            page.locator("#confirm-character-generation-label").inner_text()
            == "設定已確認"
        )
        assert page.locator("#character-history").get_attribute("data-state") == "empty"

        page.locator("#character-tab").press("ArrowDown")
        assert page.locator("#scene-tab").get_attribute("aria-selected") == "true"
        assert page.locator("#scene-panel").is_visible()
        assert page.locator("#confirm-scene-generation").inner_text() == "確認生成"
        assert page.locator("#scene-history").is_visible()
        assert page.locator("#scene-history").get_attribute("data-state") == "empty"
        assert page.locator("#scene-history-list .history-empty").count() == 1
        assert page.get_by_text("尚無場景紀錄", exact=True).is_visible()

        scene_editor = page.locator(
            "#scene-generation-form > .editor-stack"
        ).bounding_box()
        scene_showcase = page.locator("#scene-panel .scene-showcase").bounding_box()
        scene_history = page.locator("#scene-history").bounding_box()
        assert scene_editor and scene_showcase and scene_history
        assert scene_editor["x"] < scene_showcase["x"] < scene_history["x"]

        page.locator("#scene-prompt").fill("雨後的老城鐘樓工坊，冷藍月光穿過百葉窗。")
        page.locator("#confirm-scene-generation").click()
        page.get_by_text(
            "場景生成 Agent 尚未接入；目前只確認本頁設定，不會送出或建立圖片。",
            exact=True,
        ).wait_for()
        assert (
            page.locator("#confirm-scene-generation-label").inner_text() == "設定已確認"
        )
        assert page.locator("#scene-history").get_attribute("data-state") == "empty"

        page.get_by_role("tab", name="生成分鏡").click()
        assert page.locator("#storyboard-panel").is_visible()
        page.get_by_role("tab", name="生成角色").click()

        _assert_no_horizontal_overflow(page)
        desktop_screenshot = tmp_path / "style-showcase-desktop.png"
        page.screenshot(path=desktop_screenshot, full_page=True)

        page.set_viewport_size({"width": 1101, "height": 900})
        page.reload(wait_until="networkidle")
        style_cards.first.wait_for(state="visible")
        narrow_showcase = page.locator(
            "#character-panel .character-showcase"
        ).bounding_box()
        narrow_history = page.locator("#character-history").bounding_box()
        assert narrow_showcase and narrow_history
        assert narrow_history["x"] > narrow_showcase["x"]
        _assert_no_horizontal_overflow(page)

        page.set_viewport_size({"width": 1100, "height": 900})
        page.reload(wait_until="networkidle")
        style_cards.first.wait_for(state="visible")
        stacked_showcase = page.locator(
            "#character-panel .character-showcase"
        ).bounding_box()
        stacked_history = page.locator("#character-history").bounding_box()
        assert stacked_showcase and stacked_history
        assert stacked_history["y"] > stacked_showcase["y"]
        _assert_no_horizontal_overflow(page)

        page.set_viewport_size({"width": 390, "height": 844})
        page.reload(wait_until="networkidle")
        style_cards.first.wait_for(state="visible")
        assert (
            page.locator("#workspace-tabs").get_attribute("aria-orientation")
            == "horizontal"
        )
        first_two_positions = style_cards.locator(".style-visual").evaluate_all(
            """
            (items) => items.slice(0, 2).map((item) => {
              const rect = item.getBoundingClientRect();
              return { top: rect.top, left: rect.left };
            })
            """
        )
        assert len(first_two_positions) == 2
        assert abs(first_two_positions[0]["top"] - first_two_positions[1]["top"]) < 2
        assert first_two_positions[1]["left"] > first_two_positions[0]["left"]
        mobile_showcase = page.locator(
            "#character-panel .character-showcase"
        ).bounding_box()
        mobile_history = page.locator("#character-history").bounding_box()
        assert mobile_showcase and mobile_history
        assert mobile_history["y"] > mobile_showcase["y"]
        _assert_no_horizontal_overflow(page)
        mobile_screenshot = tmp_path / "style-showcase-mobile.png"
        page.screenshot(path=mobile_screenshot, full_page=True)

        assert desktop_screenshot.stat().st_size > 20_000
        assert mobile_screenshot.stat().st_size > 10_000
        assert mutation_requests == []
        assert fake.thread_count == 0
        assert fake.turn_count == 0
        context.close()
        browser.close()


def test_storyboard_workflow_selects_one_candidate_before_4k_and_handles_error(
    tmp_path: Path,
) -> None:
    fake = BrowserFakeCodexClient()
    workflow = StoryboardWorkflowRouteMock()
    scene_path = tmp_path / "scene.png"
    character_path = tmp_path / "character-front.png"
    replacement_scene_path = tmp_path / "replacement-scene.png"
    scene_path.write_bytes(ONE_PIXEL_PNG)
    character_path.write_bytes(ONE_PIXEL_PNG)
    replacement_scene_path.write_bytes(ONE_PIXEL_PNG)

    with (
        _run_gateway_server(fake) as base_url,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-TW",
        )
        page = context.new_page()
        page.route(
            "**/api/v1/gateway/workflows/**",
            workflow.handle,
        )
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("tab", name="生成分鏡").click()

        composition_prompt = (
            "把角色放進場景左側，保留角色五官、服裝與場景構圖，"
            "讓角色受光方向與場景一致。"
        )
        page.locator("#storyboard-scene-file").set_input_files(scene_path)
        page.locator("#storyboard-character-file").set_input_files(character_path)
        page.locator("#storyboard-prompt").fill(composition_prompt)
        page.locator("#storyboard-candidate-count").select_option("3")

        assert page.locator("#storyboard-upscale-form").is_hidden()
        page.locator("#generate-storyboard-button").click()
        assert page.locator("#generate-storyboard-button").is_disabled()
        page.get_by_text(
            "候選已完成，請逐張檢查並明確選定一張。",
            exact=True,
        ).wait_for()

        assert len(workflow.create_requests) == 1
        created = workflow.create_requests[0]
        assert created["request"] == {
            "prompt": composition_prompt,
            "candidate_count": 3,
        }
        assert created["scene_filename"] == "scene.png"
        assert created["character_filename"] == "character-front.png"

        candidate_cards = page.locator("#storyboard-candidate-grid .candidate-card")
        assert candidate_cards.count() == 3
        assert page.locator("#confirm-storyboard-button").is_disabled()
        assert page.locator("#storyboard-upscale-form").is_hidden()

        candidate_cards.nth(0).click()
        assert not page.locator("#confirm-storyboard-button").is_disabled()
        first_candidate_id = f"cand_{1:032x}"
        assert f"{first_candidate_id}/image" in (
            page.locator("#storyboard-result-image").get_attribute("src") or ""
        )
        assert page.locator("#storyboard-upscale-form").is_hidden()

        page.locator("#confirm-storyboard-button").click()
        page.get_by_text(
            "已確認此候選；現在可以填寫 4K 細節描述。",
            exact=True,
        ).wait_for()
        assert workflow.selection_requests == [{"candidate_id": first_candidate_id}]
        assert page.locator("#storyboard-upscale-form").is_visible()
        assert (
            page.locator("#upscale-refine-prompt").input_value() == composition_prompt
        )
        assert not page.locator("#upscale-storyboard-button").is_disabled()

        candidate_cards.nth(1).click()
        selected_candidate_id = f"cand_{2:032x}"
        assert page.locator("#storyboard-upscale-form").is_hidden()
        assert not page.locator("#confirm-storyboard-button").is_disabled()
        page.locator("#confirm-storyboard-button").click()
        page.get_by_text(
            "已確認此候選；現在可以填寫 4K 細節描述。",
            exact=True,
        ).wait_for()
        assert workflow.selection_requests == [
            {"candidate_id": first_candidate_id},
            {"candidate_id": selected_candidate_id},
        ]
        assert page.locator("#storyboard-upscale-form").is_visible()

        refine_prompt = (
            "完整保留目前角色的臉、服裝與站姿，以及場景的構圖和光線；"
            "柔化髮絲並維持自然接觸陰影。"
        )
        page.locator("#upscale-refine-prompt").fill(refine_prompt)
        workflow.conflict_next_upscale = True
        workflow.transient_poll_failures_remaining = 1
        poll_count_before_upscale = workflow.poll_request_count
        page.locator("#upscale-storyboard-button").click()
        assert page.locator("#upscale-storyboard-button").is_disabled()
        page.get_by_text(
            "4K 工作狀態已變更，正在重新查詢既有工作；不會重複建立工作。",
            exact=True,
        ).wait_for()
        page.get_by_text(
            "進度連線暫時中斷，1 秒後重試（1 / 5）；既有工作仍保留，請勿重新送出。",
            exact=True,
        ).wait_for()
        assert page.locator("#storyboard-scene-file").is_disabled()
        assert page.locator("#storyboard-character-file").is_disabled()
        assert page.locator("#storyboard-prompt").is_disabled()
        assert page.locator("#storyboard-candidate-count").is_disabled()
        assert page.locator("#generate-storyboard-button").is_disabled()
        assert all(
            page.locator('input[name="storyboard-candidate"]').nth(index).is_disabled()
            for index in range(3)
        )
        assert len(workflow.create_requests) == 1
        assert len(workflow.upscale_requests) == 1
        page.get_by_text(
            "4K 定稿完成，可在右側預覽或下載。",
            exact=True,
        ).wait_for()

        assert workflow.upscale_requests == [
            {
                "refine_prompt": refine_prompt,
                "expected_candidate_id": selected_candidate_id,
                "server_selected_candidate_id": selected_candidate_id,
            }
        ]
        assert "candidate_id" not in workflow.upscale_requests[0]
        assert workflow.poll_request_count - poll_count_before_upscale == 2
        result_image_src = (
            page.locator("#storyboard-result-image").get_attribute("src") or ""
        )
        assert result_image_src.endswith(
            f"/api/v1/gateway/workflows/storyboards/{BROWSER_RUN_ID}/upscale/image"
        )
        upscale_download = (
            page.locator("#upscale4k-download-link").get_attribute("href") or ""
        )
        assert upscale_download.endswith(
            f"/api/v1/gateway/workflows/storyboards/{BROWSER_RUN_ID}/upscale/download"
        )
        assert (
            "3840 × 2160" in page.locator("#storyboard-result-description").inner_text()
        )
        assert (
            "絕不自動放大其他候選"
            in page.locator("#storyboard-upscale-form").inner_text()
        )
        assert (
            "非 16:9 來源會置中裁切"
            in page.locator("#storyboard-upscale-form").inner_text()
        )
        assert not page.locator("#storyboard-scene-file").is_disabled()
        assert not page.locator("#storyboard-prompt").is_disabled()
        assert not page.locator("#generate-storyboard-button").is_disabled()
        assert all(
            page.locator('input[name="storyboard-candidate"]').nth(index).is_disabled()
            for index in range(3)
        )

        page.set_viewport_size({"width": 390, "height": 844})
        candidate_positions = candidate_cards.evaluate_all(
            """
            (items) => items.map((item) => {
              const rect = item.getBoundingClientRect();
              return { top: rect.top, bottom: rect.bottom, left: rect.left };
            })
            """
        )
        assert candidate_positions[1]["top"] > candidate_positions[0]["bottom"]
        assert abs(candidate_positions[1]["left"] - candidate_positions[0]["left"]) < 2
        _assert_no_horizontal_overflow(page)

        workflow.fail_next_create = True
        page.locator("#storyboard-scene-file").set_input_files(replacement_scene_path)
        assert page.locator("#storyboard-upscale-form").is_hidden()
        page.locator("#generate-storyboard-button").click()
        page.get_by_text("ComfyUI 目前無法連線。", exact=True).wait_for()
        assert not page.locator("#generate-storyboard-button").is_disabled()
        generation_status = page.locator("#storyboard-generation-status")
        assert generation_status.get_attribute("role") == "status"
        assert generation_status.get_attribute("aria-live") == "polite"
        assert generation_status.get_attribute("data-kind") == "error"

        assert fake.thread_count == 0
        assert fake.turn_count == 0
        context.close()
        browser.close()
