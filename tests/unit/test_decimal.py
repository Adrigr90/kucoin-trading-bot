"""Accounting decimals (spec 02 §5.7, H07) and acceptance test A3.

A3: no operation outside the declared quantizations raises Inexact, and a calculation
that would lose precision fails instead of rounding.
"""

from __future__ import annotations

import math
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Context,
    Decimal,
    FloatOperation,
    Inexact,
)
from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kbot.core.decimal import (
    CANONICAL_PATTERN,
    MAX_SCALE,
    PRECISION,
    PrecisionNotRepresentable,
    RoundingEvent,
    accounting,
    from_canonical,
    quantize_scale,
    quantize_to_step,
    quotient,
    to_canonical,
)

ROUNDINGS = [
    ROUND_DOWN,
    ROUND_UP,
    ROUND_FLOOR,
    ROUND_CEILING,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_HALF_DOWN,
]

PROPERTY_SETTINGS = settings(max_examples=400, deadline=None)


def amounts(integer_digits: int = 12, max_scale: int = MAX_SCALE) -> st.SearchStrategy[Decimal]:
    """Exact decimals with at most ``max_scale`` decimals and ``integer_digits`` digits."""
    bound = 10 ** (integer_digits + max_scale)

    def build(units: int, scale: int) -> Decimal:
        return Decimal(f"{units}E-{scale}")

    return st.builds(build, st.integers(-bound, bound), st.integers(0, max_scale))


def positive_steps() -> st.SearchStrategy[Decimal]:
    return st.builds(
        lambda units, scale: Decimal(f"{units}E-{scale}"),
        st.integers(1, 10**6),
        st.integers(0, MAX_SCALE),
    )


def exact(value: Decimal) -> Fraction:
    return Fraction(value)


# --- A3: no silent rounding --------------------------------------------------------------


@PROPERTY_SETTINGS
@given(amounts(), amounts())
def test_sum_difference_and_product_are_exact_without_inexact(a: Decimal, b: Decimal) -> None:
    with accounting():
        total = a + b
        difference = a - b
        product = a * b
    assert exact(total) == exact(a) + exact(b)
    assert exact(difference) == exact(a) - exact(b)
    assert exact(product) == exact(a) * exact(b)


@PROPERTY_SETTINGS
@given(st.lists(amounts(), min_size=1, max_size=60))
def test_long_sums_stay_exact(values: list[Decimal]) -> None:
    with accounting():
        total = sum(values, Decimal(0))
    assert exact(total) == sum(map(exact, values), Fraction(0))


@PROPERTY_SETTINGS
@given(amounts(), amounts().filter(lambda d: d != 0))
def test_division_is_exact_or_raises_inexact(a: Decimal, b: Decimal) -> None:
    try:
        with accounting():
            result = a / b
    except Inexact:
        assert not _fits_in_precision(exact(a) / exact(b))
    else:
        assert exact(result) == exact(a) / exact(b)


def test_a_product_beyond_80_digits_fails_instead_of_rounding() -> None:
    big = Decimal("1" * 45)
    with pytest.raises(Inexact), accounting():
        _ = big * big


def test_one_third_fails_instead_of_rounding() -> None:
    with pytest.raises(Inexact), accounting():
        _ = Decimal(1) / Decimal(3)


def test_floats_cannot_enter_the_accounting_context() -> None:
    binary_float = float("0.1")
    with pytest.raises(FloatOperation), accounting():
        Decimal(binary_float)


def test_default_context_is_left_untouched() -> None:
    with accounting() as ctx:
        assert ctx.prec == PRECISION
        assert ctx.traps[Inexact]
    assert Decimal(1) / Decimal(3) == Decimal("0.3333333333333333333333333333")


# --- declared rounding points ------------------------------------------------------------


@PROPERTY_SETTINGS
@given(amounts(), st.integers(0, MAX_SCALE), st.sampled_from(ROUNDINGS))
def test_quantize_scale_matches_python_quantize(value: Decimal, places: int, mode: str) -> None:
    events: list[RoundingEvent] = []
    result = quantize_scale(value, places, mode, reason="test", recorder=events.append)
    oracle = value.quantize(Decimal(f"1E-{places}"), rounding=mode, context=Context(prec=200))
    assert result == oracle
    assert (len(events) == 1) == (result != value)


@PROPERTY_SETTINGS
@given(amounts(), positive_steps(), st.sampled_from(ROUNDINGS))
def test_quantize_to_step_returns_the_right_multiple(
    value: Decimal, step: Decimal, mode: str
) -> None:
    events: list[RoundingEvent] = []
    with accounting():
        result = quantize_to_step(value, step, mode, reason="test", recorder=events.append)

    ratio = exact(result) / exact(step)
    assert ratio.denominator == 1, "result must be a multiple of the step"
    assert abs(exact(result) - exact(value)) < exact(step)
    assert int(ratio) == _reference_multiple(exact(value) / exact(step), mode)
    assert (len(events) == 1) == (result != value)
    if events:
        assert events[0].original == value
        assert events[0].result == result
        assert events[0].rounding == mode


def test_quantity_rounding_down_to_base_order_step() -> None:
    assert quantize_to_step(
        Decimal("0.09990009999"), Decimal("1E-8"), ROUND_DOWN, reason="qty"
    ) == Decimal("0.09990009")


def test_half_even_tie_goes_to_the_even_tick() -> None:
    tick = Decimal("0.5")
    assert quantize_to_step(Decimal("10.25"), tick, ROUND_HALF_EVEN, reason="p") == Decimal("10")
    assert quantize_to_step(Decimal("10.75"), tick, ROUND_HALF_EVEN, reason="p") == Decimal("11")


def test_quantization_needs_reason_and_allowed_rule() -> None:
    with pytest.raises(ValueError, match="reason"):
        quantize_scale(Decimal("1.23"), 1, ROUND_DOWN, reason=" ")
    with pytest.raises(ValueError, match="not allowed"):
        quantize_scale(Decimal("1.23"), 1, "ROUND_05UP", reason="x")
    with pytest.raises(ValueError, match="positive"):
        quantize_to_step(Decimal("1.23"), Decimal(0), ROUND_DOWN, reason="x")


@PROPERTY_SETTINGS
@given(amounts(), amounts().filter(lambda d: d != 0))
def test_quotient_is_correctly_rounded_half_even_at_scale_18(a: Decimal, b: Decimal) -> None:
    events: list[RoundingEvent] = []
    with accounting():
        result = quotient(a, b, reason="ratio", recorder=events.append)
    scaled = exact(a) / exact(b) * 10**18
    floor = math.floor(scaled)
    remainder = scaled - floor
    tie_up = remainder == Fraction(1, 2) and floor % 2 == 1
    expected = floor + (1 if remainder > Fraction(1, 2) or tie_up else 0)
    assert exact(result) == Fraction(expected, 10**18)
    assert (len(events) == 1) == (scaled.denominator != 1)


def test_quotient_rejects_zero_denominator() -> None:
    with pytest.raises(ZeroDivisionError):
        quotient(Decimal(1), Decimal(0), reason="x")


# --- canonical JSON text (RC15) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("-1.50"), "-1.5"),
        (Decimal("-0"), "0"),
        (Decimal("0.000"), "0"),
        (Decimal("10000"), "10000"),
        (Decimal("1E+3"), "1000"),
        (Decimal("0.5"), "0.5"),
        (Decimal("1.000000000000000000000"), "1"),
        (Decimal("0.000000000000000001"), "0.000000000000000001"),
    ],
)
def test_rc15_canonical_text(value: Decimal, text: str) -> None:
    assert to_canonical(value) == text


def test_rc15_nineteen_decimals_is_an_error() -> None:
    with pytest.raises(PrecisionNotRepresentable) as info:
        to_canonical(Decimal("0.0000000000000000001"))
    assert info.value.code == "precision_no_representable"


@PROPERTY_SETTINGS
@given(amounts(integer_digits=20))
def test_canonical_round_trip(value: Decimal) -> None:
    text = to_canonical(value)
    assert CANONICAL_PATTERN.fullmatch(text)
    assert from_canonical(text) == value
    assert to_canonical(from_canonical(text)) == text


@pytest.mark.parametrize("text", ["-0", "01", "1.0", "1.", ".5", "+1", "1e3", " 1", "NaN"])
def test_non_canonical_text_is_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        from_canonical(text)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_values_are_rejected(value: Decimal) -> None:
    with pytest.raises(ValueError):
        to_canonical(value)


def _fits_in_precision(value: Fraction) -> bool:
    """True when ``value`` has a finite decimal form with at most PRECISION digits."""
    denominator = value.denominator
    for prime in (2, 5):
        while denominator % prime == 0:
            denominator //= prime
    if denominator != 1:
        return False
    scale = 0
    while (value * 10**scale).denominator != 1:
        scale += 1
    digits = len(str(abs((value * 10**scale).numerator)).rstrip("0") or "0")
    return digits <= PRECISION


def _reference_multiple(ratio: Fraction, mode: str) -> int:
    floor = math.floor(ratio)
    ceiling = math.ceil(ratio)
    if floor == ceiling:
        return floor
    toward_zero = floor if ratio >= 0 else ceiling
    away_from_zero = ceiling if ratio >= 0 else floor
    simple = {
        ROUND_FLOOR: floor,
        ROUND_CEILING: ceiling,
        ROUND_DOWN: toward_zero,
        ROUND_UP: away_from_zero,
    }
    if mode in simple:
        return simple[mode]
    distance = ratio - floor
    if distance < Fraction(1, 2):
        return floor
    if distance > Fraction(1, 2):
        return ceiling
    if mode == ROUND_HALF_UP:
        return away_from_zero
    if mode == ROUND_HALF_DOWN:
        return toward_zero
    return floor if floor % 2 == 0 else ceiling
