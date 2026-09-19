"""Manifest of every decision pass 1 (and later pass 2 / pass 3) makes.

One row per source photo, whether it survived or not, so a human can
audit *why* something got cut without having to re-run the pipeline.

New optional columns (like the pass2_*/pass3_* pairs below) are additive
and default to None, so old manifest.json files still load fine, and code
that doesn't know about a newer column just leaves it untouched.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

STATUS_KEPT = "kept"
STATUS_DUPLICATE = "duplicate"
STATUS_BLURRY = "blurry"
STATUS_SCREENSHOT = "screenshot"
STATUS_JUNK = "junk"
STATUS_ERROR = "error"


@dataclass
class PhotoRecord:
    original_path: str
    status: str
    reason: str = ""
    cluster_id: Optional[str] = None
    chapter_label: Optional[str] = None
    output_path: Optional[str] = None
    datetime: Optional[str] = None
    datetime_source: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    blur_score: Optional[float] = None
    phash: Optional[str] = None
    duplicate_of: Optional[str] = None
    pass2_selected: Optional[bool] = None
    pass2_notes: Optional[str] = None
    pass3_selected: Optional[bool] = None
    pass3_notes: Optional[str] = None


_FIELDNAMES = list(PhotoRecord.__dataclass_fields__.keys())


class Manifest:
    def __init__(self):
        self.records: list[PhotoRecord] = []

    def add(self, record: PhotoRecord) -> None:
        self.records.append(record)

    def write_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            writer.writeheader()
            for record in self.records:
                writer.writerow(asdict(record))

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in self.records], f, indent=2, default=str)

    @classmethod
    def read_json(cls, path: Path) -> "Manifest":
        manifest = cls()
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        for row in rows:
            manifest.add(PhotoRecord(**row))
        return manifest

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.status] = counts.get(record.status, 0) + 1
        return counts
