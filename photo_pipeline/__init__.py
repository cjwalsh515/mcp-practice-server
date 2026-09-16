"""Photo culling pipeline for the anniversary photobook project.

Two-pass approach:
  * Pass 1 (this package's ``pass1`` module) — cheap heuristics only:
    EXIF-based clustering, perceptual-hash de-duplication, blur detection,
    and screenshot/non-camera filtering. Cuts the library by 70-90%.
  * Pass 2 (``pass2`` module) — Claude vision as a tiebreaker within the
    small clusters pass 1 leaves behind.

See docs/SETUP.md for end-to-end usage.
"""
