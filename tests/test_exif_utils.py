from datetime import datetime

import pytest

from photo_pipeline.exif_utils import is_plausible_timestamp, read_metadata


def test_reads_datetime_original_from_exif_sub_ifd(photo_factory):
    path = photo_factory(dt=datetime(2018, 7, 4, 12, 0, 0))
    meta = read_metadata(path)
    assert meta.datetime == datetime(2018, 7, 4, 12, 0, 0)
    assert meta.datetime_source == "exif"


def test_reads_gps(photo_factory):
    path = photo_factory(dt=datetime(2018, 7, 4), gps=(40.7128, -74.0060))
    meta = read_metadata(path)
    assert meta.has_gps
    assert meta.lat == pytest.approx(40.7128, rel=1e-3)
    assert meta.lon == pytest.approx(-74.0060, rel=1e-3)


def test_camera_exif_detected(photo_factory):
    path = photo_factory(camera=True)
    meta = read_metadata(path)
    assert meta.has_camera_exif
    assert meta.camera_make == "TestCam"


def test_missing_camera_exif(photo_factory):
    path = photo_factory(camera=False, dt=None)
    meta = read_metadata(path)
    assert not meta.has_camera_exif


def test_no_mtime_fallback_when_no_exif_timestamp(photo_factory):
    # A freshly-copied/re-downloaded file's mtime reflects when it landed on
    # disk, not when the photo was taken — it must never be used as a stand-in
    # date. No usable EXIF timestamp should leave datetime unset entirely, so
    # the photo falls through to clustering.py's "undated" handling.
    path = photo_factory(camera=False, dt=None)
    meta = read_metadata(path)
    assert meta.datetime is None
    assert meta.datetime_source == "none"


def test_implausible_timestamps_rejected():
    assert not is_plausible_timestamp(datetime(1970, 1, 1))
    assert not is_plausible_timestamp(None)
    assert is_plausible_timestamp(datetime(2018, 5, 1))
