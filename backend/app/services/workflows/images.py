"""不信任圖片的 bounded decode 與 canonical PNG 正規化。"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.workflow_settings import WorkflowSettings

_ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
_FORMAT_BY_CONTENT_TYPE = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}


@dataclass(frozen=True, slots=True)
class NormalizedImage:
    """已解碼、去 metadata 且限制大小的 RGB PNG。"""

    content: bytes
    width: int
    height: int


class UnsafeImageError(ValueError):
    """上傳或 ComfyUI output 不是可接受的靜態圖片。"""


def normalize_uploaded_image(
    raw: bytes,
    *,
    content_type: str,
    settings: WorkflowSettings,
) -> NormalizedImage:
    """驗證 browser MIME 後，以 Pillow 重新編碼成無 metadata PNG。"""

    if content_type.lower() not in _ALLOWED_CONTENT_TYPES:
        raise UnsafeImageError("只接受 PNG、JPEG 或 WebP 圖片。")
    return _normalize_image(
        raw,
        settings=settings,
        expected_format=_FORMAT_BY_CONTENT_TYPE[content_type.lower()],
    )


def normalize_generated_image(
    raw: bytes,
    *,
    settings: WorkflowSettings,
) -> NormalizedImage:
    """驗證 ComfyUI output 並移除內嵌 graph／本機資訊。"""

    return _normalize_image(raw, settings=settings, expected_format=None)


def _normalize_image(
    raw: bytes,
    *,
    settings: WorkflowSettings,
    expected_format: str | None,
) -> NormalizedImage:
    if not raw:
        raise UnsafeImageError("圖片內容不可為空。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in _ALLOWED_FORMATS:
                    raise UnsafeImageError("圖片格式不受支援。")
                if expected_format is not None and source.format != expected_format:
                    raise UnsafeImageError("圖片內容與宣告格式不一致。")
                width, height = source.size
                if (
                    width < 1
                    or height < 1
                    or width > settings.max_image_dimension
                    or height > settings.max_image_dimension
                    or width * height > settings.max_image_pixels
                ):
                    raise UnsafeImageError("圖片尺寸超過安全上限。")
                if bool(getattr(source, "is_animated", False)):
                    raise UnsafeImageError("不接受動態圖片。")
                source.load()
                transposed = ImageOps.exif_transpose(source)
                if "A" in transposed.getbands():
                    rgba = transposed.convert("RGBA")
                    background = Image.new("RGB", rgba.size, (240, 240, 240))
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    normalized = background
                else:
                    normalized = transposed.convert("RGB")
                output = BytesIO()
                normalized.save(output, format="PNG", compress_level=4)
                return NormalizedImage(
                    content=output.getvalue(),
                    width=normalized.width,
                    height=normalized.height,
                )
    except UnsafeImageError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise UnsafeImageError("圖片內容損壞或無法安全解碼。") from exc
