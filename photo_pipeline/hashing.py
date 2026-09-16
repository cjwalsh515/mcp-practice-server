"""Perceptual-hash near-duplicate detection (burst shots, near-identical retakes).

Only ever compared *within* a time-cluster (see clustering.py) — comparing
phashes across the whole library is both slow and semantically wrong (two
unrelated photos that happen to hash close together are not a "burst").
"""

from __future__ import annotations

from pathlib import Path
from typing import Hashable, Sequence

import imagehash
from PIL import Image

from . import heic_support  # noqa: F401

DEFAULT_HASH_SIZE = 8
DEFAULT_MAX_DISTANCE = 6  # out of a 64-bit hash (hash_size=8); tune per library


def compute_phash(path: Path, *, hash_size: int = DEFAULT_HASH_SIZE) -> imagehash.ImageHash:
    with Image.open(path) as img:
        return imagehash.phash(img.convert("RGB"), hash_size=hash_size)


def hamming_distance(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    return a - b


class _UnionFind:
    def __init__(self, keys: Sequence[Hashable]):
        self._parent = {k: k for k in keys}

    def find(self, k):
        while self._parent[k] != k:
            self._parent[k] = self._parent[self._parent[k]]
            k = self._parent[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def group_near_duplicates(
    hashes: dict[Hashable, imagehash.ImageHash], *, max_distance: int = DEFAULT_MAX_DISTANCE
) -> list[list[Hashable]]:
    """Groups keys whose phashes are within ``max_distance`` of each other.

    O(n^2) comparisons — fine within a single event cluster (tens to low
    hundreds of photos), not intended for whole-library use.
    """
    keys = list(hashes.keys())
    uf = _UnionFind(keys)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if hamming_distance(hashes[keys[i]], hashes[keys[j]]) <= max_distance:
                uf.union(keys[i], keys[j])

    groups: dict[Hashable, list[Hashable]] = {}
    for k in keys:
        root = uf.find(k)
        groups.setdefault(root, []).append(k)
    return list(groups.values())
