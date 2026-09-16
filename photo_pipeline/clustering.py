"""Event/trip clustering by EXIF timestamp (and optionally GPS jumps).

Chapters aren't locked yet, so the default behavior is to auto-cluster by
date gaps and let chapters.py map chapters onto the results afterward, when
a chapter config is supplied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

from .exif_utils import PhotoMetadata

DEFAULT_GAP_HOURS = 6.0
DEFAULT_GPS_JUMP_KM = 50.0

UNDATED_CLUSTER_ID = "undated"


@dataclass
class Cluster:
    cluster_id: str
    photos: list[PhotoMetadata] = field(default_factory=list)

    @property
    def start(self) -> Optional[datetime]:
        dated = [p.datetime for p in self.photos if p.datetime]
        return min(dated) if dated else None

    @property
    def end(self) -> Optional[datetime]:
        dated = [p.datetime for p in self.photos if p.datetime]
        return max(dated) if dated else None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _cluster_id_for(dt: datetime, index: int) -> str:
    return f"{dt.strftime('%Y-%m-%d')}_{index:03d}"


def cluster_by_time(
    photos: Sequence[PhotoMetadata],
    *,
    gap_hours: float = DEFAULT_GAP_HOURS,
    gps_jump_km: Optional[float] = DEFAULT_GPS_JUMP_KM,
) -> list[Cluster]:
    """Splits into event/trip clusters.

    A new cluster starts whenever the gap to the previous (dated) photo
    exceeds ``gap_hours``, or — if ``gps_jump_km`` is set and both photos
    have GPS — the two are farther apart than that even within the time
    gap (e.g. a same-day flight to a new city).

    Photos with no usable timestamp are returned as a single trailing
    "undated" cluster; they need a human (or a filename/pre-2015-export
    convention) to place.
    """
    dated = sorted((p for p in photos if p.datetime is not None), key=lambda p: p.datetime)
    undated = [p for p in photos if p.datetime is None]

    clusters: list[Cluster] = []
    current: Optional[Cluster] = None
    prev: Optional[PhotoMetadata] = None
    cluster_index = 0

    gap_delta = timedelta(hours=gap_hours)

    for photo in dated:
        start_new = current is None
        if prev is not None and not start_new:
            time_gap = photo.datetime - prev.datetime
            if time_gap > gap_delta:
                start_new = True
            elif gps_jump_km is not None and prev.has_gps and photo.has_gps:
                dist = _haversine_km((prev.lat, prev.lon), (photo.lat, photo.lon))
                if dist > gps_jump_km:
                    start_new = True

        if start_new:
            cluster_index += 1
            current = Cluster(cluster_id=_cluster_id_for(photo.datetime, cluster_index))
            clusters.append(current)

        current.photos.append(photo)
        prev = photo

    if undated:
        clusters.append(Cluster(cluster_id=UNDATED_CLUSTER_ID, photos=undated))

    return clusters
