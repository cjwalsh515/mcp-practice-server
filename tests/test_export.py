import json

import pytest
from PIL import Image

from photo_pipeline.export import kept_rel_paths, load_decisions, run_export
from photo_pipeline.review import DECISIONS_FILENAME


def _make_cluster(output_dir, chapter, cluster_id, filenames):
    cluster_dir = output_dir / chapter / cluster_id
    cluster_dir.mkdir(parents=True)
    for name in filenames:
        Image.new("RGB", (50, 50), (10, 20, 30)).save(cluster_dir / name)
    return cluster_dir


def _write_decisions(output_dir, decisions):
    (output_dir / DECISIONS_FILENAME).write_text(
        json.dumps({"decisions": decisions, "current_index": 0}), encoding="utf-8"
    )


def test_load_decisions_requires_review_run_first(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_decisions(tmp_path)


def test_kept_rel_paths_filters_out_skips_and_pending():
    decisions = {
        "a.jpg": {"decision": "keep"},
        "b.jpg": {"decision": "skip"},
        "c.jpg": {"decision": "keep"},
    }
    assert kept_rel_paths(decisions) == ["a.jpg", "c.jpg"]


def test_run_export_copies_kept_photos_grouped_by_chapter(tmp_path):
    output_dir = tmp_path / "output"
    _make_cluster(output_dir, "2018-07", "2018-07-04_001", ["photo_0.jpg", "photo_1.jpg"])
    _make_cluster(output_dir, "2019-01", "2019-01-01_001", ["photo_0.jpg"])  # same filename, different chapter

    _write_decisions(
        output_dir,
        {
            "2018-07/2018-07-04_001/photo_0.jpg": {"decision": "keep", "decided_at": "t1"},
            "2018-07/2018-07-04_001/photo_1.jpg": {"decision": "skip", "decided_at": "t2"},
            "2019-01/2019-01-01_001/photo_0.jpg": {"decision": "keep", "decided_at": "t3"},
        },
    )

    dest_dir = tmp_path / "final"
    exported = run_export(output_dir, dest_dir)

    assert len(exported) == 2
    assert (dest_dir / "2018-07" / "2018-07-04_001_photo_0.jpg").exists()
    assert (dest_dir / "2019-01" / "2019-01-01_001_photo_0.jpg").exists()
    assert not (dest_dir / "2018-07" / "2018-07-04_001_photo_1.jpg").exists()

    assert (dest_dir / "2018-07" / "_contact_sheet.jpg").exists()
    assert (dest_dir / "2019-01" / "_contact_sheet.jpg").exists()
    assert (dest_dir / "export_manifest.csv").exists()

    manifest = json.loads((dest_dir / "export_manifest.json").read_text())
    assert len(manifest) == 2
    assert {row["chapter"] for row in manifest} == {"2018-07", "2019-01"}


def test_run_export_handles_filename_collisions_within_same_cluster(tmp_path):
    # A cluster's kept/ and best/ layers can hold same-named copies of the
    # same original photo — e.g. reviewed once before pass2 ran (against
    # kept/photo_0.jpg) and again after (against best/photo_0.jpg). Both
    # would resolve to the same "<cluster_id>_photo_0.jpg" destination name.
    output_dir = tmp_path / "output"
    cluster_dir = _make_cluster(output_dir, "2018-07", "c1", ["photo_0.jpg"])
    best_dir = cluster_dir / "best"
    best_dir.mkdir()
    Image.new("RGB", (50, 50), (99, 99, 99)).save(best_dir / "photo_0.jpg")

    _write_decisions(
        output_dir,
        {
            "2018-07/c1/photo_0.jpg": {"decision": "keep", "decided_at": "t1"},
            "2018-07/c1/best/photo_0.jpg": {"decision": "keep", "decided_at": "t2"},
        },
    )

    dest_dir = tmp_path / "final"
    exported = run_export(output_dir, dest_dir)
    assert len(exported) == 2
    exported_names = {row["exported_path"].split("/")[-1] for row in exported}
    assert exported_names == {"c1_photo_0.jpg", "c1_photo_0__1.jpg"}


def test_run_export_skips_missing_files_gracefully(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_decisions(output_dir, {"nonexistent/cluster/photo.jpg": {"decision": "keep"}})

    dest_dir = tmp_path / "final"
    exported = run_export(output_dir, dest_dir)
    assert exported == []


def test_run_export_no_keeps_returns_empty(tmp_path):
    output_dir = tmp_path / "output"
    _make_cluster(output_dir, "2018-07", "c1", ["photo.jpg"])
    _write_decisions(output_dir, {"2018-07/c1/photo.jpg": {"decision": "skip"}})

    dest_dir = tmp_path / "final"
    exported = run_export(output_dir, dest_dir)
    assert exported == []


def test_reexport_with_unchanged_decisions_is_idempotent_not_duplicated(tmp_path):
    output_dir = tmp_path / "output"
    _make_cluster(output_dir, "2018-07", "c1", ["photo.jpg"])
    _write_decisions(output_dir, {"2018-07/c1/photo.jpg": {"decision": "keep", "decided_at": "t1"}})

    dest_dir = tmp_path / "final"
    run_export(output_dir, dest_dir)
    second = run_export(output_dir, dest_dir)

    assert len(second) == 1
    assert second[0]["exported_path"] == str(dest_dir / "2018-07" / "c1_photo.jpg")
    # no __1 duplicate from treating the previous run's own file as a collision
    assert list((dest_dir / "2018-07").glob("c1_photo*")) == [dest_dir / "2018-07" / "c1_photo.jpg"]


def test_reexport_prunes_file_when_decision_flips_to_skip(tmp_path):
    output_dir = tmp_path / "output"
    _make_cluster(output_dir, "2018-07", "c1", ["photo.jpg"])
    _write_decisions(output_dir, {"2018-07/c1/photo.jpg": {"decision": "keep", "decided_at": "t1"}})

    dest_dir = tmp_path / "final"
    run_export(output_dir, dest_dir)
    assert (dest_dir / "2018-07" / "c1_photo.jpg").exists()

    _write_decisions(output_dir, {"2018-07/c1/photo.jpg": {"decision": "skip", "decided_at": "t2"}})
    second = run_export(output_dir, dest_dir)

    assert second == []
    assert not (dest_dir / "2018-07" / "c1_photo.jpg").exists()
    # the whole chapter folder is gone too, since nothing else was in it
    assert not (dest_dir / "2018-07").exists()


def test_reexport_prunes_stale_folder_after_relabel_style_rel_path_change(tmp_path):
    # Simulates relabel.py having moved the underlying cluster and rewritten
    # review_decisions.json's key to match — the old chapter-named export
    # folder must not be left behind as clutter in --dest.
    output_dir = tmp_path / "output"
    _make_cluster(output_dir, "2018-07", "2018-07-04_001", ["photo.jpg"])
    _write_decisions(
        output_dir, {"2018-07/2018-07-04_001/photo.jpg": {"decision": "keep", "decided_at": "t1"}}
    )

    dest_dir = tmp_path / "final"
    run_export(output_dir, dest_dir)
    assert (dest_dir / "2018-07" / "2018-07-04_001_photo.jpg").exists()

    # relabel.py's real effect: the file moves on disk under --output, and
    # review_decisions.json's key is rewritten to the new chapter label.
    old_cluster_dir = output_dir / "2018-07" / "2018-07-04_001"
    new_cluster_dir = output_dir / "how-we-met" / "2018-07-04_001"
    new_cluster_dir.parent.mkdir(parents=True)
    old_cluster_dir.rename(new_cluster_dir)
    _write_decisions(
        output_dir, {"how-we-met/2018-07-04_001/photo.jpg": {"decision": "keep", "decided_at": "t1"}}
    )

    second = run_export(output_dir, dest_dir)

    assert len(second) == 1
    assert (dest_dir / "how-we-met" / "2018-07-04_001_photo.jpg").exists()
    # old chapter folder fully cleaned up, not left behind as stale clutter
    assert not (dest_dir / "2018-07").exists()


def test_reexport_never_touches_files_it_did_not_create(tmp_path):
    output_dir = tmp_path / "output"
    _make_cluster(output_dir, "2018-07", "c1", ["photo.jpg"])
    _write_decisions(output_dir, {"2018-07/c1/photo.jpg": {"decision": "keep", "decided_at": "t1"}})

    dest_dir = tmp_path / "final"
    run_export(output_dir, dest_dir)

    # user manually drops something into the export folder and into an
    # unrelated chapter folder that this tool never exported anything to
    (dest_dir / "notes.txt").write_text("do not touch")
    manual_dir = dest_dir / "manual-additions"
    manual_dir.mkdir()
    (manual_dir / "my_own_photo.jpg").write_bytes(b"fake")

    _write_decisions(output_dir, {"2018-07/c1/photo.jpg": {"decision": "skip", "decided_at": "t2"}})
    run_export(output_dir, dest_dir)

    assert (dest_dir / "notes.txt").read_text() == "do not touch"
    assert (manual_dir / "my_own_photo.jpg").exists()
