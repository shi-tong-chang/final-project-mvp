"""Storyboard 合成與 4K workflow HTTP routes。"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from app.schemas.api.workflows import (
    StoryboardCreateSpec,
    StoryboardRunResponse,
    StoryboardSelectionRequest,
    StoryboardUpscaleRequest,
    WorkflowStatusResponse,
)
from app.services.workflows.images import (
    UnsafeImageError,
    normalize_uploaded_image,
)
from app.services.workflows.service import (
    StoryboardWorkflowService,
    WorkflowServiceError,
)

router = APIRouter(
    prefix="/api/v1/gateway/workflows",
    tags=["Storyboard Workflows"],
)


def get_workflow_service(request: Request) -> StoryboardWorkflowService:
    """從 app lifespan 取得 workflow application service。"""

    return request.app.state.workflow_service


WorkflowServiceDependency = Annotated[
    StoryboardWorkflowService,
    Depends(get_workflow_service),
]
RunId = Annotated[str, Path(pattern=r"^run_[0-9a-f]{32}$")]
CandidateId = Annotated[str, Path(pattern=r"^cand_[0-9a-f]{32}$")]


@router.get("/status", response_model=WorkflowStatusResponse)
async def get_workflow_status(
    service: WorkflowServiceDependency,
) -> WorkflowStatusResponse:
    """查詢本機 ComfyUI 能力，不回傳路徑、模型或硬體資訊。"""

    return await service.status()


@router.post(
    "/storyboards",
    response_model=StoryboardRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_storyboard(
    request: Request,
    service: WorkflowServiceDependency,
) -> StoryboardRunResponse:
    """嚴格解析 request JSON、場景與單一角色正面圖。"""

    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise WorkflowServiceError(
            "WORKFLOW_INVALID_MULTIPART",
            "請使用 multipart/form-data 上傳圖片。",
            status_code=422,
        )
    try:
        form = await request.form(max_files=3, max_fields=4, max_part_size=64 * 1024)
    except (MultiPartException, StarletteHTTPException, ValueError) as exc:
        raise WorkflowServiceError(
            "WORKFLOW_INVALID_MULTIPART",
            "圖片上傳格式不正確。",
            status_code=422,
        ) from exc

    entries = list(form.multi_items())
    expected = Counter({"request": 1, "scene_image": 1, "character_image": 1})
    if Counter(key for key, _ in entries) != expected:
        await form.close()
        raise WorkflowServiceError(
            "WORKFLOW_INVALID_MULTIPART",
            "上傳欄位必須精確包含 request、scene_image、character_image。",
            status_code=422,
        )
    request_value = form.get("request")
    scene_upload = form.get("scene_image")
    character_upload = form.get("character_image")
    if (
        not isinstance(request_value, str)
        or not isinstance(scene_upload, UploadFile)
        or not isinstance(character_upload, UploadFile)
    ):
        await form.close()
        raise WorkflowServiceError(
            "WORKFLOW_INVALID_MULTIPART",
            "圖片上傳欄位類型不正確。",
            status_code=422,
        )
    try:
        decoded_request = json.loads(request_value)
        spec = StoryboardCreateSpec.model_validate(decoded_request)
    except (json.JSONDecodeError, ValidationError) as exc:
        await form.close()
        raise WorkflowServiceError(
            "WORKFLOW_INVALID_REQUEST",
            "分鏡工作設定未通過驗證。",
            status_code=422,
        ) from exc

    try:
        scene_raw, character_raw = await asyncio.gather(
            _read_bounded_upload(
                scene_upload,
                max_bytes=service.max_upload_bytes,
            ),
            _read_bounded_upload(
                character_upload,
                max_bytes=service.max_upload_bytes,
            ),
        )
        scene_image, character_image = await asyncio.gather(
            asyncio.to_thread(
                normalize_uploaded_image,
                scene_raw,
                content_type=scene_upload.content_type or "",
                settings=service.settings,
            ),
            asyncio.to_thread(
                normalize_uploaded_image,
                character_raw,
                content_type=character_upload.content_type or "",
                settings=service.settings,
            ),
        )
    except UnsafeImageError as exc:
        raise WorkflowServiceError(
            "WORKFLOW_INVALID_IMAGE",
            str(exc),
            status_code=422,
        ) from exc
    finally:
        await asyncio.gather(scene_upload.close(), character_upload.close())

    return await service.create_run(
        spec,
        scene_image=scene_image,
        character_image=character_image,
    )


@router.get(
    "/storyboards/{run_id}",
    response_model=StoryboardRunResponse,
)
async def get_storyboard(
    run_id: RunId,
    service: WorkflowServiceDependency,
) -> StoryboardRunResponse:
    """輪詢分鏡、候選選片與 4K 狀態。"""

    return await service.get_run(run_id)


@router.post(
    "/storyboards/{run_id}/selection",
    response_model=StoryboardRunResponse,
)
async def select_storyboard_candidate(
    run_id: RunId,
    payload: StoryboardSelectionRequest,
    service: WorkflowServiceDependency,
) -> StoryboardRunResponse:
    """選定屬於 run 且已完成的候選。"""

    return await service.select_candidate(run_id, payload.candidate_id)


@router.post(
    "/storyboards/{run_id}/upscale",
    response_model=StoryboardRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upscale_storyboard(
    run_id: RunId,
    payload: StoryboardUpscaleRequest,
    service: WorkflowServiceDependency,
) -> StoryboardRunResponse:
    """將已選定且 completed 的分鏡排入固定 4K workflow。"""

    return await service.queue_upscale(
        run_id,
        payload.refine_prompt,
        payload.expected_candidate_id,
    )


@router.get("/storyboards/{run_id}/candidates/{candidate_id}/image")
async def get_candidate_image(
    run_id: RunId,
    candidate_id: CandidateId,
    service: WorkflowServiceDependency,
) -> Response:
    """提供同源 PNG 預覽。"""

    image = await service.get_candidate_image(run_id, candidate_id)
    return _image_response(image.content)


@router.get("/storyboards/{run_id}/candidates/{candidate_id}/download")
async def download_candidate_image(
    run_id: RunId,
    candidate_id: CandidateId,
    service: WorkflowServiceDependency,
) -> Response:
    """下載同源候選 PNG。"""

    image = await service.get_candidate_image(run_id, candidate_id)
    return _image_response(
        image.content,
        filename=f"storyboard-{candidate_id}.png",
    )


@router.get("/storyboards/{run_id}/upscale/image")
async def get_upscale_image(
    run_id: RunId,
    service: WorkflowServiceDependency,
) -> Response:
    """提供同源 3840×2160 PNG 預覽。"""

    image = await service.get_upscale_image(run_id)
    return _image_response(image.content)


@router.get("/storyboards/{run_id}/upscale/download")
async def download_upscale_image(
    run_id: RunId,
    service: WorkflowServiceDependency,
) -> Response:
    """下載同源 4K PNG。"""

    image = await service.get_upscale_image(run_id)
    return _image_response(
        image.content,
        filename=f"storyboard-{run_id}-4k.png",
    )


async def _read_bounded_upload(upload: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise UnsafeImageError("圖片檔案超過安全大小上限。")
        chunks.append(chunk)
    if total == 0:
        raise UnsafeImageError("圖片內容不可為空。")
    return b"".join(chunks)


def _image_response(content: bytes, *, filename: str | None = None) -> Response:
    headers = {"cache-control": "no-store", "x-content-type-options": "nosniff"}
    if filename is not None:
        headers["content-disposition"] = f'attachment; filename="{filename}"'
    return Response(content=content, media_type="image/png", headers=headers)
