"""Exports review.py's keep decisions into a handoff-ready folder.

    python3 -m photo_pipeline.export --output ~/culled --dest ~/PhotoProject/final

This is the actual finish line for the culling pipeline: reads
``<output>/review_decisions.json`` (written by review.py) and copies every
photo marked "keep" into ``--dest``, grouped by chapter, with a contact
sheet per chapter and a manifest of exactly what got exported — the
folder you hand off to Mixbook.

Re-running is a *sync*, not just an append: it compares the new run's
exported paths against the previous run's ``export_manifest.json`` and
removes anything it previously placed that's no longer part of the
current keep-set — a decision that flipped from keep to skip, or a
chapter folder that moved after running relabel.py on --output. It only
ever deletes files it tracked in its own manifest; anything else you've
added to --dest by hand is left alone.

Independent of pass1.py/pass2.py/pass3.py/review.py/relabel.py; never
modifies --output or any original file, only ever copies out of it (or
removes its own previously-exported copies).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional

from . import heic_support  # noqa: F401
from .contact_sheet import generate_contact_sheet
from .output_layout import CONTACT_SHEET_NAME
from .review import DECISIONS_FILENAME, KEEP

logger = logging.getLogger("photo_pipeline.export")

EXPORT_MANIFEST_FIELDNAMES = ["rel_path", "chapter", "cluster_id", "exported_path", "decided_at"]
EXPORT_MANIFEST_FILENAME = "export_manifest.json"


def load_decisions(output_dir: Path) -> dict:
    path = output_dir / DECISIONS_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"No {DECISIONS_FILENAME} under {output_dir} — run `python -m photo_pipeline.review "
            f"--output {output_dir}` first and mark at least one photo 'keep'."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("decisions", {})


def kept_rel_paths(decisions: dict) -> list[str]:
    return sorted(rel for rel, info in decisions.items() if info.get("decision") == KEEP)


def _chapter_and_cluster(rel_path: str) -> tuple[str, str]:
    # <chapter>/<cluster_id>/[best|highlights/]<filename> — see output_layout.py
    parts = Path(rel_path).parts
    if len(parts) < 2:
        return "unknown", "unknown"
    return parts[0], parts[1]


def _unique_destination(dest_dir: Path, filename: str, claimed: set[str]) -> Path:
    """Picks a destination filename, disambiguating only against names
    already claimed *within this run* — not against whatever happens to
    already be on disk. A previous run's own output at the same path is
    expected to be overwritten in place (that's what makes re-exporting
    unchanged decisions idempotent instead of piling up __1, __2, ... every
    time); only two different source files landing on the same name in the
    same run need disambiguating.
    """
    if filename not in claimed:
        claimed.add(filename)
        return dest_dir / filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 1
    while True:
        candidate_name = f"{stem}__{n}{suffix}"
        if candidate_name not in claimed:
            claimed.add(candidate_name)
            return dest_dir / candidate_name
        n += 1


def _load_previous_exported_paths(dest_dir: Path) -> set[str]:
    manifest_path = dest_dir / EXPORT_MANIFEST_FILENAME
    if not manifest_path.exists():
        return set()
    try:
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Could not parse %s, treating this as a first export", manifest_path)
        return set()
    return {row["exported_path"] for row in rows if row.get("exported_path")}


def _prune_stale_files(stale_paths: set[str]) -> list[str]:
    removed = []
    for stale in stale_paths:
        path = Path(stale)
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed


def _prune_stale_chapter_dirs(dest_dir: Path, current_chapters: set[str]) -> list[str]:
    """Removes a chapter folder left over from a previous export once every
    photo that used to live in it has been pruned — leaving only a stale
    contact sheet, or nothing at all.
    """
    removed = []
    if not dest_dir.exists():
        return removed
    for child in dest_dir.iterdir():
        if not child.is_dir() or child.name in current_chapters:
            continue
        remaining = list(child.iterdir())
        if all(p.name == CONTACT_SHEET_NAME for p in remaining):
            for p in remaining:
                p.unlink()
            child.rmdir()
            removed.append(str(child))
    return removed


def run_export(
    output_dir: Path,
    dest_dir: Path,
    *,
    contact_sheets: bool = True,
) -> list[dict]:
    decisions = load_decisions(output_dir)
    kept = kept_rel_paths(decisions)
    previous_exported_paths = _load_previous_exported_paths(dest_dir)

    if not kept:
        logger.warning(
            "No photos marked 'keep' yet in %s — nothing to export.", output_dir / DECISIONS_FILENAME
        )
        removed = _prune_stale_files(previous_exported_paths)
        _prune_stale_chapter_dirs(dest_dir, current_chapters=set())
        if removed:
            logger.info("Removed %d previously-exported file(s) now that nothing is kept", len(removed))
        return []

    exported: list[dict] = []
    by_chapter: dict[str, list[Path]] = {}
    claimed_by_chapter: dict[str, set[str]] = {}

    for rel_path in kept:
        source = output_dir / rel_path
        if not source.is_file():
            logger.warning("Decision references a file that no longer exists, skipping: %s", rel_path)
            continue

        chapter, cluster_id = _chapter_and_cluster(rel_path)
        chapter_dir = dest_dir / chapter
        chapter_dir.mkdir(parents=True, exist_ok=True)

        # Prefix with cluster_id: two different clusters can easily share a
        # camera-assigned filename (IMG_1234.jpg), and the prefix is also a
        # free date/event breadcrumb for the later captioning/map phases.
        claimed = claimed_by_chapter.setdefault(chapter, set())
        dest_path = _unique_destination(chapter_dir, f"{cluster_id}_{source.name}", claimed)
        shutil.copy2(source, dest_path)

        exported.append(
            {
                "rel_path": rel_path,
                "chapter": chapter,
                "cluster_id": cluster_id,
                "exported_path": str(dest_path),
                "decided_at": decisions[rel_path].get("decided_at"),
            }
        )
        by_chapter.setdefault(chapter, []).append(dest_path)

    if contact_sheets:
        for chapter, paths in by_chapter.items():
            generate_contact_sheet(sorted(paths), dest_dir / chapter / "_contact_sheet.jpg")

    current_exported_paths = {row["exported_path"] for row in exported}
    stale_paths = previous_exported_paths - current_exported_paths
    removed = _prune_stale_files(stale_paths)
    removed += _prune_stale_chapter_dirs(dest_dir, current_chapters=set(by_chapter))
    if removed:
        logger.info("Removed %d file(s)/folder(s) no longer part of the current keep-set: %s", len(removed), removed)

    dest_dir.mkdir(parents=True, exist_ok=True)
    with open(dest_dir / "export_manifest.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_MANIFEST_FIELDNAMES)
        writer.writeheader()
        writer.writerows(exported)
    (dest_dir / EXPORT_MANIFEST_FILENAME).write_text(json.dumps(exported, indent=2), encoding="utf-8")

    logger.info("Exported %d kept photo(s) across %d chapter(s) to %s", len(exported), len(by_chapter), dest_dir)
    return exported


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, type=Path, help="Pass 1 output folder that was reviewed (contains review_decisions.json)")
    parser.add_argument("--dest", required=True, type=Path, help="Where to copy the final 'keep' photos, grouped by chapter")
    parser.add_argument("--no-contact-sheets", action="store_true", help="Skip generating a contact sheet per exported chapter")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    run_export(args.output, args.dest, contact_sheets=not args.no_contact_sheets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
