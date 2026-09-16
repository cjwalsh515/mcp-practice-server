# Setup

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. Export your photos

This pipeline never reaches into your phone or cloud accounts (except
Gmail, see below) — it only reads a local folder. Export first, with
**original files**, not compressed/resized copies (pass 1 depends on
EXIF timestamp + GPS, which compressed exports sometimes strip):

- **Google Photos**: [Google Takeout](https://takeout.google.com) →
  select Photos → export by date range if the whole library is huge.
- **iCloud Photos**: [icloud.com/photos](https://icloud.com/photos) →
  select + download, or on a Mac, Photos app → File → Export → Export
  Unmodified Original.

Organizing into per-year folders up front makes the `--years` batching
flag below more useful, but pass 1 also handles a flat folder fine — it
buckets by year internally either way.

## 3. (Optional) Pre-2015 photos from Gmail

iCloud only goes back to 2015. Older photos may live scattered across
Gmail as attachments. This needs its own Google Cloud OAuth client,
because downloading attachment bytes requires calling the Gmail API's
`users.messages.attachments.get` directly — not something a lightweight
Gmail connection can do.

1. In the [Google Cloud Console](https://console.cloud.google.com/),
   create a project (or reuse one), enable the **Gmail API**, and create
   an **OAuth client ID** of type "Desktop app". Download the client
   secret JSON.
2. Run:

   ```bash
   python -m photo_pipeline.gmail_export \
       --credentials client_secret.json \
       --output ~/photos/pre-2015 \
       --dry-run -v
   ```

   The first run opens a browser to authorize; the token is cached in
   `~/photos/pre-2015/gmail_token.json` for later runs. `--dry-run`
   lists what would be downloaded without saving anything — use it to
   sanity-check the query before committing to a real run (the default
   query already matches the ~200 candidate threads mentioned in the
   project brief; expect real noise in that count — some threads are
   documents that happen to share a thread with an unrelated photo).
3. Drop `--dry-run` to actually download. Attachments are named
   `<date>_from-<sender>_<original-filename>`, using the email's own
   date as a fallback and preferring real EXIF only when it's present
   and plausible (email pipelines frequently strip EXIF). To use real
   names instead of email-derived slugs (`bcoughlan` etc.), pass
   `--sender-names names.json` with a file like:

   ```json
   { "kemplaurajean@gmail.com": "mom", "Maryokemp@gmail.com": "aunt-mary" }
   ```

4. The run is resumable — `gmail_export_log.json` in the output folder
   tracks what's already been downloaded and what got skipped (and why:
   non-image attachments, zero-byte attachments, etc.), so re-running
   the same command won't re-download anything.
5. Move (or leave) the `pre-2015/` folder alongside your iCloud/Takeout
   year-folders so pass 1 picks it up in the same run.

## 4. Pass 1 — cheap heuristics, no AI

```bash
python -m photo_pipeline.pass1 \
    --input ~/photos \
    --output ~/culled \
    --chapters chapters.yaml \
    -v
```

`--chapters` is optional (copy `chapters.example.yaml` and edit once the
chapter list firms up — see that file's comments); without it, clusters
are grouped into year-month folders instead. Useful flags:

- `--years 2018,2019` — restrict a run to specific years (also how you'd
  batch a huge library into several smaller runs).
- `--blur-threshold` — lower this if too many marginally-soft photos are
  getting flagged; raise it if visibly blurry ones are slipping through.
  Tune it once against your own library — variance-of-Laplacian
  thresholds vary somewhat.
- `--dedup-distance` — perceptual-hash hamming distance for treating two
  photos as the same burst; lower is stricter (fewer merges).
- `--gap-hours` / `--gps-jump-km` — control what counts as a new
  event/trip cluster.

Output: `<output>/<chapter-or-year-month>/<cluster-id>/` folders with the
surviving photos, a `_contact_sheet.jpg` per cluster, and
`manifest.csv` / `manifest.json` at the output root recording every
decision (kept/duplicate/blurry/screenshot/error) and why. **Original
files are never modified or deleted** — pass 1 only copies survivors.

## 5. Pass 2 — Claude vision tiebreaker

Only run this after reviewing pass 1's contact sheets — it's meant for
the already-narrowed clusters (5-10 candidates), never the full library.

```bash
export ANTHROPIC_API_KEY=...   # or `ant auth login`
python -m photo_pipeline.pass2 --output ~/culled -v
```

For each cluster with enough surviving candidates, this sends the
(downscaled) photos to Claude and asks it to pick the best 1-2 for
composition/expressions/eyes-open, copies the picks into a `best/`
subfolder inside each cluster, and records the pick + reasoning back
into the manifest. Use `--dry-run` first to see which clusters would be
sent without spending anything.

## 6. Review

Flip through the `_contact_sheet.jpg` files and the `best/` subfolders,
and treat `manifest.csv` as the audit trail for anything that got cut.
Nothing is deleted, so if pass 1 or pass 2 was too aggressive on a
particular cluster, the excluded originals are still sitting where they
started.
