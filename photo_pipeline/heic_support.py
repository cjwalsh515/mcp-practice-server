"""Registers HEIC/HEIF support in Pillow, if pillow-heif is installed.

iPhone exports (iCloud "original files") are commonly .heic. Without this,
Pillow can't open them at all. Import this module once, early, before any
``PIL.Image.open`` calls elsewhere in the pipeline.
"""

import logging

logger = logging.getLogger(__name__)

HEIC_SUPPORTED = False

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    logger.warning(
        "pillow-heif not installed — .heic/.heif files will be skipped. "
        "Install with `pip install pillow-heif` to process iPhone originals."
    )
