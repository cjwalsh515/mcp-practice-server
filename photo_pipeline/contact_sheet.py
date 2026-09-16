"""Thumbnail-grid contact sheets, one per output folder, for fast visual approval."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from . import heic_support  # noqa: F401

DEFAULT_COLUMNS = 5
DEFAULT_THUMB_SIZE = (220, 220)
DEFAULT_LABEL_HEIGHT = 22
DEFAULT_PADDING = 8
MAX_FILENAME_CHARS = 28


def _load_font():
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 12)
    except OSError:
        return ImageFont.load_default()


def _truncate(name: str, limit: int = MAX_FILENAME_CHARS) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def generate_contact_sheet(
    image_paths: Sequence[Path],
    output_path: Path,
    *,
    columns: int = DEFAULT_COLUMNS,
    thumb_size: tuple[int, int] = DEFAULT_THUMB_SIZE,
    label_height: int = DEFAULT_LABEL_HEIGHT,
    padding: int = DEFAULT_PADDING,
) -> Path:
    if not image_paths:
        raise ValueError("generate_contact_sheet requires at least one image")

    thumb_w, thumb_h = thumb_size
    cell_w, cell_h = thumb_w + padding, thumb_h + label_height + padding
    rows = (len(image_paths) + columns - 1) // columns

    sheet = Image.new("RGB", (cell_w * columns + padding, cell_h * rows + padding), "white")
    draw = ImageDraw.Draw(sheet)
    font = _load_font()

    for index, path in enumerate(image_paths):
        row, col = divmod(index, columns)
        cell_x = padding + col * cell_w
        cell_y = padding + row * cell_h

        try:
            with Image.open(path) as img:
                img = img.convert("RGB")
                img.thumbnail(thumb_size)
                paste_x = cell_x + (thumb_w - img.width) // 2
                paste_y = cell_y + (thumb_h - img.height) // 2
                sheet.paste(img, (paste_x, paste_y))
        except Exception:  # noqa: BLE001 - keep building the sheet even if one file fails
            draw.rectangle(
                [cell_x, cell_y, cell_x + thumb_w, cell_y + thumb_h],
                outline="red",
            )
            draw.text((cell_x + 6, cell_y + thumb_h // 2), "unreadable", fill="red", font=font)

        label = _truncate(path.name)
        draw.text((cell_x, cell_y + thumb_h + 2), label, fill="black", font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=85)
    return output_path
