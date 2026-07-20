from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.gateway_settings import GatewaySettings
from app.gateway_main import create_gateway_app
from app.services.codex_gateway.client import (
    CodexClientStatus,
    CodexThread,
    CodexTurn,
)
from playwright.sync_api import Page, sync_playwright
from uvicorn import Config, Server

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_gateway_page_style_showcase_prompt_copy_and_responsive_layout(
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
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
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

        character_description = "銀白短髮、琥珀眼睛的鐘錶修復師，穿深藍工作服。"
        page.locator("#character-prompt").fill(character_description)
        page.locator("#copy-character-prompt").click()
        page.get_by_text(
            "已複製角色設定、一致性規則與所選風格提示詞。",
            exact=True,
        ).wait_for()
        copied_prompt = page.evaluate("() => navigator.clipboard.readText()")
        assert character_description in copied_prompt
        assert "五官、髮型、年齡、身形比例、服裝、配色與配件完全一致" in (copied_prompt)
        assert "黑暗童話插畫風格" in copied_prompt

        page.locator("#character-tab").press("ArrowDown")
        assert page.locator("#scene-tab").get_attribute("aria-selected") == "true"
        assert page.locator("#scene-panel").is_visible()
        page.get_by_role("tab", name="生成分鏡").click()
        assert page.locator("#storyboard-panel").is_visible()
        page.get_by_role("tab", name="生成角色").click()

        _assert_no_horizontal_overflow(page)
        desktop_screenshot = tmp_path / "style-showcase-desktop.png"
        page.screenshot(path=desktop_screenshot, full_page=True)

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
        _assert_no_horizontal_overflow(page)
        mobile_screenshot = tmp_path / "style-showcase-mobile.png"
        page.screenshot(path=mobile_screenshot, full_page=True)

        assert desktop_screenshot.stat().st_size > 20_000
        assert mobile_screenshot.stat().st_size > 10_000
        assert fake.thread_count == 0
        assert fake.turn_count == 0
        context.close()
        browser.close()
