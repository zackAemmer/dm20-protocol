"""
Party Mode image channel.

The DM attaches a picture (usually a small pixel-art PNG) to a narrative.
The file is copied into ``{campaign_dir}/party/images/`` under a generated
name and served to players at ``/img/<name>?token=...``. Only the generated
names are ever served, so nothing else in the campaign folder is reachable.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
import uuid
from pathlib import Path
from typing import Any, Optional

MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Generated names only: img_<hex>.<ext>
SAFE_NAME = re.compile(r"^img_[0-9a-f]{12}\.(png|jpg|gif|webp)$")

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def images_dir(campaign_dir: Path) -> Path:
    return campaign_dir / "party" / "images"


def _sniff(data: bytes) -> Optional[str]:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _png_size(data: bytes) -> tuple[Optional[int], Optional[int]]:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    return None, None


def store_image(
    campaign_dir: Path,
    image_path: Optional[str] = None,
    image_base64: Optional[str] = None,
    caption: Optional[str] = None,
) -> dict[str, Any]:
    """Copy an image into the party image folder and describe it.

    Exactly one of ``image_path`` (absolute, or relative to the campaign
    folder) or ``image_base64`` (raw or a ``data:`` URL) is used.

    Returns:
        ``{"file", "caption", "width", "height"}`` for the response record.

    Raises:
        ValueError: with a message fit to show the DM.
    """
    if image_path and image_base64:
        raise ValueError("give image_path or image_base64, not both")
    if image_path:
        src = Path(image_path).expanduser()
        if not src.is_absolute():
            src = campaign_dir / src
        if not src.is_file():
            raise ValueError(f"image not found: {image_path}")
        if src.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("image is larger than 5 MB")
        data = src.read_bytes()
    elif image_base64:
        raw = image_base64.strip()
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("image_base64 is not valid base64")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image is larger than 5 MB")
    else:
        raise ValueError("no image given")

    ext = _sniff(data)
    if not ext:
        raise ValueError("not a PNG, JPEG, GIF or WebP image")

    folder = images_dir(campaign_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"img_{uuid.uuid4().hex[:12]}.{ext}"
    (folder / name).write_bytes(data)

    width, height = _png_size(data)
    info: dict[str, Any] = {"file": name, "caption": (caption or "").strip()}
    if width and height:
        info["width"] = width
        info["height"] = height
    return info


def resolve_image(campaign_dir: Path, name: str) -> Optional[Path]:
    """Path of a stored image, or None if the name is not one we generated."""
    if not SAFE_NAME.match(name or ""):
        return None
    path = images_dir(campaign_dir) / name
    return path if path.is_file() else None
