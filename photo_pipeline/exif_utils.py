"""EXIF extraction: timestamp, GPS, camera make/model, dimensions.

Kept dependency-light (Pillow only) since EXIF is read for every photo in
the library during pass 1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import ExifTags, Image

from . import heic_support  # noqa: F401  (registers HEIC opener on import)

logger = logging.getLogger(__name__)

_TAG_NAME_TO_ID = {name: tag_id for tag_id, name in ExifTags.TAGS.items()}
_GPS_TAG_NAME_TO_ID = {name: tag_id for tag_id, name in ExifTags.GPSTAGS.items()}

_DATETIME_TAGS = ("DateTimeOriginal", "DateTimeDigitized", "DateTime")
_EXIF_DT_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")


@dataclass
class PhotoMetadata:
    path: Path
    width: Optional[int] = None
    height: Optional[int] = None
    datetime: Optional[datetime] = None
    datetime_source: str = "none"  # "exif" | "none"
    lat: Optional[float] = None
    lon: Optional[float] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    has_camera_exif: bool = False
    format: Optional[str] = None
    error: Optional[str] = None

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def aspect_ratio(self) -> Optional[float]:
        if self.width and self.height:
            return self.width / self.height
        return None


def _to_degrees(value) -> float:
    d, m, s = value
    return float(d) + float(m) / 60.0 + float(s) / 3600.0


def _parse_gps(gps_ifd: dict) -> Optional[tuple[float, float]]:
    try:
        lat = gps_ifd.get(_GPS_TAG_NAME_TO_ID["GPSLatitude"])
        lat_ref = gps_ifd.get(_GPS_TAG_NAME_TO_ID["GPSLatitudeRef"])
        lon = gps_ifd.get(_GPS_TAG_NAME_TO_ID["GPSLongitude"])
        lon_ref = gps_ifd.get(_GPS_TAG_NAME_TO_ID["GPSLongitudeRef"])
        if lat is None or lon is None:
            return None
        lat_deg = _to_degrees(lat)
        lon_deg = _to_degrees(lon)
        if lat_ref in ("S", b"S"):
            lat_deg = -lat_deg
        if lon_ref in ("W", b"W"):
            lon_deg = -lon_deg
        return (lat_deg, lon_deg)
    except (KeyError, TypeError, ZeroDivisionError, ValueError):
        return None


def _parse_exif_datetime(value: str) -> Optional[datetime]:
    value = value.strip()
    for fmt in _EXIF_DT_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def is_plausible_timestamp(dt: Optional[datetime], *, earliest_year: int = 1995) -> bool:
    """Filters out the common junk EXIF timestamps: unset (1970/1980/1904/0000)."""
    if dt is None:
        return False
    if dt.year < earliest_year:
        return False
    if dt.year > datetime.now().year + 1:
        return False
    return True


def read_metadata(path: Path) -> PhotoMetadata:
    meta = PhotoMetadata(path=path)
    try:
        with Image.open(path) as img:
            meta.width, meta.height = img.size
            meta.format = img.format

            exif = None
            try:
                exif = img.getexif()
            except Exception:  # noqa: BLE001 - corrupt/odd EXIF blocks are common
                exif = None

            if exif:
                tag_values = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}

                # DateTimeOriginal/DateTimeDigitized live in the "Exif" sub-IFD, not
                # the top-level IFD0 block that getexif() returns directly.
                try:
                    exif_sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
                except Exception:  # noqa: BLE001
                    exif_sub_ifd = None
                if exif_sub_ifd:
                    tag_values.update(
                        {ExifTags.TAGS.get(k, k): v for k, v in exif_sub_ifd.items()}
                    )

                make = tag_values.get("Make")
                model = tag_values.get("Model")
                meta.camera_make = str(make).strip() if make else None
                meta.camera_model = str(model).strip() if model else None
                meta.has_camera_exif = bool(meta.camera_make or meta.camera_model)

                for tag in _DATETIME_TAGS:
                    raw = tag_values.get(tag)
                    if raw:
                        dt = _parse_exif_datetime(str(raw))
                        if is_plausible_timestamp(dt):
                            meta.datetime = dt
                            meta.datetime_source = "exif"
                            break

                # GPS lives in a sub-IFD keyed by the numeric GPSInfo tag id.
                try:
                    gps_ifd = exif.get_ifd(_TAG_NAME_TO_ID["GPSInfo"])
                except Exception:  # noqa: BLE001
                    gps_ifd = None
                if gps_ifd:
                    gps_named = {
                        ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()
                    }
                    coords = _parse_gps(
                        {_GPS_TAG_NAME_TO_ID[k]: v for k, v in gps_named.items() if k in _GPS_TAG_NAME_TO_ID}
                    )
                    if coords:
                        meta.lat, meta.lon = coords
    except Exception as exc:  # noqa: BLE001 - never let one bad file kill a batch
        meta.error = f"{type(exc).__name__}: {exc}"
        logger.warning("Failed to read %s: %s", path, meta.error)

    # Deliberately no filesystem-mtime fallback: st_mtime reflects when a file
    # landed on disk (export/copy/download time), not when the photo was
    # taken, and using it here fabricated a plausible-looking but wrong date
    # that corrupted clustering. A photo with no usable EXIF timestamp stays
    # meta.datetime = None and falls through to clustering.py's "undated"
    # trailing cluster instead.

    return meta
