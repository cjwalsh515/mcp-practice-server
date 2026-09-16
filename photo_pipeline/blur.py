"""Blur detection via variance of the Laplacian.

Sharp images have a lot of high-frequency edge content, so the Laplacian
(second derivative) has high variance. Blurry/out-of-focus images are
smooth, so the variance is low. This is a cheap, well-known heuristic —
not a replacement for actually looking at close calls (that's pass 2).

The score is scale-dependent, so every image is resized to the same
longest-edge before scoring; otherwise a threshold tuned on downsized
phone photos would misfire on full-resolution DSLR exports.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import heic_support  # noqa: F401

DEFAULT_MAX_DIMENSION = 1024
DEFAULT_BLUR_THRESHOLD = 60.0


def _load_grayscale_array(path: Path, max_dimension: int) -> np.ndarray:
    with Image.open(path) as img:
        img = img.convert("L")
        img.thumbnail((max_dimension, max_dimension))
        return np.asarray(img)


def variance_of_laplacian(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def compute_blur_score(path: Path, *, max_dimension: int = DEFAULT_MAX_DIMENSION) -> float:
    """Higher = sharper. Raises on unreadable images; caller decides how to handle."""
    gray = _load_grayscale_array(path, max_dimension)
    return variance_of_laplacian(gray)


def is_blurry(score: float, *, threshold: float = DEFAULT_BLUR_THRESHOLD) -> bool:
    return score < threshold
