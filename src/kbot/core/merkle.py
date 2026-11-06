"""Merkle Tree Hash of RFC 6962 §2.1 (same structure as RFC 9162 §2.1.1), with SHA-256.

Only what reference case RC14 and the future daily seal need (spec 02 §10.2, §15).
Leaves are the canonical JCS bytes of the records, already ordered by the caller.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def empty_root() -> bytes:
    """MTH({}) = SHA-256 of the empty string."""
    return hashlib.sha256(b"").digest()


def leaf_hash(data: bytes) -> bytes:
    """MTH({d}) = SHA-256(0x00 || d)."""
    return hashlib.sha256(LEAF_PREFIX + _require_bytes(data)).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """SHA-256(0x01 || left || right)."""
    return hashlib.sha256(NODE_PREFIX + _require_bytes(left) + _require_bytes(right)).digest()


def root(leaves: Sequence[bytes]) -> bytes:
    """Merkle Tree Hash of an ordered list of leaves."""
    count = len(leaves)
    if count == 0:
        return empty_root()
    if count == 1:
        return leaf_hash(leaves[0])
    split = 1 << ((count - 1).bit_length() - 1)
    return node_hash(root(leaves[:split]), root(leaves[split:]))


def _require_bytes(data: bytes) -> bytes:
    if not isinstance(data, bytes | bytearray | memoryview):
        raise TypeError(f"Merkle inputs must be bytes, got {type(data).__name__}")
    return bytes(data)
