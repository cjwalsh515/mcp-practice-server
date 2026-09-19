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

The one thing it can't do anything about: the "undated" cluster has no
date to relabel from, so it's left exactly where it is. Everything else,
including `review_decisions.json`'s "resume where I left off" position,
is handled — relabel recomputes the new browsing order and points
`current_index` at the first still-undecided photo in it, so resuming
review afterward doesn't mean paging back past everything you already
decided. Safe to run more than once; a cluster already at its correct
label is a no-op.

**Stop review.py before running this.** review.py loads
`review_decisions.json` into memory once at startup and writes that
*entire* in-memory copy back on every keystroke — it never re-reads the
file. If a review.py server is still running (browser tab open or not)
while you run relabel.py, the next click in that tab would silently
overwrite everything relabel just rewrote with the stale pre-relabel
state. This is enforced, not just a reminder: relabel.py checks for a
live review.py process and refuses to run with a clear error if it finds
one, rather than risking a silent overwrite. Stop the review.py server
(`Ctrl+C`), run relabel.py, then start review.py again to pick up the new
layout. (Dry-run is exempt — it never writes anything, so it's always
safe to run alongside a live session.)

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
the server) never loses progress.

Resuming does **not** just reopen at the raw saved position — on load, if
that position is already decided (most commonly because the item order
changed since your last session, e.g. from relabel.py, or from running
pass2/pass3 again), it jumps forward to the next undecided photo instead
of making you page back past everything you've already called. You never
have to re-click through decided photos to pick up where you left off.

**Run order still matters, though.** review.py decides which layer to
show (`highlights/` > `best/` > `kept`) fresh each time it launches. If
you review a cluster while it's still showing `kept`, then later run
pass 3 (which adds `highlights/`), the *next* launch will show that
cluster's `highlights/` photos as new, undecided items — your old
decisions on the `kept` files aren't lost (nothing is ever deleted), but
they're now extra work you didn't need to redo. The auto-skip above stops
that from meaning re-clicking through a huge already-decided backlog, but
it's still best to run pass 2 and/or pass 3 once, then review once. This
one isn't enforced with a hard error like the relabel.py lock below —
pass2.py/pass3.py only ever *add* new best/highlights files, they never
rewrite `review_decisions.json` or move anything a live session has
already cached, so the worst case is extra review work, not an overwrite.

**relabel.py is different, and review.py will refuse to start (or vice
versa) if the other is actively running against the same `--output`
folder.** Unlike pass2/pass3, relabel.py rewrites `review_decisions.json`
directly and moves the folders a live review.py session has cached paths
for — exactly the kind of change that could get silently reverted by
review.py's next save. Both tools check for a lock file
(`.photo_pipeline.lock` in `--output`) before doing anything that writes,
and fail loudly naming the other process if it's still alive, rather than
racing. See the relabel.py section above for the stop/run/restart
sequence this means in practice.

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
exported and when it was decided.

Re-running is a **sync**, not just an append: it compares the new run
against the previous `export_manifest.json` and removes anything it
previously placed that's no longer part of the current keep-set — a
decision that flipped from keep to skip, or a chapter folder renamed by
`relabel.py` on `--output`. It only ever deletes files it tracked in its
own manifest; anything you've added to `--dest` by hand (extra photos,
notes) is left alone. So it's always safe to re-run after more review
progress, after a relabel, or after changing your mind on a few photos —
`--dest` always ends up reflecting exactly the current
`review_decisions.json`, never a stale mix of old and new.

## 9. Audit trail

If you'd rather not use the review tool, you can also just flip through
the `_contact_sheet.jpg` files and the `best/`/`highlights/` subfolders by
hand and copy your picks over yourself — `manifest.csv` is the audit trail
for anything that got cut at any stage, and nothing is ever deleted, so
the excluded originals are always still sitting where they started.
