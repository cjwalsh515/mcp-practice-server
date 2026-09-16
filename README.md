# Photo culling pipeline

Cuts a photo library (years of exports from Google Photos / iCloud / Gmail)
down to a realistic per-chapter shortlist for an anniversary photobook,
before any book-design work starts.

Two passes:

1. **Pass 1** (`photo_pipeline/pass1.py`) — cheap heuristics only: EXIF-based
   event/trip clustering, perceptual-hash de-duplication of bursts,
   variance-of-Laplacian blur detection, and screenshot/non-camera
   filtering. Cuts the library by 70-90% before anything expensive runs.
2. **Pass 2** (`photo_pipeline/pass2.py`) — Claude vision as a tiebreaker,
   run only on the small clusters pass 1 leaves behind, to pick the best
   1-2 photos per event.

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
```

Not in scope for this build: photobook layout, captions, and the
travel-map-with-photo-insets piece — those are later phases that will
consume this pipeline's shortlisted output.
