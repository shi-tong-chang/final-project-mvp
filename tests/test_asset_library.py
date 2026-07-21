from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path

import pytest
from app.core.workflow_settings import WorkflowSettings
from app.schemas.api.assets import CharacterView
from app.services.assets import AssetLibraryError, AssetLibraryService
from app.services.workflows.images import NormalizedImage
from PIL import Image


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


def _settings(tmp_path: Path) -> WorkflowSettings:
    return WorkflowSettings(
        repo_root=tmp_path,
        workflow_root=tmp_path / "workflows",
        asset_library_root=tmp_path / ".local-data" / "asset-library",
    )


def _character_images() -> dict[CharacterView, bytes]:
    return {
        CharacterView.FRONT: _image_bytes(48, 64, (180, 20, 30)),
        CharacterView.LEFT: _image_bytes(49, 64, (20, 180, 30)),
        CharacterView.RIGHT: _image_bytes(
            50,
            64,
            (20, 30, 180),
            image_format="JPEG",
        ),
        CharacterView.BACK: _image_bytes(51, 64, (120, 80, 40)),
    }


def test_default_asset_library_does_not_claim_runtime_state(tmp_path: Path) -> None:
    settings = WorkflowSettings(repo_root=tmp_path)

    async def exercise() -> None:
        service = AssetLibraryService(settings)
        await service.start()

    asyncio.run(exercise())

    assert settings.asset_library_root == (tmp_path / ".local-data" / "asset-library")
    assert settings.asset_library_root.is_dir()
    assert not (tmp_path / ".runtime").exists()


def test_asset_library_root_cannot_override_runtime_ownership(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="asset_library_root"):
        WorkflowSettings(
            repo_root=tmp_path,
            asset_library_root=tmp_path / ".runtime" / "asset-library",
        )

    configured = WorkflowSettings(
        repo_root=tmp_path,
        asset_library_root=tmp_path / ".local-data" / "alternate-library",
    )
    assert configured.asset_library_root == (
        tmp_path / ".local-data" / "alternate-library"
    )


def test_asset_library_registers_lists_reads_and_persists_complete_assets(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    async def exercise() -> None:
        service = AssetLibraryService(settings)
        await service.start()
        character = await service.register_character(
            name="亞瑟",
            description="黑髮、金色護甲的完整角色模板。",
            images=_character_images(),
        )
        scene = await service.register_scene(
            name="黃金花園",
            description="午後金色陽光照入的花園。",
            image=_image_bytes(96, 64, (220, 190, 90), image_format="WEBP"),
        )

        assert character.asset_id.startswith("char_")
        assert scene.asset_id.startswith("scene_")
        assert character.views.model_dump() == {
            "front": (f"/api/v1/gateway/assets/characters/{character.asset_id}/front"),
            "left": f"/api/v1/gateway/assets/characters/{character.asset_id}/left",
            "right": (f"/api/v1/gateway/assets/characters/{character.asset_id}/right"),
            "back": f"/api/v1/gateway/assets/characters/{character.asset_id}/back",
        }
        assert scene.image_url == (
            f"/api/v1/gateway/assets/scenes/{scene.asset_id}/image"
        )

        snapshot = await service.list_assets()
        assert [item.asset_id for item in snapshot.characters] == [character.asset_id]
        assert [item.asset_id for item in snapshot.scenes] == [scene.asset_id]

        expected_sizes = {
            CharacterView.FRONT: (48, 64),
            CharacterView.LEFT: (49, 64),
            CharacterView.RIGHT: (50, 64),
            CharacterView.BACK: (51, 64),
        }
        for view, expected_size in expected_sizes.items():
            image = await service.get_character_image(character.asset_id, view)
            assert (image.width, image.height) == expected_size
            assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
        scene_image = await service.get_scene_image(scene.asset_id)
        assert (scene_image.width, scene_image.height) == (96, 64)
        assert scene_image.content.startswith(b"\x89PNG\r\n\x1a\n")

        character_dir = settings.asset_library_root / "characters" / character.asset_id
        scene_dir = settings.asset_library_root / "scenes" / scene.asset_id
        assert {item.name for item in character_dir.iterdir()} == {
            "metadata.json",
            "front.png",
            "left.png",
            "right.png",
            "back.png",
        }
        assert {item.name for item in scene_dir.iterdir()} == {
            "metadata.json",
            "scene.png",
        }
        metadata = json.loads((character_dir / "metadata.json").read_bytes())
        assert set(metadata) == {
            "schema_version",
            "kind",
            "asset_id",
            "name",
            "description",
            "created_at",
        }
        assert metadata["schema_version"] == "storyboard-studio.character-asset.v1"
        assert metadata["kind"] == "character"

        restarted = AssetLibraryService(settings)
        await restarted.start()
        persisted = await restarted.list_assets()
        assert [item.asset_id for item in persisted.characters] == [character.asset_id]
        assert [item.asset_id for item in persisted.scenes] == [scene.asset_id]
        persisted_front = await restarted.get_character_image(
            character.asset_id,
            CharacterView.FRONT,
        )
        assert (persisted_front.width, persisted_front.height) == (48, 64)

    asyncio.run(exercise())


def test_asset_library_rejects_incomplete_character_package(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def exercise() -> None:
        service = AssetLibraryService(settings)
        await service.start()
        incomplete = _character_images()
        del incomplete[CharacterView.BACK]

        with pytest.raises(AssetLibraryError) as captured:
            await service.register_character(
                name="缺背面角色",
                description="這個角色包不完整。",
                images=incomplete,
            )

        assert captured.value.code == "ASSET_INVALID_CHARACTER_PACKAGE"
        assert captured.value.status_code == 422
        assert list((settings.asset_library_root / "characters").iterdir()) == []

    asyncio.run(exercise())


def test_asset_library_unknown_and_corrupt_assets_fail_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def exercise() -> None:
        service = AssetLibraryService(settings)
        await service.start()
        character = await service.register_character(
            name="完整角色",
            description="用來驗證損壞素材拒絕。",
            images=_character_images(),
        )

        with pytest.raises(AssetLibraryError) as unknown:
            await service.get_character_image(
                "char_00000000000000000000000000000000",
                CharacterView.FRONT,
            )
        assert unknown.value.code == "ASSET_NOT_FOUND"
        assert unknown.value.status_code == 404

        asset_dir = settings.asset_library_root / "characters" / character.asset_id
        metadata_path = asset_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_bytes())
        metadata["unexpected"] = "browser must not extend metadata"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        with pytest.raises(AssetLibraryError) as corrupt:
            await service.list_assets()
        assert corrupt.value.code == "ASSET_LIBRARY_CORRUPT"
        assert corrupt.value.status_code == 500
        assert str(settings.asset_library_root) not in corrupt.value.message

    asyncio.run(exercise())


def test_asset_library_rejects_symlinked_image(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def exercise() -> None:
        service = AssetLibraryService(settings)
        await service.start()
        scene = await service.register_scene(
            name="安全場景",
            description="用來驗證符號連結拒絕。",
            image=_image_bytes(96, 64, (20, 40, 60)),
        )
        scene_path = (
            settings.asset_library_root / "scenes" / scene.asset_id / "scene.png"
        )
        outside = tmp_path / "outside.png"
        outside.write_bytes(scene_path.read_bytes())
        scene_path.unlink()
        scene_path.symlink_to(outside)

        with pytest.raises(AssetLibraryError) as symlinked:
            await service.get_scene_image(scene.asset_id)
        assert symlinked.value.code == "ASSET_LIBRARY_CORRUPT"
        assert symlinked.value.status_code == 500

    asyncio.run(exercise())


def test_asset_library_decodes_only_requested_workflow_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)

    async def exercise() -> None:
        service = AssetLibraryService(settings)
        await service.start()
        character = await service.register_character(
            name="完整角色",
            description="驗證 list 不解碼四視圖。",
            images=_character_images(),
        )
        scene = await service.register_scene(
            name="安全場景",
            description="驗證 workflow 只解碼場景與正面圖。",
            image=_image_bytes(96, 64, (20, 40, 60)),
        )

        decoded_filenames: list[str] = []
        original = AssetLibraryService._read_canonical_png

        def counted_decode(
            target: AssetLibraryService,
            path: Path,
        ) -> NormalizedImage:
            decoded_filenames.append(path.name)
            return original(target, path)

        monkeypatch.setattr(
            AssetLibraryService,
            "_read_canonical_png",
            counted_decode,
        )

        await service.list_assets()
        assert decoded_filenames == []

        await service.get_character_image(character.asset_id, CharacterView.LEFT)
        assert decoded_filenames == ["left.png"]

        decoded_filenames.clear()
        resolved = await service.resolve_storyboard_assets(
            scene.asset_id,
            (character.asset_id,),
        )
        assert resolved.scene_name == "安全場景"
        assert resolved.character_names == ("完整角色",)
        assert decoded_filenames == ["scene.png", "front.png"]

    asyncio.run(exercise())
