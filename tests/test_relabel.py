import json

import pytest
from PIL import Image

from photo_pipeline.manifest import STATUS_KEPT, Manifest, PhotoRecord
from photo_pipeline.relabel import run_relabel
from photo_pipeline.review import DECISIONS_FILENAME


def _write_chapters(tmp_path):
    path = tmp_path / "chapters.yaml"
    path.write_text(
        """
chapters:
  - name: "How We Met"
    start: "2018-01-01"
    end: "2018-12-31"
"""
    )
    return path


def _build_year_month_output(output_dir):
    # Simulates what pass1.py would have produced *without* chapters.yaml:
    # a "2018-07" year-month folder holding one dated cluster, plus its
    # pass2 best/ pick and a pass3 highlights/ pick.
    cluster_dir = output_dir / "2018-07" / "2018-07-04_001"
    cluster_dir.mkdir(parents=True)
    Image.new("RGB", (50, 50)).save(cluster_dir / "photo_0.jpg")
    Image.new("RGB", (50, 50)).save(cluster_dir / "photo_1.jpg")

    best_dir = cluster_dir / "best"
    best_dir.mkdir()
    Image.new("RGB", (50, 50)).save(best_dir / "photo_0.jpg")

    highlights_dir = cluster_dir / "highlights"
    highlights_dir.mkdir()
    Image.new("RGB", (50, 50)).save(highlights_dir / "photo_0.jpg")

    # A second, undated cluster that must be left alone.
    undated_dir = output_dir / "undated" / "undated"
    undated_dir.mkdir(parents=True)
    Image.new("RGB", (50, 50)).save(undated_dir / "mystery.jpg")

    manifest = Manifest()
    manifest.add(
        PhotoRecord(
            original_path="/source/photo_0.jpg",
            status=STATUS_KEPT,
            cluster_id="2018-07-04_001",
            chapter_label="2018-07",
            output_path=str(cluster_dir / "photo_0.jpg"),
            pass2_selected=True,
            pass2_notes="sharpest",
            pass3_selected=True,
            pass3_notes="most candid",
        )
    )
    manifest.add(
        PhotoRecord(
            original_path="/source/photo_1.jpg",
            status=STATUS_KEPT,
            cluster_id="2018-07-04_001",
            chapter_label="2018-07",
            output_path=str(cluster_dir / "photo_1.jpg"),
            pass2_selected=False,
        )
    )
    manifest.add(
        PhotoRecord(
            original_path="/source/mystery.jpg",
            status=STATUS_KEPT,
            cluster_id="undated",
            chapter_label="undated",
            output_path=str(undated_dir / "mystery.jpg"),
        )
    )
    manifest.write_json(output_dir / "manifest.json")
    manifest.write_csv(output_dir / "manifest.csv")

    decisions = {
        "decisions": {
            "2018-07/2018-07-04_001/photo_0.jpg": {"decision": "keep", "decided_at": "t1"},
            "2018-07/2018-07-04_001/best/photo_0.jpg": {"decision": "keep", "decided_at": "t2"},
            "undated/undated/mystery.jpg": {"decision": "skip", "decided_at": "t3"},
        },
        "current_index": 2,
    }
    (output_dir / DECISIONS_FILENAME).write_text(json.dumps(decisions), encoding="utf-8")

    return cluster_dir, undated_dir


def test_relabel_moves_cluster_and_rewrites_manifest_and_decisions(tmp_path):
    output_dir = tmp_path / "output"
    cluster_dir, undated_dir = _build_year_month_output(output_dir)
    chapters_path = _write_chapters(tmp_path)

    result = run_relabel(output_dir, chapters_path)

    assert result["moved"] == 1
    assert result["skipped"] == []

    new_cluster_dir = output_dir / "how-we-met" / "2018-07-04_001"
    assert not cluster_dir.exists()
    assert (new_cluster_dir / "photo_0.jpg").exists()
    assert (new_cluster_dir / "photo_1.jpg").exists()
    assert (new_cluster_dir / "best" / "photo_0.jpg").exists()
    assert (new_cluster_dir / "highlights" / "photo_0.jpg").exists()

    # old year-month folder is gone entirely (now empty -> cleaned up)
    assert not (output_dir / "2018-07").exists()

    # undated cluster untouched
    assert undated_dir.exists()

    manifest = Manifest.read_json(output_dir / "manifest.json")
    by_id = {(r.cluster_id, r.original_path): r for r in manifest.records}

    moved_record = by_id[("2018-07-04_001", "/source/photo_0.jpg")]
    assert moved_record.chapter_label == "how-we-met"
    assert moved_record.output_path == str((new_cluster_dir / "photo_0.jpg").resolve())
    # pass2/pass3 annotations survived untouched
    assert moved_record.pass2_selected is True
    assert moved_record.pass2_notes == "sharpest"
    assert moved_record.pass3_selected is True
    assert moved_record.pass3_notes == "most candid"

    other_moved_record = by_id[("2018-07-04_001", "/source/photo_1.jpg")]
    assert other_moved_record.chapter_label == "how-we-met"

    undated_record = by_id[("undated", "/source/mystery.jpg")]
    assert undated_record.chapter_label == "undated"
    assert undated_record.output_path == str(undated_dir / "mystery.jpg")

    decisions = json.loads((output_dir / DECISIONS_FILENAME).read_text())["decisions"]
    assert decisions["how-we-met/2018-07-04_001/photo_0.jpg"]["decision"] == "keep"
    assert decisions["how-we-met/2018-07-04_001/best/photo_0.jpg"]["decision"] == "keep"
    assert "2018-07/2018-07-04_001/photo_0.jpg" not in decisions
    assert decisions["undated/undated/mystery.jpg"]["decision"] == "skip"

    # browsing position reset since item order changed; individual decisions kept
    data = json.loads((output_dir / DECISIONS_FILENAME).read_text())
    assert data["current_index"] == 0


def test_relabel_dry_run_touches_nothing(tmp_path):
    output_dir = tmp_path / "output"
    cluster_dir, _ = _build_year_month_output(output_dir)
    chapters_path = _write_chapters(tmp_path)

    original_manifest_text = (output_dir / "manifest.json").read_text()
    original_decisions_text = (output_dir / DECISIONS_FILENAME).read_text()

    result = run_relabel(output_dir, chapters_path, dry_run=True)

    assert result == {"planned": 1}
    assert cluster_dir.exists()
    assert (output_dir / "manifest.json").read_text() == original_manifest_text
    assert (output_dir / DECISIONS_FILENAME).read_text() == original_decisions_text


def test_relabel_is_idempotent(tmp_path):
    output_dir = tmp_path / "output"
    _build_year_month_output(output_dir)
    chapters_path = _write_chapters(tmp_path)

    run_relabel(output_dir, chapters_path)
    second = run_relabel(output_dir, chapters_path)

    assert second == {"moved": 0, "manifest_updated": 0, "decisions_rewritten": 0, "skipped": []}


def test_relabel_requires_existing_manifest(tmp_path):
    chapters_path = _write_chapters(tmp_path)
    with pytest.raises(FileNotFoundError):
        run_relabel(tmp_path / "nope", chapters_path)
