"""本機角色與場景素材的唯讀 HTTP routes。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response

from app.schemas.api.assets import AssetLibraryResponse, CharacterView
from app.services.assets import AssetLibraryService

router = APIRouter(
    prefix="/api/v1/gateway/assets",
    tags=["Local Asset Library"],
)


def get_asset_library_service(request: Request) -> AssetLibraryService:
    """從 app lifespan 取得本機素材庫。"""

    return request.app.state.asset_library_service


AssetLibraryDependency = Annotated[
    AssetLibraryService,
    Depends(get_asset_library_service),
]
CharacterAssetId = Annotated[str, Path(pattern=r"^char_[0-9a-f]{32}$")]
SceneAssetId = Annotated[str, Path(pattern=r"^scene_[0-9a-f]{32}$")]


@router.get("", response_model=AssetLibraryResponse)
async def list_assets(service: AssetLibraryDependency) -> AssetLibraryResponse:
    """列出角色四視圖與場景單圖。"""

    return await service.list_assets()


@router.get("/characters/{asset_id}/{view}")
async def get_character_asset_image(
    asset_id: CharacterAssetId,
    view: CharacterView,
    service: AssetLibraryDependency,
) -> Response:
    """供應已重新驗證的角色 canonical PNG。"""

    image = await service.get_character_image(asset_id, view)
    return _image_response(image.content)


@router.get("/scenes/{asset_id}/image")
async def get_scene_asset_image(
    asset_id: SceneAssetId,
    service: AssetLibraryDependency,
) -> Response:
    """供應已重新驗證的場景 canonical PNG。"""

    image = await service.get_scene_image(asset_id)
    return _image_response(image.content)


def _image_response(content: bytes) -> Response:
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
        },
    )
