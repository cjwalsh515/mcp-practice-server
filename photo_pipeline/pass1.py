"""Pass 1: cheap heuristics only. No AI calls.

    python -m photo_pipeline.pass1 --input ~/photos/2018 --output ~/culled

Reads EXIF, clusters into events/trips, drops screenshots and non-camera
images, flags blurry shots, collapses near-duplicate bursts down to the
sharpest frame, and copies survivors into per-chapter/per-cluster output
folders alongside a manifest and contact sheets. Originals are never
modified or deleted.

Processes one year at a time internally (see discovery.bucket_by_year) so a
run over years of photos stays memory-manageable and resumable — pass
--years to restrict a run to specific years.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from . import chapters as chapters_mod
from .blur import DEFAULT_BLUR_THRESHOLD, compute_blur_score, is_blurry
from .clustering import DEFAULT_GAP_HOURS, DEFAULT_GPS_JUMP_KM, Cluster, cluster_by_time
from .contact_sheet import generate_contact_sheet
from .discovery import bucket_by_year, find_images
from .exif_utils import PhotoMetadata
from .hashing import DEFAULT_MAX_DISTANCE, compute_phash, group_near_duplicates
from .manifest import (
    STATUS_BLURRY,
    STATUS_DUPLICATE,
    STATUS_ERROR,
    STATUS_JUNK,
    STATUS_KEPT,
    STATUS_SCREENSHOT,
    Manifest,
    PhotoRecord,
)
from .screenshot_filter import classify_screenshot

logger = logging.getLogger("photo_pipeline.pass1")


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


def process_cluster(
    cluster: Cluster,
    *,
    output_root: Path,
    chapters: Optional[list[chapters_mod.ChapterRange]],
    blur_threshold: float,
    dedup_distance: int,
    screenshot_threshold: int,
    contact_sheets: bool,
    manifest: Manifest,
) -> None:
    label = chapters_mod.label_for_cluster(cluster, chapters)
    dest_dir = output_root / label / cluster.cluster_id

    candidates: dict[Path, PhotoMetadata] = {}
    blur_scores: dict[Path, float] = {}

    for meta in cluster.photos:
        if meta.error:
            manifest.add(
                PhotoRecord(
                    original_path=str(meta.path),
                    status=STATUS_ERROR,
                    reason=meta.error,
                    cluster_id=cluster.cluster_id,
                    chapter_label=label,
                )
            )
            continue

        verdict = classify_screenshot(meta, threshold=screenshot_threshold)
        if verdict.is_screenshot:
            manifest.add(
                PhotoRecord(
                    original_path=str(meta.path),
                    status=STATUS_SCREENSHOT,
                    reason=",".join(verdict.reasons),
                    cluster_id=cluster.cluster_id,
                    chapter_label=label,
                    datetime=str(meta.datetime) if meta.datetime else None,
                    datetime_source=meta.datetime_source,
                )
            )
            continue

        try:
            blur_score = compute_blur_score(meta.path)
        except Exception as exc:  # noqa: BLE001
            manifest.add(
                PhotoRecord(
                    original_path=str(meta.path),
                    status=STATUS_ERROR,
                    reason=f"blur scoring failed: {exc}",
                    cluster_id=cluster.cluster_id,
                    chapter_label=label,
                )
            )
            continue

        if is_blurry(blur_score, threshold=blur_threshold):
            manifest.add(
                PhotoRecord(
                    original_path=str(meta.path),
                    status=STATUS_BLURRY,
                    reason=f"blur_score={blur_score:.1f} < threshold={blur_threshold}",
                    cluster_id=cluster.cluster_id,
                    chapter_label=label,
                    datetime=str(meta.datetime) if meta.datetime else None,
                    datetime_source=meta.datetime_source,
                    blur_score=blur_score,
                    lat=meta.lat,
                    lon=meta.lon,
                )
            )
            continue

        candidates[meta.path] = meta
        blur_scores[meta.path] = blur_score

    if not candidates:
        return

    phashes = {}
    for path in candidates:
        try:
            phashes[path] = compute_phash(path)
        except Exception as exc:  # noqa: BLE001
            manifest.add(
                PhotoRecord(
                    original_path=str(path),
                    status=STATUS_ERROR,
                    reason=f"hashing failed: {exc}",
                    cluster_id=cluster.cluster_id,
                    chapter_label=label,
                )
            )

    hashable_paths = list(phashes.keys())
    groups = group_near_duplicates(
        {p: phashes[p] for p in hashable_paths}, max_distance=dedup_distance
    )

    dest_dir_created = False
    kept_paths: list[Path] = []

    for group in groups:
        # Keep the sharpest frame in each near-duplicate group.
        representative = max(group, key=lambda p: blur_scores.get(p, 0.0))
        for path in group:
            meta = candidates[path]
            if path == representative:
                if not dest_dir_created:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest_dir_created = True
                dest_path = _unique_destination(dest_dir, path.name)
                shutil.copy2(path, dest_path)
                kept_paths.append(dest_path)
                manifest.add(
                    PhotoRecord(
                        original_path=str(path),
                        status=STATUS_KEPT,
                        reason="survived pass 1",
                        cluster_id=cluster.cluster_id,
                        chapter_label=label,
                        output_path=str(dest_path),
                        datetime=str(meta.datetime) if meta.datetime else None,
                        datetime_source=meta.datetime_source,
                        lat=meta.lat,
                        lon=meta.lon,
                        blur_score=blur_scores.get(path),
                        phash=str(phashes[path]),
                    )
                )
            else:
                manifest.add(
                    PhotoRecord(
                        original_path=str(path),
                        status=STATUS_DUPLICATE,
                        reason=f"near-duplicate of {representative.name} (phash distance <= {dedup_distance})",
                        cluster_id=cluster.cluster_id,
                        chapter_label=label,
                        datetime=str(meta.datetime) if meta.datetime else None,
                        datetime_source=meta.datetime_source,
                        blur_score=blur_scores.get(path),
                        phash=str(phashes[path]),
                        duplicate_of=str(representative),
                    )
                )

    if contact_sheets and kept_paths:
        generate_contact_sheet(kept_paths, dest_dir / "_contact_sheet.jpg")


def run_pass1(
    input_dir: Path,
    output_dir: Path,
    *,
    chapters_path: Optional[Path] = None,
    gap_hours: float = DEFAULT_GAP_HOURS,
    gps_jump_km: Optional[float] = DEFAULT_GPS_JUMP_KM,
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
    dedup_distance: int = DEFAULT_MAX_DISTANCE,
    screenshot_threshold: int = 2,
    years: Optional[set[int]] = None,
    contact_sheets: bool = True,
) -> Manifest:
    chapters = chapters_mod.load_chapters(chapters_path) if chapters_path else None

    manifest = Manifest()
    paths = find_images(input_dir)
    logger.info("Found %d candidate image files under %s", len(paths), input_dir)

    year_buckets = bucket_by_year(paths, years=years)

    for year, metas in year_buckets.items():
        logger.info("Processing year %s: %d photos", year, len(metas))
        clusters = cluster_by_time(metas, gap_hours=gap_hours, gps_jump_km=gps_jump_km)
        for cluster in tqdm(clusters, desc=f"{year} clusters", unit="cluster"):
            process_cluster(
                cluster,
                output_root=output_dir,
                chapters=chapters,
                blur_threshold=blur_threshold,
                dedup_distance=dedup_distance,
                screenshot_threshold=screenshot_threshold,
                contact_sheets=contact_sheets,
                manifest=manifest,
            )

    manifest.write_csv(output_dir / "manifest.csv")
    manifest.write_json(output_dir / "manifest.json")

    summary = manifest.summary()
    logger.info("Pass 1 summary: %s", summary)
    return manifest


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path, help="Folder of exported photos (searched recursively)")
    parser.add_argument("--output", required=True, type=Path, help="Where to write culled/clustered output")
    parser.add_argument("--chapters", type=Path, default=None, help="Optional chapter date-range YAML config")
    parser.add_argument("--gap-hours", type=float, default=DEFAULT_GAP_HOURS, help="New cluster after this many hours of silence")
    parser.add_argument("--gps-jump-km", type=float, default=DEFAULT_GPS_JUMP_KM, help="New cluster if GPS jumps this far even within the time gap; 0 disables")
    parser.add_argument("--blur-threshold", type=float, default=DEFAULT_BLUR_THRESHOLD, help="Below this variance-of-Laplacian score, a photo is flagged blurry")
    parser.add_argument("--dedup-distance", type=int, default=DEFAULT_MAX_DISTANCE, help="Max phash hamming distance to treat two photos as the same burst")
    parser.add_argument("--screenshot-threshold", type=int, default=2, help="Score threshold (see screenshot_filter.py) to flag an image as a screenshot")
    parser.add_argument("--years", type=str, default=None, help="Comma-separated list of years to process, e.g. 2018,2019 (default: all)")
    parser.add_argument("--no-contact-sheets", action="store_true", help="Skip generating contact sheets")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    years = {int(y) for y in args.years.split(",")} if args.years else None
    gps_jump_km = None if args.gps_jump_km == 0 else args.gps_jump_km

    run_pass1(
        args.input,
        args.output,
        chapters_path=args.chapters,
        gap_hours=args.gap_hours,
        gps_jump_km=gps_jump_km,
        blur_threshold=args.blur_threshold,
        dedup_distance=args.dedup_distance,
        screenshot_threshold=args.screenshot_threshold,
        years=years,
        contact_sheets=not args.no_contact_sheets,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
