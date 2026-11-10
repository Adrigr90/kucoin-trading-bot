"""Canonical content fingerprint of a table's rows (spec 02 §3.9; review of the E3 design).

The same serialization is applied to the rows read from PostgreSQL and to the rows read back
from the Parquet file, so the fingerprint does not depend on how either represents a value:

* ``NULL`` is JSON ``null``; a boolean is ``true`` or ``false``;
* an integer is its decimal text; a ``numeric`` is its decimal text without exponent or
  trailing zeros, with ``-0`` written ``0``;
* a moment is UTC ISO 8601 with microseconds; a date is ISO 8601;
* text is itself; ``jsonb`` is turned into its JCS text (RFC 8785) before both sides.

Each row is the JCS array of its values, and the fingerprint is the SHA-256 of the column
names followed by the SHA-256 of every row, in the order of the primary key.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from kbot.core.jcs import canonicalize


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError(f"not a finite number: {value}")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"a moment without its time zone: {value}")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"no canonical form for {type(value).__name__}")


def json_text(value: Any) -> str:
    """The JCS text of a ``jsonb`` value, as stored in the Parquet file."""
    return canonicalize(value).decode("utf-8")


class Fingerprint:
    """Running fingerprint of the rows of one table, fed in primary key order."""

    def __init__(self, columns: Sequence[str]) -> None:
        self._hash = hashlib.sha256(canonicalize(list(columns)))
        self.rows = 0

    def add(self, row: Sequence[Any]) -> None:
        self._hash.update(hashlib.sha256(canonicalize([canonical_value(v) for v in row])).digest())
        self.rows += 1

    def hexdigest(self) -> str:
        return self._hash.hexdigest()
