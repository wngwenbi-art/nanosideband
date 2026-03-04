"""
nano/image.py
Image compression and handling for NanoSideband.

LXMF stores images as raw bytes in fields[LXMF.FIELD_IMAGE = 6].
The format is not enforced by LXMF — we use JPEG for compatibility
with Sideband, which also stores raw JPEG bytes in that field.

Public API
----------
    compress(path_or_bytes, max_width, quality) -> bytes
    decompress(raw_bytes)                        -> (bytes, width, height, mime)
    fits_in_packet(image_bytes)                  -> bool
    image_to_field(path_or_bytes, cfg)           -> bytes | None
    field_to_display(raw_bytes, cfg)             -> dict | None
"""

import io
import logging
import pathlib
from typing import Union

log = logging.getLogger(__name__)

# ── Optional Pillow import ─────────────────────────────────────────────────────

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    log.warning("Pillow not installed — image support disabled. "
                "Install with: pip install pillow")

# ── Constants ─────────────────────────────────────────────────────────────────

# LXMF single-packet limit for content (conservative — leaves room for text)
# LXMessage.LINK_PACKET_MAX_CONTENT = 431 - 112 = 319 bytes
# For images we always use RESOURCE delivery, so practical limit is ~500 KB
# Sideband default: max_width=800, quality=70
DEFAULT_MAX_WIDTH  = 800
DEFAULT_QUALITY    = 70
SIDEBAND_MAX_BYTES = 512 * 1024   # 512 KB hard ceiling before compression
WARNING_BYTES      = 100 * 1024   # warn if compressed result > 100 KB

# JPEG magic bytes
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC  = b"\x89PNG"
GIF_MAGIC  = b"GIF8"
WEBP_MAGIC = b"RIFF"


def _detect_mime(data: bytes) -> str:
    if data[:3] == JPEG_MAGIC:
        return "image/jpeg"
    if data[:4] == PNG_MAGIC:
        return "image/png"
    if data[:4] == GIF_MAGIC:
        return "image/gif"
    if data[:4] == WEBP_MAGIC and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


# ── Core functions ─────────────────────────────────────────────────────────────

def compress(
    source: Union[str, pathlib.Path, bytes],
    max_width: int  = DEFAULT_MAX_WIDTH,
    quality: int    = DEFAULT_QUALITY,
) -> bytes:
    """
    Load an image from a file path or raw bytes, resize if wider than
    max_width, and return JPEG-compressed bytes.

    Raises RuntimeError if Pillow is not installed.
    Raises ValueError if source cannot be decoded as an image.
    """
    if not _PIL_AVAILABLE:
        raise RuntimeError(
            "Pillow is required for image support. "
            "Install with: pip install pillow"
        )

    # Load
    try:
        if isinstance(source, (str, pathlib.Path)):
            img = _PILImage.open(str(source))
        else:
            img = _PILImage.open(io.BytesIO(source))
    except Exception as exc:
        raise ValueError(f"Cannot decode image: {exc}") from exc

    # Convert to RGB (JPEG doesn't support alpha/palette)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Resize if needed (preserve aspect ratio)
    w, h = img.size
    if w > max_width:
        new_h = int(h * max_width / w)
        img = img.resize((max_width, new_h), _PILImage.LANCZOS)
        log.debug("Resized image %dx%d -> %dx%d", w, h, max_width, new_h)

    # Compress
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    result = buf.getvalue()

    original_size = len(source) if isinstance(source, bytes) else pathlib.Path(source).stat().st_size
    log.debug(
        "Compressed image: %d bytes -> %d bytes (%.1f%%)",
        original_size, len(result), 100 * len(result) / max(original_size, 1)
    )

    if len(result) > WARNING_BYTES:
        log.warning(
            "Compressed image is %d KB — consider lower quality or max_width.",
            len(result) // 1024
        )

    return result


def decompress(raw_bytes: bytes):
    """
    Decode raw image bytes (as stored in LXMF fields[FIELD_IMAGE]).

    Returns (bytes, width, height, mime_type).
    If Pillow is not available, returns (raw_bytes, 0, 0, detected_mime).
    If decoding fails, returns (raw_bytes, 0, 0, 'application/octet-stream').
    """
    mime = _detect_mime(raw_bytes)

    if not _PIL_AVAILABLE:
        return raw_bytes, 0, 0, mime

    try:
        img = _PILImage.open(io.BytesIO(raw_bytes))
        w, h = img.size
        # Re-encode as JPEG for consistent display
        if mime != "image/jpeg":
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return buf.getvalue(), w, h, "image/jpeg"
        return raw_bytes, w, h, mime
    except Exception as exc:
        log.warning("Failed to decompress image: %s", exc)
        return raw_bytes, 0, 0, mime


def fits_in_packet(image_bytes: bytes) -> bool:
    """
    Return True if image_bytes is small enough to send as a single LXMF
    packet (opportunistic). Large images always require a link/resource.

    Practical threshold: 180 bytes (tiny thumbnails only).
    For real use, images always go as RESOURCE/DIRECT.
    """
    return len(image_bytes) < 180


def image_to_field(
    source: Union[str, pathlib.Path, bytes, None],
    max_width: int = DEFAULT_MAX_WIDTH,
    quality: int   = DEFAULT_QUALITY,
) -> bytes | None:
    """
    Compress an image and return the bytes to put in fields[LXMF.FIELD_IMAGE].

    Returns None if source is None or if compression fails.
    This is the single call-site you use before building an LXMessage.

    Example:
        img_data = image_to_field("photo.jpg", cfg["image_max_width"], cfg["image_quality"])
        if img_data:
            fields[LXMF.FIELD_IMAGE] = img_data
    """
    if source is None:
        return None

    try:
        return compress(source, max_width=max_width, quality=quality)
    except RuntimeError as exc:
        log.error("Image compression unavailable: %s", exc)
        return None
    except (ValueError, OSError) as exc:
        log.error("Image compression failed: %s", exc)
        return None


def field_to_display(raw_bytes: bytes | None) -> dict | None:
    """
    Convert raw bytes from fields[LXMF.FIELD_IMAGE] into a display dict.

    Returns:
        {
            "data":      bytes,     # JPEG bytes ready to display
            "width":     int,
            "height":    int,
            "mime_type": str,
            "size_kb":   float,
        }
    or None if raw_bytes is None/empty.
    """
    if not raw_bytes:
        return None

    data, w, h, mime = decompress(raw_bytes)
    return {
        "data":      data,
        "width":     w,
        "height":    h,
        "mime_type": mime,
        "size_kb":   round(len(data) / 1024, 1),
    }


def image_dimensions(source: Union[str, pathlib.Path, bytes]) -> tuple[int, int]:
    """
    Return (width, height) of an image without full compression.
    Returns (0, 0) if Pillow unavailable or image unreadable.
    """
    if not _PIL_AVAILABLE:
        return 0, 0
    try:
        if isinstance(source, (str, pathlib.Path)):
            img = _PILImage.open(str(source))
        else:
            img = _PILImage.open(io.BytesIO(source))
        return img.size
    except Exception:
        return 0, 0
