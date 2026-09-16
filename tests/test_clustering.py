from datetime import datetime

from photo_pipeline.clustering import cluster_by_time
from photo_pipeline.exif_utils import PhotoMetadata


def _meta(path, dt=None, lat=None, lon=None):
    return PhotoMetadata(path=path, datetime=dt, lat=lat, lon=lon)


def test_splits_on_time_gap():
    photos = [
        _meta("a", datetime(2018, 7, 4, 10, 0)),
        _meta("b", datetime(2018, 7, 4, 10, 5)),
        _meta("c", datetime(2018, 7, 10, 9, 0)),  # 6 days later -> new cluster
    ]
    clusters = cluster_by_time(photos, gap_hours=6)
    assert len(clusters) == 2
    assert len(clusters[0].photos) == 2
    assert len(clusters[1].photos) == 1


def test_splits_on_gps_jump_within_time_gap():
    photos = [
        _meta("a", datetime(2018, 7, 4, 10, 0), lat=40.71, lon=-74.00),  # NYC
        _meta("b", datetime(2018, 7, 4, 12, 0), lat=48.85, lon=2.35),  # Paris, 2h later
    ]
    clusters = cluster_by_time(photos, gap_hours=6, gps_jump_km=50)
    assert len(clusters) == 2


def test_undated_photos_form_trailing_cluster():
    photos = [
        _meta("a", datetime(2018, 7, 4, 10, 0)),
        _meta("b", None),
        _meta("c", None),
    ]
    clusters = cluster_by_time(photos)
    assert clusters[-1].cluster_id == "undated"
    assert len(clusters[-1].photos) == 2
