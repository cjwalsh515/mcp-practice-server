"""Finds candidate image files and buckets them by year for manageable batches."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

from .exif_utils import PhotoMetadata, read_metadata

DEFAULT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"}

UNKNOWN_YEAR = "unknown"


def find_images(root: Path, *, extensions: set[str] = DEFAULT_EXTENSIONS) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith(".")
    )


def bucket_by_year(
    paths: list[Path], *, years: Optional[set[int]] = None
) -> dict[str, list[PhotoMetadata]]:
    """Reads metadata for every path and groups by year, so pass1 can process
    one year at a time rather than the whole library in a single sweep.
    """
    buckets: dict[str, list[PhotoMetadata]] = defaultdict(list)
    for path in paths:
        meta = read_metadata(path)
        if meta.datetime is not None:
            year = meta.datetime.year
            if years is not None and year not in years:
                continue
            buckets[str(year)].append(meta)
        else:
            if years is not None:
                continue
            buckets[UNKNOWN_YEAR].append(meta)
    return dict(sorted(buckets.items()))
