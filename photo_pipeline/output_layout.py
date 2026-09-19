"""Shared helpers for walking a pass1 output folder's on-disk layout.

pass1.py writes ``<output>/<chapter-or-year-month>/<cluster-id>/`` folders;
pass2.py adds a ``best/`` subfolder inside clusters it processed. This
module gives pass3.py and review.py a single place to agree on that layout
— and on which subfolder of candidates to prefer — without importing (or
needing to modify) pass1.py/pass2.py themselves.
"""

from __future__ import annotations

from pathlib import Path

from .discovery import DEFAULT_EXTENSIONS

CONTACT_SHEET_NAME = "_contact_sheet.jpg"
BEST_DIRNAME = "best"
HIGHLIGHTS_DIRNAME = "highlights"

# Subfolders that hold *derived* selections rather than original cluster
# candidates — never treat these as a cluster in their own right.
_DERIVED_DIRNAMES = {BEST_DIRNAME, HIGHLIGHTS_DIRNAME}


def discover_cluster_dirs(output_dir: Path) -> list[Path]:
    """Cluster dirs are two levels down: <output>/<chapter_label>/<cluster_id>/."""
    dirs = []
    for chapter_dir in sorted(output_dir.iterdir()):
        if not chapter_dir.is_dir():
            continue
        for cluster_dir in sorted(chapter_dir.iterdir()):
            if cluster_dir.is_dir() and cluster_dir.name not in _DERIVED_DIRNAMES:
                dirs.append(cluster_dir)
    return dirs


def cluster_images(directory: Path, *, extensions: set[str] = DEFAULT_EXTENSIONS) -> list[Path]:
    """Image files directly inside ``directory`` (non-recursive), skipping
    contact sheets and any derived subfolders (best/, highlights/).
    """
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith("_")
    )


def best_candidates(cluster_dir: Path) -> tuple[list[Path], str]:
    """Picks the highest-priority candidate set for a cluster.

    Prefers pass 2's ``best/`` picks (already technical-quality-filtered)
    over the raw ``kept`` survivors directly in the cluster folder, since
    that's the smaller, more relevant set to spend a vision call on. Falls
    back to the kept survivors when pass 2 hasn't run for this cluster.

    Returns (image_paths, source_label) where source_label is "best" or
    "kept".
    """
    best_dir = cluster_dir / BEST_DIRNAME
    if best_dir.is_dir():
        best_images = cluster_images(best_dir)
        if best_images:
            return best_images, "best"
    return cluster_images(cluster_dir), "kept"


def review_candidates(cluster_dir: Path) -> tuple[list[Path], str]:
    """Like :func:`best_candidates`, but also prefers pass 3's ``highlights/``
    picks above everything else — used by review.py, which wants the most
    curated set available regardless of which passes have run.

    Returns (image_paths, source_label) where source_label is "highlights",
    "best", or "kept".
    """
    highlights_dir = cluster_dir / HIGHLIGHTS_DIRNAME
    if highlights_dir.is_dir():
        highlight_images = cluster_images(highlights_dir)
        if highlight_images:
            return highlight_images, "highlights"
    return best_candidates(cluster_dir)
