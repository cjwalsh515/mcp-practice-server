"""Relabels an existing pass1 output folder's chapter grouping in place —
independent of pass1.py/pass2.py/pass3.py/review.py/export.py, none of
which are modified or need to be re-run.

    python -m photo_pipeline.relabel --output ~/culled --chapters chapters.yaml

Use this when you introduce (or change) chapters.yaml *after* you've
already run pass1 — and possibly pass2/pass3/review — against year-month
labeled output. Re-running pass1 itself into the same --output directory
would duplicate every kept photo under new chapter folders alongside the
old year-month ones (pass1 never deletes anything) and would overwrite
manifest.json, discarding pass2/pass3 annotations and orphaning any
review.py decisions already made.

This tool instead only moves folders and rewrites bookkeeping:

- Every cluster's id already encodes its own date (``YYYY-MM-DD_NNN``,
  see clustering.py), so the new label can be recomputed directly from the
  folder name — no re-reading EXIF, re-clustering, re-deduping, or
  re-scoring blur is needed.
- Each cluster's whole subfolder (kept photos, best/, highlights/,
  contact sheet) is moved as one unit, so pass2_selected/pass3_notes and
  the best/highlights picks travel with it untouched.
- manifest.json/.csv gets chapter_label (and output_path) rewritten in
  place for affected rows, not regenerated from scratch.
- review_decisions.json's keys get the same old-label -> new-label swap,
  so every prior Y/N call survives.

Note: because review.py's item order depends on chapter folder names,
relabeling changes that order, so the "resume where I left off" position
(current_index) is reset to 0 after a relabel that touches anything — all
individual decisions are preserved, only the browsing cursor resets.

The one thing this can't relabel is the "undated" cluster (no EXIF/email
date to work from); it's left exactly where it is, same as pass1 leaves it
alone when chapters.yaml is used the first time.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .chapters import ChapterRange, assign_chapter, load_chapters, slugify
from .manifest import Manifest
from .output_layout import discover_cluster_dirs
from .review import DECISIONS_FILENAME

logger = logging.getLogger("photo_pipeline.relabel")

_CLUSTER_ID_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_\d{3}$")


def _parse_cluster_date(cluster_id: str) -> Optional[date]:
    match = _CLUSTER_ID_DATE_RE.match(cluster_id)
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _label_for_date(d: date, chapters: list[ChapterRange]) -> str:
    """Mirrors chapters.label_for_cluster's dated branch, minus needing a
    real Cluster object — just the date extracted from the cluster id.
    """
    chapter_name = assign_chapter(d, chapters)
    if chapter_name:
        return slugify(chapter_name)
    return d.strftime("%Y-%m")


@dataclass
class RelabelPlan:
    cluster_id: str
    old_label: str
    new_label: str
    old_dir: Path
    new_dir: Path


def compute_plan(output_dir: Path, chapters: list[ChapterRange]) -> tuple[list[RelabelPlan], list[str]]:
    """Returns (moves, cluster_ids_with_no_parseable_date)."""
    moves = []
    unparseable = []
    for cluster_dir in discover_cluster_dirs(output_dir):
        old_label = cluster_dir.parent.name
        cluster_id = cluster_dir.name
        d = _parse_cluster_date(cluster_id)
        if d is None:
            unparseable.append(cluster_id)
            continue
        new_label = _label_for_date(d, chapters)
        if new_label == old_label:
            continue
        moves.append(
            RelabelPlan(
                cluster_id=cluster_id,
                old_label=old_label,
                new_label=new_label,
                old_dir=cluster_dir,
                new_dir=output_dir / new_label / cluster_id,
            )
        )
    return moves, unparseable


def apply_moves(moves: list[RelabelPlan]) -> tuple[list[RelabelPlan], list[RelabelPlan]]:
    applied, skipped = [], []
    for plan in moves:
        if plan.new_dir.exists():
            logger.error(
                "Skipping %s: destination %s already exists (not clobbering it — investigate manually)",
                plan.cluster_id,
                plan.new_dir,
            )
            skipped.append(plan)
            continue
        plan.new_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(plan.old_dir), str(plan.new_dir))
        applied.append(plan)
    return applied, skipped


def _cleanup_empty_chapter_dirs(output_dir: Path) -> None:
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        if not any(child.iterdir()):
            child.rmdir()
            logger.info("Removed now-empty folder %s", child)


def update_manifest(output_dir: Path, applied: list[RelabelPlan]) -> int:
    manifest_path = output_dir / "manifest.json"
    manifest = Manifest.read_json(manifest_path)
    by_cluster_id = {p.cluster_id: p for p in applied}

    updated = 0
    for record in manifest.records:
        plan = by_cluster_id.get(record.cluster_id)
        if plan is None:
            continue
        record.chapter_label = plan.new_label
        if record.output_path:
            old_path = Path(record.output_path).expanduser().resolve()
            old_dir = plan.old_dir.expanduser().resolve()
            if old_path.parent == old_dir:
                new_dir = plan.new_dir.expanduser().resolve()
                record.output_path = str(new_dir / old_path.name)
        updated += 1

    manifest.write_csv(output_dir / "manifest.csv")
    manifest.write_json(manifest_path)
    return updated


def update_decisions(output_dir: Path, applied: list[RelabelPlan]) -> int:
    decisions_path = output_dir / DECISIONS_FILENAME
    if not decisions_path.exists():
        return 0

    data = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions = data.get("decisions", {})
    relabel_by_key = {(p.old_label, p.cluster_id): p.new_label for p in applied}

    new_decisions = {}
    rewritten = 0
    for rel_path, info in decisions.items():
        parts = Path(rel_path).parts
        key = (parts[0], parts[1]) if len(parts) >= 2 else None
        if key in relabel_by_key:
            new_rel_path = "/".join([relabel_by_key[key], *parts[1:]])
            new_decisions[new_rel_path] = info
            rewritten += 1
        else:
            new_decisions[rel_path] = info

    data["decisions"] = new_decisions
    if rewritten:
        # Item order depends on chapter folder names, which just changed —
        # every decision is preserved, but "where was I" no longer means
        # the same thing.
        data["current_index"] = 0

    decisions_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return rewritten


def run_relabel(output_dir: Path, chapters_path: Path, *, dry_run: bool = False) -> dict:
    if not (output_dir / "manifest.json").exists():
        raise FileNotFoundError(f"No manifest.json under {output_dir} — run pass1 first.")

    chapters = load_chapters(chapters_path)
    moves, unparseable = compute_plan(output_dir, chapters)

    if unparseable:
        logger.info(
            "%d cluster(s) have no parseable date in their id (e.g. 'undated') and are left as-is: %s",
            len(unparseable),
            unparseable,
        )

    if not moves:
        logger.info("Nothing to relabel — every cluster already matches chapters.yaml.")
        return {"moved": 0, "manifest_updated": 0, "decisions_rewritten": 0, "skipped": []}

    if dry_run:
        for plan in moves:
            logger.info("[dry-run] %s: %s -> %s", plan.cluster_id, plan.old_label, plan.new_label)
        return {"planned": len(moves)}

    applied, skipped = apply_moves(moves)
    _cleanup_empty_chapter_dirs(output_dir)
    manifest_updated = update_manifest(output_dir, applied)
    decisions_rewritten = update_decisions(output_dir, applied)

    logger.info(
        "Relabeled %d cluster(s), updated %d manifest record(s), rewrote %d review decision key(s).",
        len(applied),
        manifest_updated,
        decisions_rewritten,
    )
    if skipped:
        logger.warning(
            "%d cluster(s) skipped due to a destination collision: %s",
            len(skipped),
            [p.cluster_id for p in skipped],
        )

    return {
        "moved": len(applied),
        "manifest_updated": manifest_updated,
        "decisions_rewritten": decisions_rewritten,
        "skipped": [p.cluster_id for p in skipped],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, type=Path, help="Existing pass1 output folder to relabel in place")
    parser.add_argument("--chapters", required=True, type=Path, help="chapters.yaml to relabel against")
    parser.add_argument("--dry-run", action="store_true", help="Show what would move without touching anything")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    run_relabel(args.output, args.chapters, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
