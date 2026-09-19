"""Exports review.py's keep decisions into a handoff-ready folder.

    python3 -m photo_pipeline.export --output ~/culled --dest ~/PhotoProject/final

This is the actual finish line for the culling pipeline: reads
``<output>/review_decisions.json`` (written by review.py) and copies every
photo marked "keep" into ``--dest``, grouped by chapter, with a contact
sheet per chapter and a manifest of exactly what got exported — the
folder you hand off to Mixbook.

Independent of pass1.py/pass2.py/pass3.py/review.py; never modifies
--output or any original file, only ever copies out of it.
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
from .review import DECISIONS_FILENAME, KEEP

logger = logging.getLogger("photo_pipeline.export")

EXPORT_MANIFEST_FIELDNAMES = ["rel_path", "chapter", "cluster_id", "exported_path", "decided_at"]


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


def _unique_destination(dest_dir: Path, filename: str) -> Path:
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 1
    while True:
        candidate = dest_dir / f"{stem}__{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def run_export(
    output_dir: Path,
    dest_dir: Path,
    *,
    contact_sheets: bool = True,
) -> list[dict]:
    decisions = load_decisions(output_dir)
    kept = kept_rel_paths(decisions)
    if not kept:
        logger.warning(
            "No photos marked 'keep' yet in %s — nothing to export.", output_dir / DECISIONS_FILENAME
        )
        return []

    exported: list[dict] = []
    by_chapter: dict[str, list[Path]] = {}

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
        dest_path = _unique_destination(chapter_dir, f"{cluster_id}_{source.name}")
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

    dest_dir.mkdir(parents=True, exist_ok=True)
    with open(dest_dir / "export_manifest.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_MANIFEST_FIELDNAMES)
        writer.writeheader()
        writer.writerows(exported)
    (dest_dir / "export_manifest.json").write_text(json.dumps(exported, indent=2), encoding="utf-8")

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
