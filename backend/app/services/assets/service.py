"""只允許 trusted CLI 寫入的 strict 本機素材庫。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import overload

from pydantic import ValidationError

from app.core.workflow_settings import WorkflowSettings
from app.schemas.api.assets import (
    AssetLibraryResponse,
    CharacterAssetMetadata,
    CharacterAssetResponse,
    CharacterAssetViewUrls,
    CharacterView,
    SceneAssetMetadata,
    SceneAssetResponse,
)
from app.services.workflows.images import (
    NormalizedImage,
    UnsafeImageError,
    normalize_uploaded_image,
)

_CHARACTER_ID = re.compile(r"char_[0-9a-f]{32}")
_SCENE_ID = re.compile(r"scene_[0-9a-f]{32}")
_METADATA_MAX_BYTES = 64 * 1024
_CHARACTER_FILENAMES: dict[CharacterView, str] = {
    CharacterView.FRONT: "front.png",
    CharacterView.LEFT: "left.png",
    CharacterView.RIGHT: "right.png",
    CharacterView.BACK: "back.png",
}


class AssetLibraryError(RuntimeError):
    """素材庫邊界的安全錯誤。"""

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ResolvedStoryboardAssets:
    """供 workflow service 使用的 server-resolved 素材。"""

    scene_name: str
    scene_image: NormalizedImage
    character_names: tuple[str, ...]
    character_images: tuple[NormalizedImage, ...]


class AssetLibraryService:
    """以 opaque ID 管理 repo 內 Git-ignored 的 canonical PNG。"""

    def __init__(self, settings: WorkflowSettings) -> None:
        self._settings = settings
        self._repo_root = settings.repo_root
        self._root = settings.asset_library_root
        self._characters_root = self._root / "characters"
        self._scenes_root = self._root / "scenes"

    async def start(self) -> None:
        """在 worker thread 建立安全的素材庫目錄。"""

        await asyncio.to_thread(self._initialize_sync)

    async def list_assets(self) -> AssetLibraryResponse:
        """列出已驗證 metadata、目錄結構與 PNG signature 的素材。"""

        return await asyncio.to_thread(self._list_assets_sync)

    async def get_character_image(
        self,
        asset_id: str,
        view: CharacterView,
    ) -> NormalizedImage:
        """依 opaque ID 與固定視圖名讀取角色 PNG。"""

        return await asyncio.to_thread(
            self._load_character_image_sync,
            asset_id,
            view,
        )

    async def get_scene_image(self, asset_id: str) -> NormalizedImage:
        """依 opaque ID 讀取場景 PNG。"""

        return await asyncio.to_thread(self._load_scene_image_sync, asset_id)

    async def resolve_storyboard_assets(
        self,
        scene_asset_id: str,
        character_asset_ids: tuple[str, ...],
    ) -> ResolvedStoryboardAssets:
        """在單一 thread 快照中解析 workflow 需要的場景與角色正面圖。"""

        return await asyncio.to_thread(
            self._resolve_storyboard_assets_sync,
            scene_asset_id,
            character_asset_ids,
        )

    async def register_character(
        self,
        *,
        name: str,
        description: str,
        images: Mapping[CharacterView, bytes],
    ) -> CharacterAssetResponse:
        """以 atomic directory rename 登錄完整四視圖角色包。"""

        return await asyncio.to_thread(
            self._register_character_sync,
            name,
            description,
            dict(images),
        )

    async def register_scene(
        self,
        *,
        name: str,
        description: str,
        image: bytes,
    ) -> SceneAssetResponse:
        """以 atomic directory rename 登錄單圖場景。"""

        return await asyncio.to_thread(
            self._register_scene_sync,
            name,
            description,
            image,
        )

    def _initialize_sync(self) -> None:
        self._ensure_directory_chain(self._root)
        self._ensure_directory_chain(self._characters_root)
        self._ensure_directory_chain(self._scenes_root)

    def _list_assets_sync(self) -> AssetLibraryResponse:
        self._initialize_sync()
        characters = [
            self._character_response(self._load_character_metadata_sync(asset_id)[1])
            for asset_id in self._list_asset_ids(
                self._characters_root,
                pattern=_CHARACTER_ID,
            )
        ]
        scenes = [
            self._scene_response(self._load_scene_metadata_sync(asset_id)[1])
            for asset_id in self._list_asset_ids(
                self._scenes_root,
                pattern=_SCENE_ID,
            )
        ]
        characters.sort(key=lambda item: (item.created_at, item.asset_id), reverse=True)
        scenes.sort(key=lambda item: (item.created_at, item.asset_id), reverse=True)
        return AssetLibraryResponse(
            characters=tuple(characters),
            scenes=tuple(scenes),
        )

    def _register_character_sync(
        self,
        name: str,
        description: str,
        images: dict[CharacterView, bytes],
    ) -> CharacterAssetResponse:
        self._initialize_sync()
        if set(images) != set(CharacterView):
            raise AssetLibraryError(
                "ASSET_INVALID_CHARACTER_PACKAGE",
                "角色包必須精確包含 front、left、right、back 四視圖。",
                status_code=422,
            )
        normalized = {
            view: self._normalize_registration_image(raw)
            for view, raw in images.items()
        }
        asset_id = self._new_asset_id("char", self._characters_root)
        try:
            metadata = CharacterAssetMetadata(
                schema_version="storyboard-studio.character-asset.v1",
                kind="character",
                asset_id=asset_id,
                name=name,
                description=description,
                created_at=datetime.now(UTC),
            )
        except ValidationError as exc:
            raise self._invalid_metadata_error() from exc
        files = {
            _CHARACTER_FILENAMES[view]: image.content
            for view, image in normalized.items()
        }
        self._write_asset_atomically(
            self._characters_root,
            asset_id,
            files,
            metadata.model_dump_json(indent=2).encode("utf-8") + b"\n",
        )
        return self._character_response(metadata)

    def _register_scene_sync(
        self,
        name: str,
        description: str,
        image: bytes,
    ) -> SceneAssetResponse:
        self._initialize_sync()
        normalized = self._normalize_registration_image(image)
        asset_id = self._new_asset_id("scene", self._scenes_root)
        try:
            metadata = SceneAssetMetadata(
                schema_version="storyboard-studio.scene-asset.v1",
                kind="scene",
                asset_id=asset_id,
                name=name,
                description=description,
                created_at=datetime.now(UTC),
            )
        except ValidationError as exc:
            raise self._invalid_metadata_error() from exc
        self._write_asset_atomically(
            self._scenes_root,
            asset_id,
            {"scene.png": normalized.content},
            metadata.model_dump_json(indent=2).encode("utf-8") + b"\n",
        )
        return self._scene_response(metadata)

    def _resolve_storyboard_assets_sync(
        self,
        scene_asset_id: str,
        character_asset_ids: tuple[str, ...],
    ) -> ResolvedStoryboardAssets:
        if not 1 <= len(character_asset_ids) <= 2 or len(
            set(character_asset_ids)
        ) != len(character_asset_ids):
            raise AssetLibraryError(
                "ASSET_INVALID_SELECTION",
                "角色素材必須是一或兩個不重複的 opaque ID。",
                status_code=422,
            )
        scene_dir, scene_metadata = self._load_scene_metadata_sync(scene_asset_id)
        scene_image = self._read_canonical_png(scene_dir / "scene.png")
        characters = tuple(
            self._load_character_metadata_sync(asset_id)
            for asset_id in character_asset_ids
        )
        return ResolvedStoryboardAssets(
            scene_name=scene_metadata.name,
            scene_image=scene_image,
            character_names=tuple(metadata.name for _, metadata in characters),
            character_images=tuple(
                self._read_canonical_png(asset_dir / "front.png")
                for asset_dir, _ in characters
            ),
        )

    def _load_character_metadata_sync(
        self,
        asset_id: str,
    ) -> tuple[Path, CharacterAssetMetadata]:
        asset_dir = self._safe_asset_directory(
            self._characters_root,
            asset_id,
            pattern=_CHARACTER_ID,
        )
        metadata = self._read_metadata(
            asset_dir / "metadata.json",
            CharacterAssetMetadata,
        )
        if metadata.asset_id != asset_id:
            raise self._corrupt_error()
        self._reject_unexpected_entries(
            asset_dir,
            {"metadata.json", *_CHARACTER_FILENAMES.values()},
        )
        for filename in _CHARACTER_FILENAMES.values():
            self._validate_png_signature(asset_dir / filename)
        return asset_dir, metadata

    def _load_scene_metadata_sync(
        self,
        asset_id: str,
    ) -> tuple[Path, SceneAssetMetadata]:
        asset_dir = self._safe_asset_directory(
            self._scenes_root,
            asset_id,
            pattern=_SCENE_ID,
        )
        metadata = self._read_metadata(asset_dir / "metadata.json", SceneAssetMetadata)
        if metadata.asset_id != asset_id:
            raise self._corrupt_error()
        self._reject_unexpected_entries(asset_dir, {"metadata.json", "scene.png"})
        self._validate_png_signature(asset_dir / "scene.png")
        return asset_dir, metadata

    def _load_character_image_sync(
        self,
        asset_id: str,
        view: CharacterView,
    ) -> NormalizedImage:
        asset_dir, _ = self._load_character_metadata_sync(asset_id)
        return self._read_canonical_png(asset_dir / _CHARACTER_FILENAMES[view])

    def _load_scene_image_sync(self, asset_id: str) -> NormalizedImage:
        asset_dir, _ = self._load_scene_metadata_sync(asset_id)
        return self._read_canonical_png(asset_dir / "scene.png")

    def _normalize_registration_image(self, raw: bytes) -> NormalizedImage:
        if len(raw) > self._settings.max_output_bytes:
            raise AssetLibraryError(
                "ASSET_IMAGE_TOO_LARGE",
                "素材圖片超過安全大小上限。",
                status_code=422,
            )
        try:
            normalized = normalize_uploaded_image(
                raw,
                content_type=self._detect_content_type(raw),
                settings=self._settings,
            )
        except UnsafeImageError as exc:
            raise AssetLibraryError(
                "ASSET_INVALID_IMAGE",
                str(exc),
                status_code=422,
            ) from exc
        if len(normalized.content) > self._settings.max_output_bytes:
            raise AssetLibraryError(
                "ASSET_IMAGE_TOO_LARGE",
                "素材圖片正規化後超過安全大小上限。",
                status_code=422,
            )
        return normalized

    def _read_canonical_png(self, path: Path) -> NormalizedImage:
        raw = self._read_regular_file(path, max_bytes=self._settings.max_output_bytes)
        try:
            normalized = normalize_uploaded_image(
                raw,
                content_type="image/png",
                settings=self._settings,
            )
        except UnsafeImageError as exc:
            raise self._corrupt_error() from exc
        if normalized.content != raw:
            raise self._corrupt_error()
        return normalized

    def _validate_png_signature(self, path: Path) -> None:
        """不解碼圖片，只驗證 regular file、大小與 PNG signature。"""

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_size < 8
                    or info.st_size > self._settings.max_output_bytes
                ):
                    raise self._corrupt_error()
                signature = stream.read(8)
        except AssetLibraryError:
            raise
        except OSError as exc:
            raise self._corrupt_error() from exc
        if signature != b"\x89PNG\r\n\x1a\n":
            raise self._corrupt_error()

    @overload
    def _read_metadata(
        self,
        path: Path,
        model: type[CharacterAssetMetadata],
    ) -> CharacterAssetMetadata: ...

    @overload
    def _read_metadata(
        self,
        path: Path,
        model: type[SceneAssetMetadata],
    ) -> SceneAssetMetadata: ...

    def _read_metadata(
        self,
        path: Path,
        model: type[CharacterAssetMetadata] | type[SceneAssetMetadata],
    ) -> CharacterAssetMetadata | SceneAssetMetadata:
        encoded = self._read_regular_file(path, max_bytes=_METADATA_MAX_BYTES)
        try:
            payload = json.loads(encoded)
            if not isinstance(payload, dict):
                raise ValueError("metadata must be an object")
            return model.model_validate(payload)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise self._corrupt_error() from exc

    def _safe_asset_directory(
        self,
        category_root: Path,
        asset_id: str,
        *,
        pattern: re.Pattern[str],
    ) -> Path:
        if pattern.fullmatch(asset_id) is None:
            raise AssetLibraryError(
                "ASSET_NOT_FOUND",
                "找不到這個本機素材。",
                status_code=404,
            )
        self._initialize_sync()
        asset_dir = category_root / asset_id
        try:
            info = asset_dir.lstat()
        except FileNotFoundError as exc:
            raise AssetLibraryError(
                "ASSET_NOT_FOUND",
                "找不到這個本機素材。",
                status_code=404,
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise self._corrupt_error()
        try:
            resolved = asset_dir.resolve(strict=True)
        except OSError as exc:
            raise self._corrupt_error() from exc
        if not resolved.is_relative_to(category_root.resolve(strict=True)):
            raise self._corrupt_error()
        return asset_dir

    def _list_asset_ids(
        self,
        category_root: Path,
        *,
        pattern: re.Pattern[str],
    ) -> list[str]:
        asset_ids: list[str] = []
        try:
            entries = list(os.scandir(category_root))
        except OSError as exc:
            raise self._corrupt_error() from exc
        for entry in entries:
            if entry.name.startswith(".registering-"):
                try:
                    info = os.lstat(entry.path)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise self._corrupt_error() from exc
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise self._corrupt_error()
                continue
            if (
                pattern.fullmatch(entry.name) is None
                or entry.is_symlink()
                or not entry.is_dir(follow_symlinks=False)
            ):
                raise self._corrupt_error()
            asset_ids.append(entry.name)
        return asset_ids

    def _read_regular_file(self, path: Path, *, max_bytes: int) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                    raise self._corrupt_error()
                raw = stream.read(max_bytes + 1)
        except AssetLibraryError:
            raise
        except OSError as exc:
            raise self._corrupt_error() from exc
        if not raw or len(raw) > max_bytes:
            raise self._corrupt_error()
        return raw

    def _ensure_directory_chain(self, target: Path) -> None:
        if not target.is_relative_to(self._repo_root):
            raise self._corrupt_error()
        current = self._repo_root
        for part in target.relative_to(self._repo_root).parts:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    info = current.lstat()
                except OSError as exc:
                    raise self._corrupt_error() from exc
                else:
                    continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise self._corrupt_error()

    def _write_asset_atomically(
        self,
        category_root: Path,
        asset_id: str,
        image_files: Mapping[str, bytes],
        metadata: bytes,
    ) -> None:
        target = category_root / asset_id
        staging = category_root / f".registering-{asset_id}-{uuid.uuid4().hex}"
        try:
            staging.mkdir(mode=0o700)
            for filename, content in image_files.items():
                self._write_new_file(staging / filename, content)
            self._write_new_file(staging / "metadata.json", metadata)
            self._fsync_directory(staging)
            os.replace(staging, target)
            self._fsync_directory(category_root)
        except (AssetLibraryError, OSError) as exc:
            with contextlib.suppress(OSError):
                shutil.rmtree(staging)
            if isinstance(exc, AssetLibraryError):
                raise
            raise AssetLibraryError(
                "ASSET_REGISTRATION_FAILED",
                "無法完成本機素材登錄。",
                status_code=500,
            ) from exc

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        with contextlib.suppress(OSError):
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    @staticmethod
    def _reject_unexpected_entries(asset_dir: Path, expected: set[str]) -> None:
        try:
            actual = {entry.name for entry in os.scandir(asset_dir)}
        except OSError as exc:
            raise AssetLibraryService._corrupt_error() from exc
        if actual != expected:
            raise AssetLibraryService._corrupt_error()

    @staticmethod
    def _detect_content_type(raw: bytes) -> str:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if raw.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            return "image/webp"
        return "application/octet-stream"

    @staticmethod
    def _new_asset_id(prefix: str, category_root: Path) -> str:
        while True:
            asset_id = f"{prefix}_{uuid.uuid4().hex}"
            try:
                (category_root / asset_id).lstat()
            except FileNotFoundError:
                return asset_id
            except OSError as exc:
                raise AssetLibraryService._corrupt_error() from exc

    @staticmethod
    def _character_response(
        metadata: CharacterAssetMetadata,
    ) -> CharacterAssetResponse:
        base = f"/api/v1/gateway/assets/characters/{metadata.asset_id}"
        return CharacterAssetResponse(
            asset_id=metadata.asset_id,
            name=metadata.name,
            description=metadata.description,
            created_at=metadata.created_at,
            views=CharacterAssetViewUrls(
                front=f"{base}/front",
                left=f"{base}/left",
                right=f"{base}/right",
                back=f"{base}/back",
            ),
        )

    @staticmethod
    def _scene_response(metadata: SceneAssetMetadata) -> SceneAssetResponse:
        return SceneAssetResponse(
            asset_id=metadata.asset_id,
            name=metadata.name,
            description=metadata.description,
            created_at=metadata.created_at,
            image_url=(f"/api/v1/gateway/assets/scenes/{metadata.asset_id}/image"),
        )

    @staticmethod
    def _invalid_metadata_error() -> AssetLibraryError:
        return AssetLibraryError(
            "ASSET_INVALID_METADATA",
            "素材名稱或描述未通過驗證。",
            status_code=422,
        )

    @staticmethod
    def _corrupt_error() -> AssetLibraryError:
        return AssetLibraryError(
            "ASSET_LIBRARY_CORRUPT",
            "本機素材庫內容損壞或不安全。",
            status_code=500,
        )
