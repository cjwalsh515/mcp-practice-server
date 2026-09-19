# Photo culling pipeline

Cuts a photo library (years of exports from Google Photos / iCloud / Gmail)
down to a realistic per-chapter shortlist for an anniversary photobook,
before any book-design work starts.

Stages:

1. **Pass 1** (`photo_pipeline/pass1.py`) — cheap heuristics only: EXIF-based
   event/trip clustering, perceptual-hash de-duplication of bursts,
   variance-of-Laplacian blur detection, and screenshot/non-camera
   filtering. Cuts the library by 70-90% before anything expensive runs.
2. **Pass 2** (`photo_pipeline/pass2.py`) — Claude vision as a technical-quality
   tiebreaker, run only on the small near-duplicate clusters pass 1 leaves
   behind, to pick the sharpest/cleanest 1-2 photos per burst.
3. **Pass 3** (`photo_pipeline/pass3.py`) — Claude vision again, but asking a
   different question across *every* cluster regardless of size: which 1-3
   photos are the most interesting or emotionally resonant for the book
   (composition, genuine expressions, candid moments), not just technically
   correct. Independent of pass 2 — works with or without it having run.
4. **Review** (`photo_pipeline/review.py`) — a fast, local, keyboard-driven
   web app (no network calls) for flipping through the curated output one
   photo at a time and marking keep/skip, with durable, resumable progress.
5. **Export** (`photo_pipeline/export.py`) — turns review's keep decisions
   into a handoff-ready folder, grouped by chapter, with a contact sheet
   per chapter and an export manifest. The actual finish line.

A separate script (`photo_pipeline/gmail_export.py`) downloads pre-2015
photos that only exist as Gmail attachments, so they can flow into the
same pipeline.

Non-destructive throughout: pass 1 only copies survivors into an output
folder alongside a manifest (CSV/JSON) and per-cluster contact sheets;
originals are never modified or deleted.

See [`docs/SETUP.md`](docs/SETUP.md) for exporting photos, setting up the
Gmail OAuth credentials, and running each stage.

```bash
pip install -r requirements.txt
python -m photo_pipeline.pass1 --input ~/photos --output ~/culled -v
python -m photo_pipeline.pass2 --output ~/culled -v   # needs ANTHROPIC_API_KEY
python -m photo_pipeline.pass3 --output ~/culled -v   # needs ANTHROPIC_API_KEY, independent of pass 2
python -m photo_pipeline.review --output ~/culled     # local only, no API key
python -m photo_pipeline.export --output ~/culled --dest ~/PhotoProject/final
```

Not in scope for this build: photobook layout, captions, and the
travel-map-with-photo-insets piece — those are later phases that will
consume this pipeline's shortlisted output.
