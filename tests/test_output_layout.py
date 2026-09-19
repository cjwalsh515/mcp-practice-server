from PIL import Image

from photo_pipeline.output_layout import (
    best_candidates,
    cluster_images,
    discover_cluster_dirs,
    review_candidates,
)


def _make_cluster(base, chapter, cluster_id, n=2):
    cluster_dir = base / chapter / cluster_id
    cluster_dir.mkdir(parents=True)
    for i in range(n):
        Image.new("RGB", (50, 50), (i * 10, 0, 0)).save(cluster_dir / f"photo_{i}.jpg")
    return cluster_dir


def test_discover_cluster_dirs_ignores_derived_subfolders(tmp_path):
    cluster_dir = _make_cluster(tmp_path, "2018-07", "2018-07-04_001")
    (cluster_dir / "best").mkdir()
    Image.new("RGB", (50, 50)).save(cluster_dir / "best" / "photo_0.jpg")

    dirs = discover_cluster_dirs(tmp_path)
    assert dirs == [cluster_dir]


def test_cluster_images_skips_contact_sheet(tmp_path):
    cluster_dir = _make_cluster(tmp_path, "2018-07", "c1", n=2)
    Image.new("RGB", (10, 10)).save(cluster_dir / "_contact_sheet.jpg")

    images = cluster_images(cluster_dir)
    assert {p.name for p in images} == {"photo_0.jpg", "photo_1.jpg"}


def test_best_candidates_prefers_best_dir(tmp_path):
    cluster_dir = _make_cluster(tmp_path, "2018-07", "c1", n=3)
    best_dir = cluster_dir / "best"
    best_dir.mkdir()
    Image.new("RGB", (10, 10)).save(best_dir / "photo_0.jpg")

    images, source = best_candidates(cluster_dir)
    assert source == "best"
    assert [p.name for p in images] == ["photo_0.jpg"]


def test_best_candidates_falls_back_to_kept(tmp_path):
    cluster_dir = _make_cluster(tmp_path, "2018-07", "c1", n=2)
    images, source = best_candidates(cluster_dir)
    assert source == "kept"
    assert len(images) == 2


def test_review_candidates_prefers_highlights_over_best(tmp_path):
    cluster_dir = _make_cluster(tmp_path, "2018-07", "c1", n=3)
    best_dir = cluster_dir / "best"
    best_dir.mkdir()
    Image.new("RGB", (10, 10)).save(best_dir / "photo_0.jpg")
    highlights_dir = cluster_dir / "highlights"
    highlights_dir.mkdir()
    Image.new("RGB", (10, 10)).save(highlights_dir / "photo_1.jpg")

    images, source = review_candidates(cluster_dir)
    assert source == "highlights"
    assert [p.name for p in images] == ["photo_1.jpg"]
