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
are grouped into year-month folders instead.

**If you set up (or change) `chapters.yaml` *after* you've already run
pass1** — and possibly reviewed some of the output by hand — don't just
re-run pass1 with `--chapters` pointed at the same `--output` directory.
Pass1 never deletes anything, so that duplicates every kept photo under
new chapter folders alongside the old year-month ones, overwrites
`manifest.json` (discarding pass2/pass3 annotations), and orphans any
`review_decisions.json` progress. Use `photo_pipeline.relabel` instead
(step 6a below) to reorganize the existing output in place without
losing any of that.

Useful flags:

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

## 6. Pass 3 — highlight extraction (optional, independent of pass 2)

Pass 2 only fires on near-duplicate bursts and picks the sharpest frame.
Pass 3 runs across *every* cluster — regardless of size or how similar the
photos are — and asks a different question: which 1-3 photos from this
whole event are the most interesting or emotionally resonant for the book
(composition, genuine expressions, candid moments), not just which is
technically correct. It works whether or not pass 2 has run: it prefers a
cluster's `best/` picks when present, otherwise every `kept` survivor.

```bash
export ANTHROPIC_API_KEY=...   # or `ant auth login`
python -m photo_pipeline.pass3 --output ~/culled -v
```

Picks are copied into a `highlights/` subfolder per cluster and logged to
the manifest as `pass3_selected` / `pass3_notes` (mirroring pass 2's
`pass2_selected` / `pass2_notes`). A cluster with only one surviving photo
is auto-highlighted without spending an API call — there's nothing to
compare. Use `--dry-run` first, and `--top-n` to change how many highlights
per cluster (default 2).

You can run pass 2 and pass 3 in either order relative to *each other*.
But run whichever of them you're going to use **before** you start a
review session (step 7) — see the note there about why.

## 6a. Relabeling chapters in place (only if you add/change chapters.yaml later)

If you started without `chapters.yaml` (or with an earlier version of it)
and already have year-month-labeled output — possibly with pass2/pass3
already run and some photos already reviewed by hand — this reorganizes
the existing `--output` folder to match a new `chapters.yaml` without
re-running clustering, blur, dedup, pass2, or pass3, and without losing
any review progress:

```bash
python -m photo_pipeline.relabel --output ~/culled --chapters chapters.yaml --dry-run -v
python -m photo_pipeline.relabel --output ~/culled --chapters chapters.yaml -v
```

Each cluster's id already encodes its own date (`YYYY-MM-DD_NNN`), so the
new chapter label is recomputed straight from the folder name — no photo
content is re-examined. For every cluster whose label actually changes,
the whole subfolder (kept photos, `best/`, `highlights/`, contact sheet)
moves as one unit, `manifest.json`/`.csv` gets `chapter_label` (and
`output_path`) rewritten in place — `pass2_selected`/`pass2_notes`/
`pass3_selected`/`pass3_notes` are untouched — and `review_decisions.json`
keys are rewritten to match, so every prior keep/skip call survives.

Two things it can't do anything about: the "undated" cluster (no date to
work from — left exactly where it is), and `review_decisions.json`'s
"resume where I left off" position, which resets to 0 after a relabel
that moves anything, because review order depends on chapter folder names
and those just changed — no individual decision is lost, only the
browsing cursor. Safe to run more than once; a cluster already at its
correct label is a no-op.

## 7. Fast local review

A keyboard-driven local web app for quickly deciding keep/skip, one photo
at a time, using whichever layer is most curated for each cluster
(`highlights/` > `best/` > `kept`, whichever exists):

```bash
python3 -m photo_pipeline.review --output ~/culled
```

Opens a browser tab automatically. No network calls, no API key — this
only reads files under `--output` and writes a small
`review_decisions.json` next to `manifest.json`.

Keys: `Y` keep, `N` skip (both auto-advance), `→`/space next without
deciding, `←`/Backspace/`U` back (revisit a photo to change your mind).
Decisions save after every keystroke, so closing the tab (or `Ctrl+C`-ing
the server) never loses progress — relaunching resumes exactly where you
left off.

**Run order matters here.** review.py decides which layer to show
(`highlights/` > `best/` > `kept`) fresh each time it launches. If you
review a cluster while it's still showing `kept`, then later run pass 3
(which adds `highlights/`), the *next* launch will show that cluster's
`highlights/` photos as new, undecided items — your old decisions on the
`kept` files aren't lost (nothing is ever deleted) but they become
orphaned extra work. Run pass 2 and/or pass 3 once, then review once.

Each cluster's speed depends on which layer you're reviewing: a cluster
narrowed by pass 3 is usually 1 photo (auto-highlighted, no choices to
make) or up to `--top-n`; a cluster you're reviewing straight off pass 1's
`kept` output could still have 5-15 candidates in it.

## 8. Export your final picks

This is the actual finish line — turning your `Y` decisions into a folder
you can hand to Mixbook:

```bash
python -m photo_pipeline.export --output ~/culled --dest ~/PhotoProject/final
```

Reads `review_decisions.json`, copies every photo marked `keep` into
`--dest`, grouped by chapter folder (`<dest>/<chapter>/<cluster-id>_<filename>`
— the cluster-id prefix avoids collisions between same-named photos from
different events, and doubles as a free date/event breadcrumb for the
later captioning/map phases). Generates a contact sheet per exported
chapter, plus `export_manifest.csv` / `.json` recording exactly what got
exported and when it was decided. Safe to re-run any time after more
review progress — it always reflects the current state of
`review_decisions.json`.

## 9. Audit trail

If you'd rather not use the review tool, you can also just flip through
the `_contact_sheet.jpg` files and the `best/`/`highlights/` subfolders by
hand and copy your picks over yourself — `manifest.csv` is the audit trail
for anything that got cut at any stage, and nothing is ever deleted, so
the excluded originals are always still sitting where they started.
