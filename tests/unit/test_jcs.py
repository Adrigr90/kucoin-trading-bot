"""Canonical JSON (RFC 8785) and acceptance test F1-A4.

F1-A4: jcs reproduces the RFC 8785 examples and serializes decimals as text.
The expected values below are copied from RFC 8785 (https://www.rfc-editor.org/rfc/rfc8785),
sections 3.2.2 to 3.2.4 and Appendix B, consulted on 6 November 2026.
"""

from __future__ import annotations

import json
import random
import struct
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kbot.core.jcs import MAX_SAFE_INTEGER, JcsError, canonicalize

BACKSLASH = chr(92)


def _u(code: str) -> str:
    """A JSON \\u escape for one UTF-16 code unit, built at run time.

    Editors and tools may turn a literal backslash-u sequence into the character itself;
    building it keeps the RFC escapes exactly as published.
    """
    return BACKSLASH + "u" + code


# RFC 8785 §3.2.2: the input, as JSON text keeping every escape sequence of the RFC.
RFC_INPUT = (
    "{\n"
    '  "numbers": [333333333.33333329, 1E30, 4.50,\n'
    "              2e-3, 0.000000000000000000000000001],\n"
    '  "string": "'
    + _u("20ac")
    + "$"
    + _u("000F")
    + _u("000a")
    + "A'"
    + _u("0042")
    + _u("0022")
    + _u("005c")
    + BACKSLASH * 2
    + BACKSLASH
    + '"'
    + BACKSLASH
    + "/"
    + '",\n'
    '  "literals": [null, true, false]\n'
    "}\n"
)

# RFC 8785 §3.2.3, joined without the display line wrap.
RFC_CANONICAL_TEXT = (
    '{"literals":[null,true,false],"numbers":[333333333.3333333,'
    '1e+30,4.5,0.002,1e-27],"string":"\N{EURO SIGN}$'
    + _u("000f")
    + BACKSLASH
    + "nA'B"
    + BACKSLASH
    + '"'
    + BACKSLASH * 4
    + BACKSLASH
    + '"/"}'
)

# RFC 8785 §3.2.4: the UTF-8 bytes in hexadecimal.
RFC_UTF8_HEX = """
7b 22 6c 69 74 65 72 61 6c 73 22 3a 5b 6e 75 6c 6c 2c 74 72
75 65 2c 66 61 6c 73 65 5d 2c 22 6e 75 6d 62 65 72 73 22 3a
5b 33 33 33 33 33 33 33 33 33 2e 33 33 33 33 33 33 33 2c 31
65 2b 33 30 2c 34 2e 35 2c 30 2e 30 30 32 2c 31 65 2d 32 37
5d 2c 22 73 74 72 69 6e 67 22 3a 22 e2 82 ac 24 5c 75 30 30
30 66 5c 6e 41 27 42 5c 22 5c 5c 5c 5c 5c 22 2f 22 7d
"""

# RFC 8785 §3.2.3: sorting test data (keys as escaped in the RFC) and the expected order.
_SORTING_MEMBERS = [
    (_u("20ac"), "Euro Sign"),
    (BACKSLASH + "r", "Carriage Return"),
    (_u("fb33"), "Hebrew Letter Dalet With Dagesh"),
    ("1", "One"),
    (_u("d83d") + _u("de00"), "Emoji: Grinning Face"),
    (_u("0080"), "Control"),
    (_u("00f6"), "Latin Small Letter O With Diaeresis"),
]
RFC_SORTING_INPUT = "{" + ",".join(f'"{key}": "{value}"' for key, value in _SORTING_MEMBERS) + "}"
RFC_SORTED_VALUES = [
    "Carriage Return",
    "One",
    "Control",
    "Latin Small Letter O With Diaeresis",
    "Euro Sign",
    "Emoji: Grinning Face",
    "Hebrew Letter Dalet With Dagesh",
]

# RFC 8785 Appendix B: IEEE 754 bits and their JSON text. None means "must be an error".
RFC_NUMBERS = [
    ("0000000000000000", "0"),
    ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"),
    ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"),
    ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"),
    ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("7fffffffffffffff", None),
    ("7ff0000000000000", None),
    ("44b52d02c7e14af5", "9.999999999999997e+22"),
    ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"),
    ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
    ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"),
    ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"),
    ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


# --- F1-A4: RFC 8785 examples ---------------------------------------------------------------


def test_rfc8785_main_example_text_and_bytes() -> None:
    data = json.loads(RFC_INPUT)
    result = canonicalize(data, allow_float=True)
    assert result.decode("utf-8") == RFC_CANONICAL_TEXT
    assert result == bytes.fromhex(RFC_UTF8_HEX)


def test_rfc8785_property_sorting() -> None:
    data = json.loads(RFC_SORTING_INPUT)
    result = json.loads(canonicalize(data))
    assert list(result.values()) == RFC_SORTED_VALUES


@pytest.mark.parametrize(("bits", "expected"), RFC_NUMBERS)
def test_rfc8785_appendix_b_numbers(bits: str, expected: str | None) -> None:
    value = struct.unpack(">d", bytes.fromhex(bits))[0]
    if expected is None:
        with pytest.raises(JcsError):
            canonicalize(value, allow_float=True)
    else:
        assert canonicalize(value, allow_float=True).decode() == expected


def test_rfc8785_lone_surrogates_are_errors() -> None:
    with pytest.raises(JcsError):
        canonicalize(chr(0xDEAD))
    with pytest.raises(JcsError):
        canonicalize({chr(0xDEAD): 1})


# --- project rules (02 §5.7) -------------------------------------------------------------


def test_decimals_are_serialized_as_canonical_text() -> None:
    data = {"qty": Decimal("-1.50"), "zero": Decimal("-0"), "price": Decimal("60000.00")}
    assert canonicalize(data) == b'{"price":"60000","qty":"-1.5","zero":"0"}'


def test_decimal_with_19_decimals_is_rejected() -> None:
    with pytest.raises(ValueError, match="significant decimals"):
        canonicalize({"x": Decimal("0.0000000000000000001")})


def test_floats_are_rejected_by_default() -> None:
    with pytest.raises(JcsError, match="Decimal"):
        canonicalize({"price": 1.5})


def test_counters_are_limited_to_2_pow_53_minus_1() -> None:
    assert (
        canonicalize([MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER])
        == b"[9007199254740991,-9007199254740991]"
    )
    with pytest.raises(JcsError):
        canonicalize(MAX_SAFE_INTEGER + 1)


def test_datetimes_are_rfc3339_utc_with_6_decimals() -> None:
    moment = datetime(2026, 11, 6, 8, 30, 5, 120, tzinfo=timezone(timedelta(hours=1)))
    assert canonicalize({"t": moment}) == b'{"t":"2026-11-06T07:30:05.000120Z"}'
    with pytest.raises(ValueError, match="naive"):
        canonicalize(datetime(2026, 11, 6, 8, 30))
    assert canonicalize(datetime(2026, 1, 1, tzinfo=UTC)) == b'"2026-01-01T00:00:00.000000Z"'


def test_control_characters_and_escapes() -> None:
    text = '\b\t\n\f\r\x00\x1f\x7f\N{LINE SEPARATOR}"\\/'
    assert (
        canonicalize(text)
        == '"\\b\\t\\n\\f\\r\\u0000\\u001f\x7f\N{LINE SEPARATOR}\\"\\\\/"'.encode()
    )


@pytest.mark.parametrize("bad", [{1: "a"}, {(1, 2): "a"}, {"a": {1, 2}}, b"bytes", object()])
def test_unsupported_values_are_rejected(bad: object) -> None:
    with pytest.raises(JcsError):
        canonicalize(bad)


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-MAX_SAFE_INTEGER, MAX_SAFE_INTEGER)
    | st.text()
    | st.decimals(allow_nan=False, allow_infinity=False, places=6),
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=8), children, max_size=5)
    ),
    max_leaves=25,
)


@settings(max_examples=300, deadline=None)
@given(json_values, st.randoms())
def test_canonical_form_ignores_key_order_and_is_valid_json(
    value: object, rng: random.Random
) -> None:
    first = canonicalize(value)
    assert canonicalize(_shuffled(value, rng)) == first
    # Decimals come back as their canonical strings, which serialize to the same bytes.
    assert canonicalize(json.loads(first)) == first


def _shuffled(value: object, rng: random.Random) -> object:
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {key: _shuffled(item, rng) for key, item in items}
    if isinstance(value, list):
        return [_shuffled(item, rng) for item in value]
    return value
