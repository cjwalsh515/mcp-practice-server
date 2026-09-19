"""Pass 3: highlight extraction — "which photo(s) from this event actually
belong in the book," independent of pass1.py/pass2.py.

    python -m photo_pipeline.pass3 --output ~/culled

Where pass2.py only fires on near-duplicate bursts and tie-breaks on
technical quality ("which of these is sharpest"), pass3 runs across
*every* cluster in a pass1 output folder — regardless of size or how
similar the photos in it are — and asks a different question: which 1-3
photos are the most interesting or emotionally resonant for a printed
relationship photobook (composition, genuine/candid expressions, people
engaged with each other or the moment), not just which is technically
correct.

Works whether or not pass2.py has already run on the folder: if a
cluster's ``best/`` subfolder exists, those (already technical-quality
filtered) picks are used as candidates; otherwise every ``kept`` survivor
in the cluster is a candidate. Selections are copied into a ``highlights/``
subfolder per cluster, and logged back to the manifest as
``pass3_selected``/``pass3_notes`` — the same pattern pass2 uses for
``pass2_selected``/``pass2_notes``.

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
from .manifest import Manifest, PhotoRecord
from .output_layout import HIGHLIGHTS_DIRNAME, best_candidates, discover_cluster_dirs

logger = logging.getLogger("photo_pipeline.pass3")

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_TOP_N = 2
DEFAULT_MIN_CANDIDATES = 1
DEFAULT_MAX_CANDIDATES = 15
DEFAULT_MAX_DIMENSION = 1280
REQUEST_PAUSE_SECONDS = 0.5

AUTO_SELECT_NOTE = "only surviving candidate in this cluster; selected by default (no vision call needed)"

_PROMPT_TEMPLATE = """\
You are shortlisting photos from ONE event/moment for a printed anniversary \
photobook covering a couple's relationship. Below are {count} photos from \
this event, each labeled with a filename.

This is NOT a technical-quality check — ignore minor softness, awkward \
lighting, or a slightly off crop if the moment itself is worth keeping. \
Instead, judge which photo(s) are the most interesting or emotionally \
resonant:
- composition and storytelling, not just correctness
- genuine, candid expressions — not posed or mid-blink
- people actually engaged with each other or the moment, not just facing \
the camera
- a moment worth remembering in a book about this relationship

Pick the best {top_n} (or fewer, if the event genuinely doesn't have that
many worth highlighting — a single strong photo beats padding to {top_n}).

Respond with ONLY a JSON object, no other text, no markdown fences:
{{"picks": ["<filename>", ...], "reasoning": "<one or two sentences>"}}

The filenames you may choose from are: {filenames}
"""


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


def _ask_claude_for_highlights(
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
        return _ask_claude_for_highlights(client, model, images, top_n=top_n, max_dimension=max_dimension)
    except anthropic.APIStatusError as exc:
        logger.error("API error for cluster (%s): %s", [p.name for p in images], exc)
        return None
    except anthropic.APIConnectionError as exc:
        logger.error("Connection error for cluster (%s): %s", [p.name for p in images], exc)
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    parsed = _extract_json(text)
    if parsed is None or "picks" not in parsed:
        logger.warning("Could not parse a selection out of Claude's response: %r", text[:300])
        return None
    return parsed


def _apply_selection(
    cluster_dir: Path,
    images: list[Path],
    picks: set[str],
    reasoning: str,
    *,
    records_by_filename: dict[str, "PhotoRecord"],
    dry_run: bool,
) -> None:
    if not picks:
        return

    highlights_dir = cluster_dir / HIGHLIGHTS_DIRNAME
    if not dry_run:
        highlights_dir.mkdir(exist_ok=True)

    for path in images:
        selected = path.name in picks
        if selected and not dry_run:
            shutil.copy2(path, highlights_dir / path.name)
        record = records_by_filename.get(path.name)
        if record is not None and not dry_run:
            record.pass3_selected = selected
            if selected:
                record.pass3_notes = reasoning


def process_cluster_dir(
    cluster_dir: Path,
    *,
    client,
    model: str,
    top_n: int,
    max_dimension: int,
    min_candidates: int,
    max_candidates: int,
    records_by_cluster_and_filename: dict[tuple[str, str], "PhotoRecord"],
    dry_run: bool,
) -> None:
    images, source = best_candidates(cluster_dir)
    cluster_id = cluster_dir.name
    records_by_filename = {
        filename: record
        for (cid, filename), record in records_by_cluster_and_filename.items()
        if cid == cluster_id
    }

    if len(images) < min_candidates:
        logger.debug("Skipping %s: %d candidates < --min-candidates=%d", cluster_dir, len(images), min_candidates)
        return

    if len(images) == 1:
        # Nothing to compare — the one surviving candidate is trivially "the highlight."
        logger.info("[%s] only one candidate (%s) — auto-selecting, no vision call", cluster_dir, images[0].name)
        _apply_selection(
            cluster_dir,
            images,
            {images[0].name},
            AUTO_SELECT_NOTE,
            records_by_filename=records_by_filename,
            dry_run=dry_run,
        )
        return

    if len(images) > max_candidates:
        logger.warning(
            "Skipping %s: %d candidates exceeds --max-candidates=%d "
            "(this shouldn't normally happen after pass1's own cuts — check --dedup-distance/--blur-threshold there)",
            cluster_dir,
            len(images),
            max_candidates,
        )
        return

    if dry_run:
        logger.info("[dry-run] Would evaluate %d %s-sourced photos in %s for highlights", len(images), source, cluster_dir)
        return

    result = _ask_claude_for_highlights(client, model, images, top_n=top_n, max_dimension=max_dimension)
    if result is None:
        return

    picks = set(result.get("picks", []))
    reasoning = result.get("reasoning", "")
    valid_names = {p.name for p in images}
    unknown = picks - valid_names
    if unknown:
        logger.warning("Claude picked filenames not in the cluster, ignoring: %s", unknown)
        picks &= valid_names

    _apply_selection(cluster_dir, images, picks, reasoning, records_by_filename=records_by_filename, dry_run=dry_run)
    time.sleep(REQUEST_PAUSE_SECONDS)


def _find_manifest(output_dir: Path) -> Path:
    path = output_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"No manifest.json under {output_dir} — run pass1 first.")
    return path


def run_pass3(
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
    records_by_cluster_and_filename = {
        (r.cluster_id, Path(r.output_path).name): r
        for r in manifest.records
        if r.output_path and r.cluster_id
    }

    client = None
    if not dry_run:
        import anthropic

        client = anthropic.Anthropic()

    cluster_dirs = discover_cluster_dirs(output_dir)
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
            records_by_cluster_and_filename=records_by_cluster_and_filename,
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
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Highlight up to N photos per cluster (default: %(default)s)")
    parser.add_argument("--max-dimension", type=int, default=DEFAULT_MAX_DIMENSION, help="Downscale images to this longest edge before sending")
    parser.add_argument("--min-candidates", type=int, default=DEFAULT_MIN_CANDIDATES, help="Skip clusters with fewer surviving photos than this (default: %(default)s, i.e. never skip)")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES, help="Skip (and warn about) clusters with more surviving photos than this")
    parser.add_argument("--dry-run", action="store_true", help="List what would be sent/selected without calling the API or writing files")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    run_pass3(
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
