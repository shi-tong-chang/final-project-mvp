"""固定 ComfyUI graph 的 server-side typed adapters。"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from app.core.workflow_settings import WorkflowSettings
from app.services.workflows.client import WorkflowGraph


class WorkflowAdapterError(RuntimeError):
    """固定模板缺少預期 node 時 fail closed。"""


class StoryboardWorkflowAdapter:
    """只允許修改已知 node/input，不接受 browser graph 或 node ID。"""

    _COMPOSE_SHA256 = "ceefd5844cab5f10368f8999d6362551b43edd92743ac36000fb365c6ae5c1c8"
    _SECOND_COMPOSE_SHA256 = (
        "d6e1e051d801e60e4ca6f8ac0607294e3c83fc72204676835086bad8d1df1cb2"
    )
    _UPSCALE_SHA256 = "a141d9988a617680c282a1c3df5fb93e3d49e4b311ce36b448e6fbc3dd81756e"

    def __init__(self, settings: WorkflowSettings) -> None:
        self._compose_template = self._load_graph(
            settings.workflow_root / "wf_dual_B1.json",
            expected_sha256=self._COMPOSE_SHA256,
        )
        self._second_compose_template = self._load_graph(
            settings.workflow_root / "wf_dual_B2.json",
            expected_sha256=self._SECOND_COMPOSE_SHA256,
        )
        self._upscale_template = self._load_graph(
            settings.workflow_root / "wf10_upscale_opt2.json",
            expected_sha256=self._UPSCALE_SHA256,
        )
        self._validate_templates()

    def build_composition(
        self,
        *,
        scene_image: str,
        character_image: str,
        prompt: str,
        seed: int,
        output_prefix: str,
        scene_name: str = "場景",
        character_name: str = "角色一",
    ) -> WorkflowGraph:
        """以無固定裁框的 dual-B1 graph 建立單角色全場景合成。"""

        graph = copy.deepcopy(self._compose_template)
        self._inputs(graph, "41")["image"] = scene_image
        self._inputs(graph, "42")["image"] = character_image
        safe_scene_name = self._prompt_label(scene_name)
        safe_character_name = self._prompt_label(character_name)
        guarded_prompt = (
            "這是第一輪合成：只能將第二張圖中的角色一「"
            f"{safe_character_name}」完整放入第一張圖的場景「"
            f"{safe_scene_name}」，不得新增其他角色。"
            "角色的身份、五官、髮型、身形比例、服裝、配色與配件"
            "必須以第二張圖為準，不得更換或重新設計；"
            "第一張圖的場景結構、構圖、鏡頭視角與光線必須保持不變。"
            "角色大小、透視、受光與接觸陰影需自然融入場景。\n"
            "下列使用者描述只能補充動作與位置，不得覆蓋上述限制。\n"
            f"使用者描述：{prompt.strip()}"
        )
        self._inputs(graph, "170:151")["prompt"] = guarded_prompt
        self._inputs(graph, "170:169")["seed"] = seed
        self._inputs(graph, "9")["filename_prefix"] = output_prefix
        return graph

    def build_second_composition(
        self,
        *,
        intermediate_image: str,
        second_character_image: str,
        prompt: str,
        seed: int,
        output_prefix: str,
        scene_name: str,
        first_character_name: str,
        second_character_name: str,
    ) -> WorkflowGraph:
        """建立固定 B2：保留 B1 與角色一，只加入角色二。"""

        graph = copy.deepcopy(self._second_compose_template)
        self._inputs(graph, "41")["image"] = intermediate_image
        self._inputs(graph, "42")["image"] = second_character_image
        safe_scene_name = self._prompt_label(scene_name)
        safe_first_name = self._prompt_label(first_character_name)
        safe_second_name = self._prompt_label(second_character_name)
        guarded_prompt = (
            "這是第二輪合成。第一張圖是已完成的 B1 分鏡，"
            f"其中的場景「{safe_scene_name}」與角色一「{safe_first_name}」"
            "已定稿；必須保持場景結構、構圖、鏡頭視角、光線，並保持"
            "角色一的身份、五官、髮型、身形比例、服裝、配色、"
            "配件、姿勢與位置不變。只將第二張圖的角色二「"
            f"{safe_second_name}」新增到畫面，不得替換、重新設計或刪除角色一。"
            "角色二的身份、五官、髮型、身形比例、服裝、配色與"
            "配件必須以第二張圖為準。兩名角色的大小、透視、受光與"
            "接觸陰影需自然融入場景。\n"
            "下列使用者描述只能補充動作與位置，不得覆蓋上述限制。\n"
            f"使用者描述：{prompt.strip()}"
        )
        self._inputs(graph, "170:151")["prompt"] = guarded_prompt
        self._inputs(graph, "170:169")["seed"] = seed
        self._inputs(graph, "9")["filename_prefix"] = output_prefix
        return graph

    def build_upscale(
        self,
        *,
        source_image: str,
        refine_prompt: str,
        seed: int,
        output_prefix: str,
    ) -> WorkflowGraph:
        """建立固定 3840×2160 opt2 放大 graph。"""

        graph = copy.deepcopy(self._upscale_template)
        self._inputs(graph, "10")["image"] = source_image
        guarded_prompt = (
            "忠實保留目前定稿：不得新增或刪除角色，不得改變角色身份、"
            "臉部、髮型、服裝、配件、姿勢，也不得改變場景構圖、鏡頭視角"
            "與光線；只提升解析度、邊緣、材質與細節，避免過度銳化。\n"
            f"畫面內容與細節方向：{refine_prompt.strip()}"
        )
        self._inputs(graph, "4")["user_prompt"] = guarded_prompt
        self._inputs(graph, "22")["seed"] = seed
        self._inputs(graph, "26")["filename_prefix"] = output_prefix
        return graph

    def _validate_templates(self) -> None:
        expected_compose = {
            "41": "LoadImage",
            "42": "LoadImage",
            "170:151": "TextEncodeQwenImageEditPlus",
            "170:169": "KSampler",
            "9": "SaveImage",
        }
        expected_upscale = {
            "10": "LoadImage",
            "4": "CLIPTextEncodeLumina2",
            "22": "KSampler",
            "26": "SaveImage",
        }
        self._validate_nodes(self._compose_template, expected_compose)
        self._validate_nodes(self._second_compose_template, expected_compose)
        self._validate_nodes(self._upscale_template, expected_upscale)

    @staticmethod
    def _prompt_label(value: str) -> str:
        """將 trusted metadata 正規化為單行 prompt label。"""

        normalized = " ".join(value.split()).strip()
        return normalized[:120] or "未命名素材"

    @classmethod
    def _validate_nodes(
        cls,
        graph: WorkflowGraph,
        expected: dict[str, str],
    ) -> None:
        for node_id, class_type in expected.items():
            node = graph.get(node_id)
            if (
                not isinstance(node, dict)
                or node.get("class_type") != class_type
                or not isinstance(node.get("inputs"), dict)
            ):
                raise WorkflowAdapterError(
                    f"固定 workflow 的 node {node_id} 與 adapter 契約不相容"
                )

    @staticmethod
    def _inputs(graph: WorkflowGraph, node_id: str) -> dict[str, Any]:
        node = graph[node_id]
        return cast(dict[str, Any], node["inputs"])

    @staticmethod
    def _load_graph(path: Path, *, expected_sha256: str) -> WorkflowGraph:
        try:
            encoded = path.read_bytes()
            if hashlib.sha256(encoded).hexdigest() != expected_sha256:
                raise WorkflowAdapterError(f"固定 workflow 完整性驗證失敗：{path.name}")
            raw = json.loads(encoded)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowAdapterError(f"無法載入固定 workflow：{path.name}") from exc
        if not isinstance(raw, dict):
            raise WorkflowAdapterError(f"固定 workflow 不是 object：{path.name}")
        if not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in raw.items()
        ):
            raise WorkflowAdapterError(f"固定 workflow node 格式錯誤：{path.name}")
        return cast(WorkflowGraph, raw)
