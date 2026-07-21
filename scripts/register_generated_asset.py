"""將 Agent 生成的角色四視圖或場景登錄到本機素材庫。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.workflow_settings import WorkflowSettings  # noqa: E402
from app.schemas.api.assets import CharacterView  # noqa: E402
from app.services.assets import AssetLibraryError, AssetLibraryService  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Trusted CLI：登錄完整角色四視圖或場景單圖，不開放 browser 寫入素材庫。"
        )
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--asset-library-root", type=Path)
    subparsers = parser.add_subparsers(dest="asset_kind", required=True)

    character = subparsers.add_parser("character", help="登錄角色四視圖")
    _add_metadata_arguments(character)
    character.add_argument("--front", type=Path, required=True)
    character.add_argument("--left", type=Path, required=True)
    character.add_argument("--right", type=Path, required=True)
    character.add_argument("--back", type=Path, required=True)

    scene = subparsers.add_parser("scene", help="登錄場景單圖")
    _add_metadata_arguments(scene)
    scene.add_argument("--image", type=Path, required=True)
    return parser


def _add_metadata_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", required=True)


def _read_bounded(path: Path, *, max_bytes: int) -> bytes:
    try:
        info = path.stat()
        if not path.is_file() or info.st_size < 1 or info.st_size > max_bytes:
            raise ValueError
        content = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise AssetLibraryError(
            "ASSET_SOURCE_INVALID",
            "輸入圖片不存在、不是一般檔案或超過安全上限。",
            status_code=422,
        ) from exc
    if len(content) != info.st_size:
        raise AssetLibraryError(
            "ASSET_SOURCE_CHANGED",
            "讀取期間輸入圖片已改變，本次沒有登錄。",
            status_code=409,
        )
    return content


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    settings = (
        WorkflowSettings(repo_root=arguments.repo_root)
        if arguments.asset_library_root is None
        else WorkflowSettings(
            repo_root=arguments.repo_root,
            asset_library_root=arguments.asset_library_root,
        )
    )
    service = AssetLibraryService(settings)
    await service.start()

    if arguments.asset_kind == "character":
        images = {
            CharacterView.FRONT: _read_bounded(
                arguments.front,
                max_bytes=settings.max_output_bytes,
            ),
            CharacterView.LEFT: _read_bounded(
                arguments.left,
                max_bytes=settings.max_output_bytes,
            ),
            CharacterView.RIGHT: _read_bounded(
                arguments.right,
                max_bytes=settings.max_output_bytes,
            ),
            CharacterView.BACK: _read_bounded(
                arguments.back,
                max_bytes=settings.max_output_bytes,
            ),
        }
        character_response = await service.register_character(
            name=arguments.name,
            description=arguments.description,
            images=images,
        )
        return character_response.model_dump(mode="json")
    scene_response = await service.register_scene(
        name=arguments.name,
        description=arguments.description,
        image=_read_bounded(
            arguments.image,
            max_bytes=settings.max_output_bytes,
        ),
    )
    return scene_response.model_dump(mode="json")


def main() -> int:
    """CLI entrypoint。"""

    parser = _parser()
    arguments = parser.parse_args()
    try:
        payload = asyncio.run(_run(arguments))
    except AssetLibraryError as exc:
        parser.exit(1, f"{exc.code}: {exc.message}\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
