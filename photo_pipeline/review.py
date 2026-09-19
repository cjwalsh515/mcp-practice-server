"""Fast, local, keyboard-driven review of pass1 (and optionally pass2/pass3)
output — independent of pass1.py/pass2.py/pass3.py.

    python3 -m photo_pipeline.review --output ~/PhotoProject/2015/culled

No network calls, no API key, nothing but the Python standard library plus
Pillow (already a pipeline dependency). Opens your browser to a single big
photo at a time, one cluster at a time, using whichever layer is most
curated for that cluster: pass 3's ``highlights/`` if present, else pass
2's ``best/``, else every ``kept`` survivor pass 1 left behind.

Keys:
    Y                 keep for the book (advances)
    N                 skip (advances)
    -> / space        next, without deciding
    <- / Backspace/U  back (revisit a previous photo to change your mind)

Decisions are written to <output>/review_decisions.json after every
keystroke — a small, separate file rather than a new manifest column, so
closing the browser (or this process) never loses progress and never
fights pass2.py/pass3.py for a write to manifest.json.
"""

from __future__ import annotations

import argparse
import functools
import io
import json
import logging
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from PIL import Image

from . import heic_support  # noqa: F401
from .output_layout import discover_cluster_dirs, review_candidates

logger = logging.getLogger("photo_pipeline.review")

DEFAULT_PORT = 8765
DEFAULT_MAX_DIMENSION = 1600
DECISIONS_FILENAME = "review_decisions.json"

KEEP = "keep"
SKIP = "skip"
_VALID_DECISIONS = (KEEP, SKIP, None)


def build_review_items(output_dir: Path) -> list[dict]:
    items = []
    for cluster_dir in discover_cluster_dirs(output_dir):
        images, source = review_candidates(cluster_dir)
        for path in images:
            items.append(
                {
                    "rel_path": str(path.relative_to(output_dir)),
                    "chapter_label": cluster_dir.parent.name,
                    "cluster_id": cluster_dir.name,
                    "source": source,
                    "filename": path.name,
                }
            )
    return items


class DecisionsStore:
    """Durable keep/skip decisions + current position, separate from manifest.json."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.decisions: dict[str, dict] = {}
        self.current_index = 0
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.decisions = data.get("decisions", {})
            self.current_index = data.get("current_index", 0)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps({"decisions": self.decisions, "current_index": self.current_index}, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)  # atomic on POSIX and Windows

    def set_decision(self, rel_path: str, decision: Optional[str]) -> None:
        with self._lock:
            if decision is None:
                self.decisions.pop(rel_path, None)
            else:
                self.decisions[rel_path] = {
                    "decision": decision,
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                }
            self._save()

    def set_current_index(self, index: int) -> None:
        with self._lock:
            self.current_index = index
            self._save()


INDEX_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Photo review</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; height: 100vh; display: flex; flex-direction: column;
    background: #111; color: #eee; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  }
  header, footer {
    padding: 10px 16px; display: flex; justify-content: space-between; align-items: center;
    background: #1a1a1a; flex-shrink: 0; font-size: 14px; color: #aaa;
  }
  #main { flex: 1; display: flex; align-items: center; justify-content: center; overflow: hidden; position: relative; }
  #photo { max-width: 96vw; max-height: 80vh; border: 6px solid #333; border-radius: 4px; transition: border-color 0.1s; }
  #photo.keep { border-color: #2ecc71; }
  #photo.skip { border-color: #e74c3c; opacity: 0.5; }
  #status { font-weight: 600; letter-spacing: 0.05em; }
  #status.keep { color: #2ecc71; }
  #status.skip { color: #e74c3c; }
  #status.pending { color: #888; }
  .legend kbd {
    background: #2a2a2a; border: 1px solid #444; border-radius: 3px; padding: 1px 6px; margin: 0 2px; font-size: 12px;
  }
  #empty { margin: auto; font-size: 18px; color: #888; }
</style>
</head>
<body>
  <header>
    <span id="caption">Loading…</span>
    <span id="progress"></span>
  </header>
  <div id="main">
    <img id="photo" style="display:none">
    <div id="empty" style="display:none">No reviewable photos found.</div>
  </div>
  <footer>
    <span id="status" class="pending">undecided</span>
    <span class="legend">
      <kbd>Y</kbd> keep &nbsp; <kbd>N</kbd> skip &nbsp;
      <kbd>&rarr;</kbd>/<kbd>space</kbd> next &nbsp;
      <kbd>&larr;</kbd>/<kbd>backspace</kbd>/<kbd>U</kbd> back
    </span>
    <span id="stats"></span>
  </footer>
<script>
let items = [];
let decisions = {};
let index = 0;

async function loadState() {
  const res = await fetch('/api/items');
  const data = await res.json();
  items = data.items;
  decisions = data.decisions;
  index = items.length ? Math.min(Math.max(data.current_index || 0, 0), items.length - 1) : 0;
  render();
}

function currentItem() {
  return items[index];
}

function render() {
  const img = document.getElementById('photo');
  const empty = document.getElementById('empty');
  if (!items.length) {
    img.style.display = 'none';
    empty.style.display = 'block';
    document.getElementById('caption').textContent = '';
    document.getElementById('progress').textContent = '';
    document.getElementById('status').textContent = '';
    return;
  }
  const item = currentItem();
  img.style.display = 'block';
  empty.style.display = 'none';
  img.src = '/api/image?path=' + encodeURIComponent(item.rel_path);
  document.getElementById('caption').textContent =
    item.chapter_label + ' / ' + item.cluster_id + ' — ' + item.filename + ' (' + item.source + ')';
  document.getElementById('progress').textContent = (index + 1) + ' / ' + items.length;

  const d = decisions[item.rel_path];
  const status = document.getElementById('status');
  img.classList.remove('keep', 'skip');
  if (d && d.decision === 'keep') {
    status.textContent = 'KEPT'; status.className = 'keep'; img.classList.add('keep');
  } else if (d && d.decision === 'skip') {
    status.textContent = 'SKIPPED'; status.className = 'skip'; img.classList.add('skip');
  } else {
    status.textContent = 'undecided'; status.className = 'pending';
  }

  let kept = 0, skipped = 0;
  for (const key in decisions) {
    if (decisions[key].decision === 'keep') kept++;
    else if (decisions[key].decision === 'skip') skipped++;
  }
  document.getElementById('stats').textContent = 'kept: ' + kept + '   skipped: ' + skipped;

  persistIndex();
}

function persistIndex() {
  fetch('/api/index', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index: index }),
  });
}

function decide(decision) {
  const item = currentItem();
  if (!item) return;
  decisions[item.rel_path] = { decision: decision, decided_at: new Date().toISOString() };
  fetch('/api/decide', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rel_path: item.rel_path, decision: decision }),
  });
  advance();
}

function advance() {
  if (index < items.length - 1) index++;
  render();
}

function goBack() {
  if (index > 0) index--;
  render();
}

document.addEventListener('keydown', (e) => {
  switch (e.key) {
    case 'y': case 'Y': decide('keep'); break;
    case 'n': case 'N': decide('skip'); break;
    case 'ArrowRight': case ' ': e.preventDefault(); advance(); break;
    case 'ArrowLeft': case 'Backspace': case 'u': case 'U': e.preventDefault(); goBack(); break;
  }
});

loadState();
</script>
</body>
</html>
"""


class ReviewHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, output_dir: Path, items: list[dict], store: DecisionsStore, max_dimension: int, **kwargs):
        self.output_dir = output_dir
        self.items = items
        self.store = store
        self.max_dimension = max_dimension
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - matches base signature
        logger.debug("%s - %s", self.address_string(), format % args)

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def _resolve_rel_path(self, rel_path: str) -> Optional[Path]:
        if not rel_path:
            return None
        base = self.output_dir.resolve()
        candidate = (base / rel_path).resolve()
        if not (candidate == base or base in candidate.parents):
            return None
        if not candidate.is_file():
            return None
        return candidate

    def _send_image(self, path: Path) -> None:
        try:
            with Image.open(path) as img:
                img = img.convert("RGB")
                img.thumbnail((self.max_dimension, self.max_dimension))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=90)
                data = buf.getvalue()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to serve image %s: %s", path, exc)
            self.send_error(500, "Image error")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send_html(INDEX_HTML)
        elif parsed.path == "/api/items":
            self._send_json(
                {
                    "items": self.items,
                    "decisions": self.store.decisions,
                    "current_index": self.store.current_index,
                }
            )
        elif parsed.path == "/api/image":
            rel_path = parse_qs(parsed.query).get("path", [""])[0]
            resolved = self._resolve_rel_path(rel_path)
            if resolved is None:
                self.send_error(404, "Not found")
                return
            self._send_image(resolved)
        else:
            self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urlparse(self.path)
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, status=400)
            return

        if parsed.path == "/api/decide":
            rel_path = body.get("rel_path")
            decision = body.get("decision")
            if not rel_path or decision not in _VALID_DECISIONS:
                self._send_json({"error": "bad request"}, status=400)
                return
            self.store.set_decision(rel_path, decision)
            self._send_json({"ok": True})
        elif parsed.path == "/api/index":
            index = body.get("index")
            if not isinstance(index, int) or index < 0:
                self._send_json({"error": "bad request"}, status=400)
                return
            self.store.set_current_index(index)
            self._send_json({"ok": True})
        else:
            self.send_error(404, "Not found")


def build_server(
    output_dir: Path,
    *,
    port: int = DEFAULT_PORT,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
) -> tuple[ThreadingHTTPServer, list[dict], DecisionsStore]:
    """Constructs (but does not start) the review server. Split out from
    run_server() so tests can bind an ephemeral port, drive a few requests,
    and shut it down without going through serve_forever()/webbrowser.
    """
    items = build_review_items(output_dir)
    if not items:
        logger.warning("No reviewable photos found under %s — run pass1 first.", output_dir)

    store = DecisionsStore(output_dir / DECISIONS_FILENAME)
    if items and store.current_index >= len(items):
        store.current_index = 0

    handler_factory = functools.partial(
        ReviewHandler, output_dir=output_dir, items=items, store=store, max_dimension=max_dimension
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_factory)
    return server, items, store


def run_server(
    output_dir: Path,
    *,
    port: int = DEFAULT_PORT,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    open_browser: bool = True,
) -> None:
    server, items, store = build_server(output_dir, port=port, max_dimension=max_dimension)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"

    logger.info("%d photos to review under %s", len(items), output_dir)
    logger.info("Review server running at %s — press Ctrl+C to stop.", url)

    if open_browser:
        def _open():
            time.sleep(0.4)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("Decisions saved to %s", store.path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, type=Path, help="Pass 1 output folder to review")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (0 picks a free port); default %(default)s")
    parser.add_argument("--max-dimension", type=int, default=DEFAULT_MAX_DIMENSION, help="Downscale served images to this longest edge")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser tab")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    run_server(
        args.output,
        port=args.port,
        max_dimension=args.max_dimension,
        open_browser=not args.no_browser,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
