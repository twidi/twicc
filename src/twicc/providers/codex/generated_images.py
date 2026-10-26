"""Inline a Codex generated image whose bytes are no longer in the rollout.

Today Codex writes the generated PNG as base64 in the item's ``result``. A
future attachment store may keep it as a file reference instead (``fileId``,
empty or absent ``result``), while ``savedPath`` still points at the PNG Codex
saved under ``<codex home>/generated_images/``. The frontend only renders
``result``, so ingestion fills it from that file.

``savedPath`` comes from a rollout file: it is untrusted. Only a regular PNG
whose real path (symlinks and ``..`` resolved) lies inside the generated images
directory is ever read.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from twicc.provider_homes import codex_home

from .canonical import completed_item, image_generation

logger = logging.getLogger(__name__)

GENERATED_IMAGES_DIRNAME = "generated_images"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# A generated image is a 1-2 MB PNG; the rollout already stored it as base64
# without any limit. 16 MiB leaves ample room for larger renders while keeping
# a bogus path from loading an arbitrarily large file into the database.
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def _read_allowed_png(saved_path: str) -> bytes | None:
    """Return the PNG bytes at ``saved_path`` if it passes every check, else ``None``."""
    try:
        root = (codex_home().path / GENERATED_IMAGES_DIRNAME).resolve(strict=True)
        path = Path(saved_path).resolve(strict=True)
        if not path.is_relative_to(root):
            logger.warning("Ignoring generated image outside %s: %s", root, saved_path)
            return None
        # O_NOFOLLOW guards the window between the resolve and the open.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            if os.fstat(handle.fileno()).st_size > MAX_IMAGE_BYTES:
                logger.warning("Ignoring oversized generated image: %s", saved_path)
                return None
            data = handle.read(MAX_IMAGE_BYTES + 1)
    except (OSError, ValueError):
        return None
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(PNG_SIGNATURE):
        return None
    return data


def inline_generated_image(parsed_json: dict) -> bool:
    """Fill an empty ``result`` of an image generation item from its saved file, in place.

    Returns ``True`` when the item changed. An item with a non-empty ``result``
    is never touched, and an unreadable or rejected file leaves the item as is.
    """
    image = image_generation(parsed_json)
    if image is None or image.result or not image.saved_path or not isinstance(image.saved_path, str):
        return False
    item = completed_item(parsed_json)
    if item is None:
        return False
    data = _read_allowed_png(image.saved_path)
    if data is None:
        return False
    item["result"] = base64.b64encode(data).decode("ascii")
    return True
