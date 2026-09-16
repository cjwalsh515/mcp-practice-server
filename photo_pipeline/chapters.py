"""Optional chapter-date-range mapping.

Chapters aren't locked in yet, so this is entirely optional: with no
config, clusters are grouped into folders by year/month instead. Once a
chapter list firms up, point pass1 at a YAML file like:

    chapters:
      - name: "How We Met"
        start: "2007-01-01"
        end: "2009-12-31"
      - name: "Dating"
        start: "2010-01-01"
        end: "2012-06-30"

Ranges are inclusive and matched against a photo's date only (no time-of-day
edge cases to worry about). Overlapping ranges resolve to the first match in
file order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import yaml

from .clustering import Cluster

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


@dataclass
class ChapterRange:
    name: str
    start: date
    end: date

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def _parse_date(value: Union[str, date]) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def load_chapters(path: Path) -> list[ChapterRange]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    entries = data.get("chapters", [])
    chapters = []
    for entry in entries:
        chapters.append(
            ChapterRange(
                name=entry["name"],
                start=_parse_date(entry["start"]),
                end=_parse_date(entry["end"]),
            )
        )
    return chapters


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip()).strip("-").lower()
    return slug or "chapter"


def assign_chapter(d: date, chapters: list[ChapterRange]) -> Optional[str]:
    for chapter in chapters:
        if chapter.contains(d):
            return chapter.name
    return None


def label_for_cluster(cluster: Cluster, chapters: Optional[list[ChapterRange]]) -> str:
    """Picks the output folder name for a cluster.

    Falls back through: matching chapter -> year-month bucket -> the raw
    cluster id (covers the "undated" cluster).
    """
    if cluster.start is None:
        return cluster.cluster_id

    if chapters:
        chapter_name = assign_chapter(cluster.start.date(), chapters)
        if chapter_name:
            return slugify(chapter_name)

    return cluster.start.strftime("%Y-%m")
