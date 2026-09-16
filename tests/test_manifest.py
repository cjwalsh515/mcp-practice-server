from photo_pipeline.manifest import Manifest, PhotoRecord, STATUS_DUPLICATE, STATUS_KEPT


def test_write_and_read_json_roundtrip(tmp_path):
    manifest = Manifest()
    manifest.add(PhotoRecord(original_path="a.jpg", status=STATUS_KEPT, cluster_id="c1"))
    manifest.add(PhotoRecord(original_path="b.jpg", status=STATUS_DUPLICATE, duplicate_of="a.jpg"))

    json_path = tmp_path / "manifest.json"
    manifest.write_json(json_path)

    reloaded = Manifest.read_json(json_path)
    assert len(reloaded.records) == 2
    assert reloaded.records[0].original_path == "a.jpg"
    assert reloaded.records[1].duplicate_of == "a.jpg"


def test_write_csv_does_not_raise(tmp_path):
    manifest = Manifest()
    manifest.add(PhotoRecord(original_path="a.jpg", status=STATUS_KEPT))
    manifest.write_csv(tmp_path / "manifest.csv")
    assert (tmp_path / "manifest.csv").exists()


def test_summary_counts_by_status():
    manifest = Manifest()
    manifest.add(PhotoRecord(original_path="a.jpg", status=STATUS_KEPT))
    manifest.add(PhotoRecord(original_path="b.jpg", status=STATUS_KEPT))
    manifest.add(PhotoRecord(original_path="c.jpg", status=STATUS_DUPLICATE))
    assert manifest.summary() == {STATUS_KEPT: 2, STATUS_DUPLICATE: 1}
