"""JSON Canonicalization Scheme (RFC 8785) with the project's value rules (spec 02 §5.7).

* ``Decimal`` values are serialized as canonical decimal text (a JSON string), never as
  JSON numbers, so no precision is lost.
* Integers are counters and must satisfy ``|n| <= 2**53 - 1``.
* Floats are rejected unless ``allow_float=True``. That flag exists to check RFC 8785
  conformance; project data never uses floats.
* Aware datetimes are serialized as RFC 3339 UTC text with 6 decimals.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from kbot.core.clock import to_rfc3339
from kbot.core.decimal import to_canonical

MAX_SAFE_INTEGER = 2**53 - 1

_CONTROL_ESCAPES = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r"}


class JcsError(ValueError):
    """The value cannot be canonicalized."""


def canonicalize(value: object, *, allow_float: bool = False) -> bytes:
    """Return the RFC 8785 canonical UTF-8 bytes of ``value``."""
    parts: list[str] = []
    _serialize(value, parts, allow_float)
    return "".join(parts).encode("utf-8")


def _serialize(value: object, out: list[str], allow_float: bool) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, Decimal):
        out.append(_string(to_canonical(value)))
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise JcsError(f"integer outside +/-(2**53 - 1): {value}")
        out.append(str(value))
    elif isinstance(value, float):
        if not allow_float:
            raise JcsError("floats are not allowed; use Decimal")
        out.append(_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, datetime):
        out.append(_string(to_rfc3339(value)))
    elif isinstance(value, Mapping):
        _object(value, out, allow_float)
    elif isinstance(value, list | tuple):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _serialize(item, out, allow_float)
        out.append("]")
    else:
        raise JcsError(f"type not supported: {type(value).__name__}")


def _object(value: Mapping[object, object], out: list[str], allow_float: bool) -> None:
    keys: list[str] = []
    for key in value:
        if not isinstance(key, str):
            raise JcsError(f"object keys must be strings, got {type(key).__name__}")
        keys.append(key)
    try:
        # RFC 8785 §3.2.3: order by UTF-16 code units; big-endian bytes compare the same way.
        ordered = sorted(keys, key=lambda key: key.encode("utf-16-be"))
    except UnicodeEncodeError as exc:
        raise JcsError("lone surrogate in an object key") from exc
    out.append("{")
    for index, key in enumerate(ordered):
        if index:
            out.append(",")
        out.append(_string(key))
        out.append(":")
        _serialize(value[key], out, allow_float)
    out.append("}")


def _string(text: str) -> str:
    chunks = ['"']
    for char in text:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise JcsError(f"lone surrogate U+{code:04X}")
        if char == '"':
            chunks.append('\\"')
        elif char == "\\":
            chunks.append("\\\\")
        elif code < 0x20:
            chunks.append(_CONTROL_ESCAPES.get(code, f"\\u{code:04x}"))
        else:
            chunks.append(char)
    chunks.append('"')
    return "".join(chunks)


def _number(value: float) -> str:
    """ECMAScript Number::toString for a finite double (ECMA-262 §7.1.12.1)."""
    if math.isnan(value) or math.isinf(value):
        raise JcsError("NaN and Infinity are not valid JSON")
    if value == 0:
        return "0"
    if value < 0:
        return "-" + _number(-value)

    # repr() gives the shortest digit string that round-trips, as ECMAScript requires.
    _, digit_tuple, exponent = Decimal(repr(value)).as_tuple()
    if not isinstance(exponent, int):
        raise JcsError("unexpected non-finite value")
    digits = "".join(map(str, digit_tuple))
    stripped = digits.rstrip("0")
    exponent += len(digits) - len(stripped)
    digits = stripped
    k = len(digits)
    n = exponent + k

    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return f"{digits[:n]}.{digits[n:]}"
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits
    e = n - 1
    sign = "+" if e >= 0 else "-"
    mantissa = digits if k == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{mantissa}e{sign}{abs(e)}"
