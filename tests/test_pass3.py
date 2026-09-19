from pathlib import Path

import pytest
from PIL import Image

from photo_pipeline import pass3
from photo_pipeline.manifest import STATUS_KEPT, Manifest, PhotoRecord
from photo_pipeline.output_layout import HIGHLIGHTS_DIRNAME


def _make_cluster(output_dir, chapter, cluster_id, n):
    cluster_dir = output_dir / chapter / cluster_id
    cluster_dir.mkdir(parents=True)
    paths = []
    for i in range(n):
        path = cluster_dir / f"photo_{i}.jpg"
        Image.new("RGB", (50, 50), (i * 20, 0, 0)).save(path)
        paths.append(path)
    return cluster_dir, paths


def _manifest_for(cluster_id, paths):
    manifest = Manifest()
    for path in paths:
        manifest.add(
            PhotoRecord(
                original_path=str(path),
                status=STATUS_KEPT,
                cluster_id=cluster_id,
                output_path=str(path),
            )
        )
    return manifest


def _records_by_cluster_and_filename(manifest):
    return {
        (r.cluster_id, Path(r.output_path).name): r
        for r in manifest.records
        if r.output_path and r.cluster_id
    }


def test_single_candidate_is_auto_selected_without_api_call(tmp_path, monkeypatch):
    cluster_dir, paths = _make_cluster(tmp_path, "2018-07", "c1", n=1)
    manifest = _manifest_for("c1", paths)

    def _boom(*args, **kwargs):
        raise AssertionError("should not call the API for a single-candidate cluster")

    monkeypatch.setattr(pass3, "_ask_claude_for_highlights", _boom)

    pass3.process_cluster_dir(
        cluster_dir,
        client=None,
        model="claude-opus-5",
        top_n=2,
        max_dimension=1280,
        min_candidates=1,
        max_candidates=15,
        records_by_cluster_and_filename=_records_by_cluster_and_filename(manifest),
        dry_run=False,
    )

    highlights = cluster_dir / HIGHLIGHTS_DIRNAME
    assert (highlights / "photo_0.jpg").exists()
    assert manifest.records[0].pass3_selected is True
    assert "only surviving candidate" in manifest.records[0].pass3_notes


def test_multi_candidate_applies_claude_picks(tmp_path, monkeypatch):
    cluster_dir, paths = _make_cluster(tmp_path, "2018-07", "c1", n=3)
    manifest = _manifest_for("c1", paths)

    def _fake_ask(client, model, images, *, top_n, max_dimension):
        return {"picks": ["photo_1.jpg"], "reasoning": "most candid moment"}

    monkeypatch.setattr(pass3, "_ask_claude_for_highlights", _fake_ask)

    records = _records_by_cluster_and_filename(manifest)
    pass3.process_cluster_dir(
        cluster_dir,
        client=object(),
        model="claude-opus-5",
        top_n=2,
        max_dimension=1280,
        min_candidates=1,
        max_candidates=15,
        records_by_cluster_and_filename=records,
        dry_run=False,
    )

    highlights = cluster_dir / HIGHLIGHTS_DIRNAME
    assert (highlights / "photo_1.jpg").exists()
    assert not (highlights / "photo_0.jpg").exists()
    assert not (highlights / "photo_2.jpg").exists()

    selected = records[("c1", "photo_1.jpg")]
    assert selected.pass3_selected is True
    assert selected.pass3_notes == "most candid moment"

    not_selected = records[("c1", "photo_0.jpg")]
    assert not_selected.pass3_selected is False
    assert not_selected.pass3_notes is None


def test_dry_run_never_calls_api_or_writes_files(tmp_path, monkeypatch):
    cluster_dir, paths = _make_cluster(tmp_path, "2018-07", "c1", n=3)
    manifest = _manifest_for("c1", paths)

    def _boom(*args, **kwargs):
        raise AssertionError("dry-run must never call the API")

    monkeypatch.setattr(pass3, "_ask_claude_for_highlights", _boom)

    pass3.process_cluster_dir(
        cluster_dir,
        client=None,
        model="claude-opus-5",
        top_n=2,
        max_dimension=1280,
        min_candidates=1,
        max_candidates=15,
        records_by_cluster_and_filename=_records_by_cluster_and_filename(manifest),
        dry_run=True,
    )

    assert not (cluster_dir / HIGHLIGHTS_DIRNAME).exists()
    assert all(r.pass3_selected is None for r in manifest.records)


def test_exceeding_max_candidates_skips_cluster(tmp_path, monkeypatch):
    cluster_dir, paths = _make_cluster(tmp_path, "2018-07", "c1", n=5)
    manifest = _manifest_for("c1", paths)

    def _boom(*args, **kwargs):
        raise AssertionError("should not call the API when over max-candidates")

    monkeypatch.setattr(pass3, "_ask_claude_for_highlights", _boom)

    pass3.process_cluster_dir(
        cluster_dir,
        client=object(),
        model="claude-opus-5",
        top_n=2,
        max_dimension=1280,
        min_candidates=1,
        max_candidates=3,
        records_by_cluster_and_filename=_records_by_cluster_and_filename(manifest),
        dry_run=False,
    )

    assert not (cluster_dir / HIGHLIGHTS_DIRNAME).exists()


def test_prefers_best_dir_over_kept_when_pass2_already_ran(tmp_path, monkeypatch):
    cluster_dir, paths = _make_cluster(tmp_path, "2018-07", "c1", n=3)
    best_dir = cluster_dir / "best"
    best_dir.mkdir()
    Image.new("RGB", (50, 50)).save(best_dir / "photo_0.jpg")
    manifest = _manifest_for("c1", paths)

    seen = {}

    def _fake_ask(client, model, images, *, top_n, max_dimension):
        seen["images"] = [p.name for p in images]
        raise AssertionError("shouldn't be called for a single best/ candidate")

    monkeypatch.setattr(pass3, "_ask_claude_for_highlights", _fake_ask)

    pass3.process_cluster_dir(
        cluster_dir,
        client=None,
        model="claude-opus-5",
        top_n=2,
        max_dimension=1280,
        min_candidates=1,
        max_candidates=15,
        records_by_cluster_and_filename=_records_by_cluster_and_filename(manifest),
        dry_run=False,
    )

    # only the single best/ photo should have been treated as the candidate set
    assert (cluster_dir / HIGHLIGHTS_DIRNAME / "photo_0.jpg").exists()


def test_run_pass3_requires_existing_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        pass3.run_pass3(tmp_path / "nope", dry_run=True)


def test_run_pass3_dry_run_end_to_end(tmp_path):
    output_dir = tmp_path / "output"
    cluster_dir, paths = _make_cluster(output_dir, "2018-07", "c1", n=3)
    manifest = _manifest_for("c1", paths)
    manifest.write_json(output_dir / "manifest.json")

    result = pass3.run_pass3(output_dir, dry_run=True)

    assert len(result.records) == 3
    assert not (cluster_dir / HIGHLIGHTS_DIRNAME).exists()
