from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from photo_pipeline.manifest import (
    STATUS_BLURRY,
    STATUS_KEPT,
    STATUS_SCREENSHOT,
)
from photo_pipeline.pass1 import run_pass1


def test_pass1_end_to_end(tmp_path, photo_factory):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    base = datetime(2018, 7, 4, 12, 0, 0)

    for i in range(3):
        photo_factory(
            path=input_dir / f"burst_{i}.jpg",
            dt=base + timedelta(seconds=i * 2),
            sharp=True,
            gps=(40.71, -74.00),
        )
    photo_factory(path=input_dir / "burst_blurry.jpg", dt=base + timedelta(seconds=30), sharp=False, gps=(40.71, -74.00))
    photo_factory(path=input_dir / "day2.jpg", dt=base + timedelta(days=3), sharp=True)

    Image.new("RGB", (1170, 2532), "white").save(input_dir / "Screenshot_test.png", "png")

    manifest = run_pass1(input_dir, output_dir, contact_sheets=True)
    summary = manifest.summary()

    assert summary.get(STATUS_BLURRY) == 1
    assert summary.get(STATUS_SCREENSHOT) == 1
    assert summary.get(STATUS_KEPT, 0) >= 2  # burst collapses to >=1 + day2

    assert (output_dir / "manifest.csv").exists()
    assert (output_dir / "manifest.json").exists()

    kept_records = [r for r in manifest.records if r.status == STATUS_KEPT]
    for record in kept_records:
        assert record.output_path is not None
        assert Path(record.output_path).exists()

    contact_sheets = list(output_dir.rglob("_contact_sheet.jpg"))
    assert len(contact_sheets) >= 1


def test_pass1_never_touches_originals(tmp_path, photo_factory):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    photo_factory(path=input_dir / "photo.jpg", dt=datetime(2020, 1, 1, 9, 0))
    original_bytes = (input_dir / "photo.jpg").read_bytes()

    run_pass1(input_dir, output_dir)

    assert (input_dir / "photo.jpg").read_bytes() == original_bytes
