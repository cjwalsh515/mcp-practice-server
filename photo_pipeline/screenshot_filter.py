"""Heuristic screenshot / non-camera-photo detection.

No single signal is reliable on its own (some real camera photos lack
EXIF after round-tripping through messaging apps; some screenshots get
saved at unusual sizes). We score a handful of independent signals and
flag anything that clears a threshold, so it's the *combination* that
does the work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .exif_utils import PhotoMetadata

_FILENAME_PATTERN = re.compile(r"screenshot|screen[\s_-]?shot|\bsnip\b|\bcapture\b", re.IGNORECASE)

# Common phone/tablet screen resolutions (either orientation). Not exhaustive —
# this is a "does it look exactly like a device screen" signal, not a lookup table
# of every panel ever shipped.
_KNOWN_SCREEN_RESOLUTIONS = {
    (640, 1136), (750, 1334), (828, 1792), (1080, 1920), (1080, 2160),
    (1080, 2280), (1080, 2340), (1080, 2400), (1125, 2436), (1170, 2532),
    (1179, 2556), (1242, 2208), (1242, 2688), (1284, 2778), (1290, 2796),
    (1320, 2868), (1440, 2560), (1440, 2960), (1440, 3200), (720, 1280),
    (768, 1024), (1536, 2048), (1620, 2160), (2048, 2732), (1668, 2388),
    (2360, 1640), (2732, 2048),
}


@dataclass
class ScreenshotVerdict:
    is_screenshot: bool
    score: int
    reasons: list[str] = field(default_factory=list)


def classify_screenshot(meta: PhotoMetadata, *, threshold: int = 2) -> ScreenshotVerdict:
    score = 0
    reasons: list[str] = []

    if meta.format == "PNG":
        score += 1
        reasons.append("png_format")

    if not meta.has_camera_exif:
        score += 1
        reasons.append("no_camera_exif")

    if _FILENAME_PATTERN.search(meta.path.name):
        score += 2
        reasons.append("filename_pattern")

    if meta.width and meta.height:
        dims = (meta.width, meta.height)
        dims_flipped = (meta.height, meta.width)
        if dims in _KNOWN_SCREEN_RESOLUTIONS or dims_flipped in _KNOWN_SCREEN_RESOLUTIONS:
            score += 2
            reasons.append("known_screen_resolution")

    return ScreenshotVerdict(is_screenshot=score >= threshold, score=score, reasons=reasons)
