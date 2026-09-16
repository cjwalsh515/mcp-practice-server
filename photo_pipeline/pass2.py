"""Pass 2: Claude vision as a tiebreaker within pass 1's surviving clusters.

    python -m photo_pipeline.pass2 --output ~/culled

Only ever looks at the clusters pass1.py already narrowed down (typically
5-10 candidates each) — never the whole library. For each cluster with
enough candidates to be worth comparing, sends the (downscaled) photos to
Claude and asks it to pick the best 1-2 for composition, expressions, and
eyes-open, then copies the picks into a `best/` subfolder and records the
choice + reasoning back into the manifest.

Requires ANTHROPIC_API_KEY (or an `ant auth login` profile) unless
--dry-run is passed.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from PIL import Image

from . import heic_support  # noqa: F401
from .discovery import DEFAULT_EXTENSIONS
from .manifest import Manifest

logger = logging.getLogger("photo_pipeline.pass2")

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MIN_CANDIDATES = 3
DEFAULT_MAX_CANDIDATES = 10
DEFAULT_TOP_N = 2
DEFAULT_MAX_DIMENSION = 1280
REQUEST_PAUSE_SECONDS = 0.5

_PROMPT_TEMPLATE = """\
You are helping shortlist photos for a printed anniversary photobook covering a \
couple's relationship. Below are {count} photos from the same moment/event, each \
labeled with a filename.

Compare them and pick the best {top_n} (or fewer, if fewer are actually good) based on:
- composition and framing
- expressions (genuine, flattering, not mid-blink)
- eyes open, in focus on the subject
- overall "would this go in a printed photobook" quality

Respond with ONLY a JSON object, no other text, no markdown fences:
{{"best": ["<filename>", ...], "reasoning": "<one or two sentences>"}}

The filenames you may choose from are: {filenames}
"""


def _find_manifest(output_dir: Path) -> Path:
    path = output_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No manifest.json under {output_dir} — run pass1 first."
        )
    return path


def _discover_cluster_dirs(output_dir: Path) -> list[Path]:
    """Cluster dirs are two levels down: <output>/<chapter_label>/<cluster_id>/."""
    dirs = []
    for chapter_dir in output_dir.iterdir():
        if not chapter_dir.is_dir():
            continue
        for cluster_dir in chapter_dir.iterdir():
            if cluster_dir.is_dir():
                dirs.append(cluster_dir)
    return sorted(dirs)


def _cluster_images(cluster_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in cluster_dir.iterdir()
        if p.is_file() and p.suffix.lower() in DEFAULT_EXTENSIONS and not p.name.startswith("_")
    )


def _encode_image(path: Path, *, max_dimension: int) -> tuple[str, str]:
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_dimension, max_dimension))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
    return data, "image/jpeg"


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _ask_claude_for_best(
    client, model: str, images: list[Path], *, top_n: int, max_dimension: int
) -> Optional[dict]:
    import anthropic

    content = []
    for path in images:
        data, media_type = _encode_image(path, max_dimension=max_dimension)
        content.append({"type": "text", "text": f"Filename: {path.name}"})
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            }
        )

    prompt = _PROMPT_TEMPLATE.format(
        count=len(images),
        top_n=top_n,
        filenames=", ".join(p.name for p in images),
    )
    content.append({"type": "text", "text": prompt})

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.RateLimitError as exc:
        retry_after = int(exc.response.headers.get("retry-after", "30")) if exc.response else 30
        logger.warning("Rate limited, sleeping %ss", retry_after)
        time.sleep(retry_after)
        return _ask_claude_for_best(client, model, images, top_n=top_n, max_dimension=max_dimension)
    except anthropic.APIStatusError as exc:
        logger.error("API error for cluster (%s): %s", [p.name for p in images], exc)
        return None
    except anthropic.APIConnectionError as exc:
        logger.error("Connection error for cluster (%s): %s", [p.name for p in images], exc)
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    parsed = _extract_json(text)
    if parsed is None or "best" not in parsed:
        logger.warning("Could not parse a selection out of Claude's response: %r", text[:300])
        return None
    return parsed


def process_cluster_dir(
    cluster_dir: Path,
    *,
    client,
    model: str,
    top_n: int,
    max_dimension: int,
    min_candidates: int,
    max_candidates: int,
    manifest_by_output_path: dict[str, "PhotoRecord"],  # noqa: F821
    dry_run: bool,
) -> None:
    images = _cluster_images(cluster_dir)
    if len(images) < min_candidates:
        return
    if len(images) > max_candidates:
        logger.warning(
            "Skipping %s: %d candidates exceeds --max-candidates=%d "
            "(tighten pass1 --dedup-distance/--blur-threshold instead of widening this)",
            cluster_dir,
            len(images),
            max_candidates,
        )
        return

    if dry_run:
        logger.info("[dry-run] Would compare %d photos in %s", len(images), cluster_dir)
        return

    result = _ask_claude_for_best(client, model, images, top_n=top_n, max_dimension=max_dimension)
    if result is None:
        return

    best_names = set(result.get("best", []))
    reasoning = result.get("reasoning", "")
    valid_names = {p.name for p in images}
    unknown = best_names - valid_names
    if unknown:
        logger.warning("Claude picked filenames not in the cluster, ignoring: %s", unknown)
        best_names &= valid_names

    if not best_names:
        return

    best_dir = cluster_dir / "best"
    best_dir.mkdir(exist_ok=True)

    for path in images:
        record = manifest_by_output_path.get(str(path))
        selected = path.name in best_names
        if selected:
            shutil.copy2(path, best_dir / path.name)
        if record is not None:
            record.pass2_selected = selected
            record.pass2_notes = reasoning if selected else record.pass2_notes

    time.sleep(REQUEST_PAUSE_SECONDS)


def run_pass2(
    output_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    top_n: int = DEFAULT_TOP_N,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    min_candidates: int = DEFAULT_MIN_CANDIDATES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    dry_run: bool = False,
) -> Manifest:
    manifest_path = _find_manifest(output_dir)
    manifest = Manifest.read_json(manifest_path)
    manifest_by_output_path = {r.output_path: r for r in manifest.records if r.output_path}

    client = None
    if not dry_run:
        import anthropic

        client = anthropic.Anthropic()

    cluster_dirs = _discover_cluster_dirs(output_dir)
    logger.info("Found %d cluster folders under %s", len(cluster_dirs), output_dir)

    for cluster_dir in cluster_dirs:
        process_cluster_dir(
            cluster_dir,
            client=client,
            model=model,
            top_n=top_n,
            max_dimension=max_dimension,
            min_candidates=min_candidates,
            max_candidates=max_candidates,
            manifest_by_output_path=manifest_by_output_path,
            dry_run=dry_run,
        )

    if not dry_run:
        manifest.write_csv(output_dir / "manifest.csv")
        manifest.write_json(output_dir / "manifest.json")

    return manifest


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, type=Path, help="Pass 1 output folder (contains manifest.json and cluster folders)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Best N photos to keep per cluster")
    parser.add_argument("--max-dimension", type=int, default=DEFAULT_MAX_DIMENSION, help="Downscale images to this longest edge before sending")
    parser.add_argument("--min-candidates", type=int, default=DEFAULT_MIN_CANDIDATES, help="Skip clusters with fewer surviving photos than this")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES, help="Skip (and warn about) clusters with more surviving photos than this")
    parser.add_argument("--dry-run", action="store_true", help="List what would be sent without calling the API")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    run_pass2(
        args.output,
        model=args.model,
        top_n=args.top_n,
        max_dimension=args.max_dimension,
        min_candidates=args.min_candidates,
        max_candidates=args.max_candidates,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
