"""Exact JSON parsing for KuCoin payloads.

Some KuCoin responses carry decimals as JSON numbers (for example ``1.0E-4`` in the V1
funding rate). Parsing them as floats would round silently, which spec 02 §0 rule 5
forbids, so every number with a fraction or an exponent becomes a ``Decimal``.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any


class NonFiniteNumber(ValueError):
    """The payload contains NaN or Infinity, which are not valid JSON numbers."""


def loads(raw: str | bytes | bytearray) -> Any:
    return json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant)


def _reject_constant(name: str) -> Any:
    raise NonFiniteNumber(f"non-finite JSON number: {name}")
