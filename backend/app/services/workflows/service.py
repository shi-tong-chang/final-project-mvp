"""Storyboard 合成、候選選片與 4K 放大的 application service。"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.workflow_settings import WorkflowSettings
from app.schemas.api.workflows import (
    StoryboardCreateSpec,
    StoryboardFromLibraryCreateSpec,
    StoryboardRunResponse,
    WorkflowCandidateResponse,
    WorkflowCandidateStatus,
    WorkflowRoute,
    WorkflowRunStatus,
    WorkflowStageSeeds,
    WorkflowStatusResponse,
    WorkflowUpscaleResponse,
    WorkflowUpscaleStatus,
)
from app.services.workflows.adapters import (
    StoryboardWorkflowAdapter,
    WorkflowAdapterError,
)
from app.services.workflows.client import (
    ComfyUIClient,
    ComfyUIClientError,
    WorkflowGraph,
)
from app.services.workflows.images import (
    NormalizedImage,
    UnsafeImageError,
    normalize_generated_image,
)

_MAX_SAFE_SEED = 9_007_199_254_740_991


class WorkflowServiceError(RuntimeError):
    """供 HTTP layer 映射的穩定安全錯誤。"""

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(slots=True)
class _CandidateRecord:
    candidate_id: str
    seed: int
    b2_seed: int | None = None
    status: WorkflowCandidateStatus = WorkflowCandidateStatus.QUEUED
    image: NormalizedImage | None = None
    error: str | None = None


@dataclass(slots=True)
class _UpscaleRecord:
    status: WorkflowUpscaleStatus = WorkflowUpscaleStatus.IDLE
    refine_prompt: str | None = None
    seed: int | None = None
    image: NormalizedImage | None = None
    error: str | None = None


@dataclass(slots=True)
class _RunRecord:
    run_id: str
    prompt: str
    workflow_route: WorkflowRoute
    scene_name: str
    character_names: tuple[str, ...]
    scene_image: NormalizedImage | None
    character_images: list[NormalizedImage]
    candidates: list[_CandidateRecord]
    status: WorkflowRunStatus = WorkflowRunStatus.QUEUED
    selected_candidate_id: str | None = None
    upscale: _UpscaleRecord = field(default_factory=_UpscaleRecord)


class _WorkKind(StrEnum):
    COMPOSE = "compose"
    UPSCALE = "upscale"


@dataclass(frozen=True, slots=True)
class _WorkItem:
    kind: _WorkKind
    run_id: str


class StoryboardWorkflowService:
    """以單一 worker 序列化本機 GPU 工作並保存本 process authority。"""

    def __init__(
        self,
        client: ComfyUIClient,
        settings: WorkflowSettings,
        *,
        adapter: StoryboardWorkflowAdapter | None = None,
        seed_factory: Callable[[], int] | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._adapter = adapter or StoryboardWorkflowAdapter(settings)
        self._seed_factory = seed_factory or (
            lambda: secrets.randbelow(_MAX_SAFE_SEED + 1)
        )
        self._runs: dict[str, _RunRecord] = {}
        self._queue: asyncio.Queue[_WorkItem] = asyncio.Queue(
            maxsize=settings.max_queue_size
        )
        self._lock = asyncio.Lock()
        self._worker_task: asyncio.Task[None] | None = None
        self._active_prompt_id: str | None = None
        self._retained_bytes = 0
        self._closed = False

    async def start(self) -> None:
        """啟動 service-owned worker；不連線或啟動 ComfyUI。"""

        if self._closed:
            raise RuntimeError("workflow service 已關閉")
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(
                self._worker_loop(),
                name="storyboard-workflow-worker",
            )

    @property
    def settings(self) -> WorkflowSettings:
        """提供 route 的唯讀圖片限制設定。"""

        return self._settings

    @property
    def max_upload_bytes(self) -> int:
        """提供 multipart streaming 的 byte 上限。"""

        return self._settings.max_upload_bytes

    async def status(self) -> WorkflowStatusResponse:
        """回傳不含路徑、模型或硬體細節的 ComfyUI 狀態。"""

        status = await self._client.status()
        return WorkflowStatusResponse(
            status="ready" if status.available else "unavailable",
            available=status.available,
            detail=status.detail,
        )

    async def create_run(
        self,
        spec: StoryboardCreateSpec,
        *,
        scene_image: NormalizedImage,
        character_image: NormalizedImage,
    ) -> StoryboardRunResponse:
        """建立 1–3 張候選的非同步合成工作。"""

        return await self._create_run(
            prompt=spec.prompt,
            candidate_count=spec.candidate_count,
            scene_image=scene_image,
            character_images=(character_image,),
            scene_name="使用者上傳的場景",
            character_names=("使用者上傳的角色一",),
        )

    async def create_library_run(
        self,
        spec: StoryboardFromLibraryCreateSpec,
        *,
        scene_image: NormalizedImage,
        character_images: tuple[NormalizedImage, ...],
        scene_name: str,
        character_names: tuple[str, ...],
    ) -> StoryboardRunResponse:
        """以 server-resolved 本機素材建立一或兩角色分鏡。"""

        return await self._create_run(
            prompt=spec.prompt,
            candidate_count=spec.candidate_count,
            scene_image=scene_image,
            character_images=character_images,
            scene_name=scene_name,
            character_names=character_names,
        )

    async def _create_run(
        self,
        *,
        prompt: str,
        candidate_count: int,
        scene_image: NormalizedImage,
        character_images: tuple[NormalizedImage, ...],
        scene_name: str,
        character_names: tuple[str, ...],
    ) -> StoryboardRunResponse:
        if len(character_images) not in {1, 2} or len(character_names) != len(
            character_images
        ):
            raise WorkflowServiceError(
                "WORKFLOW_INVALID_ASSET_SELECTION",
                "分鏡必須精確使用一或兩個角色素材。",
                status_code=422,
            )

        if self._closed:
            raise WorkflowServiceError(
                "WORKFLOW_SERVICE_CLOSED",
                "圖片工作服務正在關閉。",
                status_code=503,
            )
        await self._require_available()
        run_id = f"run_{uuid.uuid4().hex}"
        workflow_route = (
            WorkflowRoute.SINGLE_CHARACTER_B1
            if len(character_images) == 1
            else WorkflowRoute.DUAL_CHARACTER_B1_B2
        )
        stage_count = 1 if workflow_route is WorkflowRoute.SINGLE_CHARACTER_B1 else 2
        seeds = self._unique_seeds(candidate_count * stage_count)
        candidates = [
            _CandidateRecord(
                candidate_id=f"cand_{uuid.uuid4().hex}",
                seed=seeds[index],
                b2_seed=(
                    seeds[candidate_count + index]
                    if workflow_route is WorkflowRoute.DUAL_CHARACTER_B1_B2
                    else None
                ),
            )
            for index in range(candidate_count)
        ]
        run = _RunRecord(
            run_id=run_id,
            prompt=prompt.strip(),
            workflow_route=workflow_route,
            scene_name=scene_name,
            character_names=character_names,
            scene_image=scene_image,
            character_images=list(character_images),
            candidates=candidates,
        )
        async with self._lock:
            self._ensure_run_and_queue_capacity_locked()
            input_bytes = len(scene_image.content) + sum(
                len(image.content) for image in character_images
            )
            self._ensure_retained_capacity_locked(input_bytes)
            self._runs[run_id] = run
            self._retained_bytes += input_bytes
            self._queue.put_nowait(_WorkItem(_WorkKind.COMPOSE, run_id))
            response = self._snapshot(run)
        return response

    async def get_run(self, run_id: str) -> StoryboardRunResponse:
        """取得本 process 核發 run 的一致快照。"""

        async with self._lock:
            return self._snapshot(self._get_run(run_id))

    async def select_candidate(
        self,
        run_id: str,
        candidate_id: str,
    ) -> StoryboardRunResponse:
        """只允許選擇已完成且屬於該 run 的候選。"""

        async with self._lock:
            run = self._get_run(run_id)
            candidate = self._find_candidate(run, candidate_id)
            if run.upscale.status in {
                WorkflowUpscaleStatus.QUEUED,
                WorkflowUpscaleStatus.RUNNING,
                WorkflowUpscaleStatus.COMPLETED,
            }:
                if candidate_id == run.selected_candidate_id:
                    return self._snapshot(run)
                raise WorkflowServiceError(
                    "WORKFLOW_SELECTION_LOCKED",
                    "4K 工作已建立，不能更換來源候選。",
                    status_code=409,
                )
            if run.status not in {
                WorkflowRunStatus.AWAITING_SELECTION,
                WorkflowRunStatus.COMPLETED,
            }:
                raise WorkflowServiceError(
                    "WORKFLOW_SELECTION_NOT_READY",
                    "候選圖片尚未完成，現在不能選定。",
                    status_code=409,
                )
            if candidate.status is not WorkflowCandidateStatus.COMPLETED:
                raise WorkflowServiceError(
                    "WORKFLOW_CANDIDATE_NOT_READY",
                    "這張候選圖片尚未完成。",
                    status_code=409,
                )
            run.selected_candidate_id = candidate_id
            run.status = WorkflowRunStatus.COMPLETED
            run.upscale = _UpscaleRecord()
            return self._snapshot(run)

    async def queue_upscale(
        self,
        run_id: str,
        refine_prompt: str,
        expected_candidate_id: str,
    ) -> StoryboardRunResponse:
        """只有 completed 且已選片的 run 能進入 4K queue。"""

        if self._closed:
            raise WorkflowServiceError(
                "WORKFLOW_SERVICE_CLOSED",
                "圖片工作服務正在關閉。",
                status_code=503,
            )
        await self._require_available()
        async with self._lock:
            run = self._get_run(run_id)
            if (
                run.status is not WorkflowRunStatus.COMPLETED
                or run.selected_candidate_id is None
            ):
                raise WorkflowServiceError(
                    "WORKFLOW_SELECTION_REQUIRED",
                    "請先完成候選生成並選定一張分鏡圖。",
                    status_code=409,
                )
            if run.selected_candidate_id != expected_candidate_id:
                raise WorkflowServiceError(
                    "WORKFLOW_SELECTION_CHANGED",
                    "已選候選與建立 4K 工作時的預期不一致。",
                    status_code=409,
                )
            if run.upscale.status not in {
                WorkflowUpscaleStatus.IDLE,
                WorkflowUpscaleStatus.FAILED,
            }:
                raise WorkflowServiceError(
                    "WORKFLOW_UPSCALE_ALREADY_QUEUED",
                    "這張候選已建立 4K 工作，不能重複排程。",
                    status_code=409,
                )
            self._ensure_queue_capacity_locked()
            run.upscale = _UpscaleRecord(
                status=WorkflowUpscaleStatus.QUEUED,
                refine_prompt=refine_prompt.strip(),
                seed=self._next_seed(),
            )
            run.status = WorkflowRunStatus.UPSCALING
            self._queue.put_nowait(_WorkItem(_WorkKind.UPSCALE, run_id))
            response = self._snapshot(run)
        return response

    async def get_candidate_image(
        self,
        run_id: str,
        candidate_id: str,
    ) -> NormalizedImage:
        """取得已完成候選，不接受檔案路徑。"""

        async with self._lock:
            candidate = self._find_candidate(self._get_run(run_id), candidate_id)
            if (
                candidate.status is not WorkflowCandidateStatus.COMPLETED
                or candidate.image is None
            ):
                raise WorkflowServiceError(
                    "WORKFLOW_IMAGE_NOT_FOUND",
                    "找不到這張已完成的候選圖片。",
                    status_code=404,
                )
            return candidate.image

    async def get_upscale_image(self, run_id: str) -> NormalizedImage:
        """取得已完成 4K 圖片。"""

        async with self._lock:
            run = self._get_run(run_id)
            if (
                run.upscale.status is not WorkflowUpscaleStatus.COMPLETED
                or run.upscale.image is None
            ):
                raise WorkflowServiceError(
                    "WORKFLOW_IMAGE_NOT_FOUND",
                    "找不到已完成的 4K 圖片。",
                    status_code=404,
                )
            return run.upscale.image

    async def close(self) -> None:
        """取消並 await worker，再關閉 client-owned HTTP 資源。"""

        if self._closed:
            return
        self._closed = True
        active_prompt_id = self._active_prompt_id
        worker = self._worker_task
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        if active_prompt_id is not None:
            with contextlib.suppress(ComfyUIClientError):
                await self._client.cancel_prompt(active_prompt_id)
        await self._client.close()

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item.kind is _WorkKind.COMPOSE:
                    await self._process_composition(item.run_id)
                else:
                    await self._process_upscale(item.run_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._mark_unexpected_failure(item)
            finally:
                self._queue.task_done()

    async def _process_composition(self, run_id: str) -> None:
        async with self._lock:
            run = self._get_run(run_id)
            run.status = WorkflowRunStatus.RUNNING
            expected_character_count = (
                1 if run.workflow_route is WorkflowRoute.SINGLE_CHARACTER_B1 else 2
            )
            if (
                run.scene_image is None
                or len(run.character_images) != expected_character_count
                or len(run.character_names) != expected_character_count
            ):
                raise RuntimeError("合成工作缺少輸入圖片")
            scene_bytes = run.scene_image.content
            character_bytes = tuple(image.content for image in run.character_images)
        subfolder = f"final-project-mvp/{run_id}"
        try:
            scene_ref = await self._client.upload_image(
                f"{run_id}-scene.png",
                scene_bytes,
                subfolder=subfolder,
            )
            first_character_ref = await self._client.upload_image(
                f"{run_id}-character.png",
                character_bytes[0],
                subfolder=subfolder,
            )
            second_character_ref = (
                await self._client.upload_image(
                    f"{run_id}-character-2.png",
                    character_bytes[1],
                    subfolder=subfolder,
                )
                if expected_character_count == 2
                else None
            )
        except ComfyUIClientError as exc:
            await self._fail_all_candidates(run_id, exc.message)
            return
        finally:
            async with self._lock:
                self._release_input_images_locked(run)

        for candidate in run.candidates:
            async with self._lock:
                candidate.status = WorkflowCandidateStatus.RUNNING
            try:
                graph = self._adapter.build_composition(
                    scene_image=scene_ref.load_image_value,
                    character_image=first_character_ref.load_image_value,
                    prompt=run.prompt,
                    seed=candidate.seed,
                    output_prefix=(
                        f"final-project-mvp/{run_id}/b1/{candidate.candidate_id}"
                    ),
                    scene_name=run.scene_name,
                    character_name=run.character_names[0],
                )
                normalized = await self._execute_composition_graph(
                    graph,
                    output_node_id="9",
                )
                if run.workflow_route is WorkflowRoute.DUAL_CHARACTER_B1_B2:
                    if second_character_ref is None or candidate.b2_seed is None:
                        raise RuntimeError("雙角色工作缺少 B2 設定")
                    intermediate_ref = await self._client.upload_image(
                        f"{run_id}-{candidate.candidate_id}-b1.png",
                        normalized.content,
                        subfolder=subfolder,
                    )
                    second_graph = self._adapter.build_second_composition(
                        intermediate_image=intermediate_ref.load_image_value,
                        second_character_image=second_character_ref.load_image_value,
                        prompt=run.prompt,
                        seed=candidate.b2_seed,
                        output_prefix=(
                            f"final-project-mvp/{run_id}/b2/{candidate.candidate_id}"
                        ),
                        scene_name=run.scene_name,
                        first_character_name=run.character_names[0],
                        second_character_name=run.character_names[1],
                    )
                    normalized = await self._execute_composition_graph(
                        second_graph,
                        output_node_id="9",
                    )
            except (ComfyUIClientError, UnsafeImageError, WorkflowAdapterError) as exc:
                await self._fail_candidate(candidate, self._safe_error(exc))
            else:
                async with self._lock:
                    if self._can_retain_locked(len(normalized.content)):
                        self._retained_bytes += len(normalized.content)
                        candidate.image = normalized
                        candidate.status = WorkflowCandidateStatus.COMPLETED
                        candidate.error = None
                    else:
                        candidate.status = WorkflowCandidateStatus.FAILED
                        candidate.error = "本機圖片保留空間已達安全上限。"

        async with self._lock:
            run.status = (
                WorkflowRunStatus.AWAITING_SELECTION
                if any(
                    item.status is WorkflowCandidateStatus.COMPLETED
                    for item in run.candidates
                )
                else WorkflowRunStatus.FAILED
            )

    async def _execute_composition_graph(
        self,
        graph: WorkflowGraph,
        *,
        output_node_id: str,
    ) -> NormalizedImage:
        """執行單一固定階段，並將 Comfy output 重新正規化。"""

        prompt_id = self._new_prompt_id()
        self._active_prompt_id = prompt_id
        try:
            await self._client.queue_prompt(graph, prompt_id=prompt_id)
            output_ref = await self._client.wait_for_output(
                prompt_id,
                output_node_id=output_node_id,
            )
            output = await self._client.download_image(output_ref)
            return await asyncio.to_thread(
                normalize_generated_image,
                output,
                settings=self._settings,
            )
        except (ComfyUIClientError, UnsafeImageError):
            with contextlib.suppress(ComfyUIClientError):
                await self._client.cancel_prompt(prompt_id)
            raise
        finally:
            if self._active_prompt_id == prompt_id:
                self._active_prompt_id = None

    async def _process_upscale(self, run_id: str) -> None:
        async with self._lock:
            run = self._get_run(run_id)
            selected_id = run.selected_candidate_id
            if selected_id is None:
                return
            selected = self._find_candidate(run, selected_id)
            if selected.image is None:
                return
            source_bytes = selected.image.content
            refine_prompt = run.upscale.refine_prompt
            seed = run.upscale.seed
            run.upscale.status = WorkflowUpscaleStatus.RUNNING
        if refine_prompt is None or seed is None:
            await self._fail_upscale(run_id, "4K 工作缺少必要設定。")
            return

        prompt_id: str | None = None
        try:
            source_ref = await self._client.upload_image(
                f"{run_id}-upscale-source.png",
                source_bytes,
                subfolder=f"final-project-mvp/{run_id}",
            )
            graph = self._adapter.build_upscale(
                source_image=source_ref.load_image_value,
                refine_prompt=refine_prompt,
                seed=seed,
                output_prefix=f"final-project-mvp/{run_id}/upscale/final-4k",
            )
            prompt_id = self._new_prompt_id()
            self._active_prompt_id = prompt_id
            await self._client.queue_prompt(graph, prompt_id=prompt_id)
            output_ref = await self._client.wait_for_output(
                prompt_id,
                output_node_id="26",
            )
            output = await self._client.download_image(output_ref)
            normalized = await asyncio.to_thread(
                normalize_generated_image,
                output,
                settings=self._settings,
            )
            if (normalized.width, normalized.height) != (3_840, 2_160):
                raise UnsafeImageError("4K 工作流沒有輸出 3840×2160 圖片。")
            async with self._lock:
                if not self._can_retain_locked(len(normalized.content)):
                    raise UnsafeImageError("本機圖片保留空間已達安全上限。")
                self._retained_bytes += len(normalized.content)
                run.upscale.image = normalized
                run.upscale.status = WorkflowUpscaleStatus.COMPLETED
                run.upscale.error = None
                run.status = WorkflowRunStatus.COMPLETED
        except (ComfyUIClientError, UnsafeImageError, WorkflowAdapterError) as exc:
            if prompt_id is not None:
                with contextlib.suppress(ComfyUIClientError):
                    await self._client.cancel_prompt(prompt_id)
            await self._fail_upscale(run_id, self._safe_error(exc))
        finally:
            if self._active_prompt_id == prompt_id:
                self._active_prompt_id = None

    async def _fail_candidate(
        self,
        candidate: _CandidateRecord,
        message: str,
    ) -> None:
        async with self._lock:
            candidate.status = WorkflowCandidateStatus.FAILED
            candidate.error = message

    async def _fail_all_candidates(self, run_id: str, message: str) -> None:
        async with self._lock:
            run = self._get_run(run_id)
            for candidate in run.candidates:
                candidate.status = WorkflowCandidateStatus.FAILED
                candidate.error = message
            run.status = WorkflowRunStatus.FAILED

    async def _fail_upscale(self, run_id: str, message: str) -> None:
        async with self._lock:
            run = self._get_run(run_id)
            run.upscale.status = WorkflowUpscaleStatus.FAILED
            run.upscale.error = message
            run.status = WorkflowRunStatus.COMPLETED

    async def _mark_unexpected_failure(self, item: _WorkItem) -> None:
        message = "圖片工作發生非預期錯誤。"
        if item.kind is _WorkKind.COMPOSE:
            await self._fail_all_candidates(item.run_id, message)
        else:
            await self._fail_upscale(item.run_id, message)

    def _snapshot(self, run: _RunRecord) -> StoryboardRunResponse:
        candidate_responses = tuple(
            WorkflowCandidateResponse(
                candidate_id=candidate.candidate_id,
                seed=(
                    candidate.b2_seed
                    if candidate.b2_seed is not None
                    else candidate.seed
                ),
                stage_seeds=WorkflowStageSeeds(
                    b1=candidate.seed,
                    b2=candidate.b2_seed,
                ),
                status=candidate.status,
                image_url=(
                    self._candidate_url(run.run_id, candidate.candidate_id, "image")
                    if candidate.image is not None
                    else None
                ),
                download_url=(
                    self._candidate_url(
                        run.run_id,
                        candidate.candidate_id,
                        "download",
                    )
                    if candidate.image is not None
                    else None
                ),
                error=candidate.error,
            )
            for candidate in run.candidates
        )
        upscale_image_url = (
            f"/api/v1/gateway/workflows/storyboards/{run.run_id}/upscale/image"
            if run.upscale.image is not None
            else None
        )
        upscale_download_url = (
            f"/api/v1/gateway/workflows/storyboards/{run.run_id}/upscale/download"
            if run.upscale.image is not None
            else None
        )
        return StoryboardRunResponse(
            run_id=run.run_id,
            status=run.status,
            workflow_route=run.workflow_route,
            candidates=candidate_responses,
            selected_candidate_id=run.selected_candidate_id,
            upscale=WorkflowUpscaleResponse(
                status=run.upscale.status,
                image_url=upscale_image_url,
                download_url=upscale_download_url,
                error=run.upscale.error,
            ),
        )

    def _get_run(self, run_id: str) -> _RunRecord:
        run = self._runs.get(run_id)
        if run is None:
            raise WorkflowServiceError(
                "WORKFLOW_RUN_NOT_FOUND",
                "找不到這個圖片工作。",
                status_code=404,
            )
        return run

    @staticmethod
    def _find_candidate(
        run: _RunRecord,
        candidate_id: str,
    ) -> _CandidateRecord:
        for candidate in run.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise WorkflowServiceError(
            "WORKFLOW_CANDIDATE_NOT_FOUND",
            "找不到這張候選圖片。",
            status_code=404,
        )

    @staticmethod
    def _candidate_url(run_id: str, candidate_id: str, action: str) -> str:
        return (
            f"/api/v1/gateway/workflows/storyboards/{run_id}/candidates/"
            f"{candidate_id}/{action}"
        )

    def _unique_seeds(self, count: int) -> list[int]:
        values: list[int] = []
        while len(values) < count:
            value = self._next_seed()
            if value not in values:
                values.append(value)
        return values

    async def _require_available(self) -> None:
        status = await self._client.status()
        if not status.available:
            raise WorkflowServiceError(
                "WORKFLOW_UNAVAILABLE",
                "ComfyUI 尚未啟動或必要節點不可用。",
                status_code=503,
            )

    def _ensure_run_and_queue_capacity_locked(self) -> None:
        if len(self._runs) >= self._settings.max_runs:
            raise self._capacity_error()
        self._ensure_queue_capacity_locked()

    def _ensure_queue_capacity_locked(self) -> None:
        if self._queue.full():
            raise self._capacity_error()

    def _ensure_retained_capacity_locked(self, additional_bytes: int) -> None:
        if not self._can_retain_locked(additional_bytes):
            raise self._capacity_error()

    def _can_retain_locked(self, additional_bytes: int) -> bool:
        return (
            additional_bytes >= 0
            and self._retained_bytes + additional_bytes
            <= self._settings.max_retained_image_bytes
        )

    def _release_input_images_locked(self, run: _RunRecord) -> None:
        released = 0
        if run.scene_image is not None:
            released += len(run.scene_image.content)
            run.scene_image = None
        released += sum(len(image.content) for image in run.character_images)
        run.character_images.clear()
        self._retained_bytes = max(0, self._retained_bytes - released)

    @staticmethod
    def _capacity_error() -> WorkflowServiceError:
        return WorkflowServiceError(
            "WORKFLOW_CAPACITY_EXCEEDED",
            "本機圖片工作佇列或保留空間已滿，請稍後再試。",
            status_code=429,
        )

    @staticmethod
    def _new_prompt_id() -> str:
        return f"fpmvp_{uuid.uuid4().hex}"

    def _next_seed(self) -> int:
        value = self._seed_factory()
        if value < 0 or value > _MAX_SAFE_SEED:
            raise RuntimeError("seed_factory 回傳超出安全範圍的值")
        return value

    @staticmethod
    def _safe_error(
        error: ComfyUIClientError | UnsafeImageError | WorkflowAdapterError,
    ) -> str:
        if isinstance(error, ComfyUIClientError):
            return error.message
        if isinstance(error, UnsafeImageError):
            return str(error)
        return "固定圖片工作流與目前版本不相容。"
