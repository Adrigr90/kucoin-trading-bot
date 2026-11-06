"""Accounting decimal context and the only allowed rounding points (spec 02 §5.7, H07).

Rules enforced here:

* Accounting arithmetic runs in a context with precision 80 and the ``Inexact`` trap on,
  so any unplanned rounding raises instead of silently losing digits.
* The only rounding points are the explicit quantization functions of this module. They
  round with a declared rule and report every rounding that changes the value.
* The canonical JSON text of a decimal never rounds: more than 18 significant decimals
  raises ``PrecisionNotRepresentable`` (code ``precision_no_representable``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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
    DivisionByZero,
    FloatOperation,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)

import structlog

PRECISION = 80
MAX_SCALE = 18
QUOTIENT_SCALE = 18

ALLOWED_ROUNDINGS = frozenset(
    {
        ROUND_DOWN,
        ROUND_UP,
        ROUND_FLOOR,
        ROUND_CEILING,
        ROUND_HALF_EVEN,
        ROUND_HALF_UP,
        ROUND_HALF_DOWN,
    }
)

CANONICAL_PATTERN = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?")

_log = structlog.get_logger("kbot.core.decimal")


class PrecisionNotRepresentable(ValueError):
    """A decimal has more significant decimals than the canonical form allows."""

    code = "precision_no_representable"


@dataclass(frozen=True, slots=True)
class RoundingEvent:
    """One rounding that changed a value at a declared quantization point.

    For an accounting quotient, ``original`` is its 80-digit approximation, kept only
    for the record; the exact quotient may have no finite decimal form.
    """

    reason: str
    original: Decimal
    result: Decimal
    step: Decimal
    rounding: str


RoundingRecorder = Callable[[RoundingEvent], None]


def accounting_context() -> Context:
    """Return a new accounting context: precision 80, traps on Inexact and FloatOperation."""
    return Context(
        prec=PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emax=999_999,
        Emin=-999_999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[Inexact, InvalidOperation, DivisionByZero, Overflow, FloatOperation],
    )


@contextmanager
def accounting() -> Iterator[Context]:
    """Run the enclosed block with the accounting context as the current decimal context."""
    with localcontext(accounting_context()) as ctx:
        yield ctx


def quantize_to_step(
    value: Decimal,
    step: Decimal,
    rounding: str,
    *,
    reason: str,
    recorder: RoundingRecorder | None = None,
) -> Decimal:
    """Round ``value`` to a multiple of ``step`` with an explicit rule.

    Exact integer arithmetic is used, so the result does not depend on the current
    decimal context. ``step`` may be any positive decimal (for example a ``tickSize``
    of 0.5), not only a power of ten.
    """
    _require_finite(value, "value")
    _require_finite(step, "step")
    if step <= 0:
        raise ValueError("step must be positive")
    _require_rounding(rounding)
    _require_reason(reason)

    scale = max(_scale(value), _scale(step))
    scaled_value = _scaled_int(value, scale)
    scaled_step = _scaled_int(step, scale)
    multiple = _divide_round(scaled_value, scaled_step, rounding)
    result = Decimal(f"{multiple * scaled_step}E-{scale}")

    if result != value:
        _record(RoundingEvent(reason, value, result, step, rounding), recorder)
    return result


def quantize_scale(
    value: Decimal,
    places: int,
    rounding: str,
    *,
    reason: str,
    recorder: RoundingRecorder | None = None,
) -> Decimal:
    """Round ``value`` to ``places`` decimals with an explicit rule."""
    if not 0 <= places <= MAX_SCALE:
        raise ValueError(f"places must be between 0 and {MAX_SCALE}")
    return quantize_to_step(
        value, Decimal(f"1E-{places}"), rounding, reason=reason, recorder=recorder
    )


def quotient(
    numerator: Decimal,
    denominator: Decimal,
    *,
    reason: str,
    recorder: RoundingRecorder | None = None,
) -> Decimal:
    """Accounting quotient: scale 18 and ROUND_HALF_EVEN, correctly rounded (02 §5.7)."""
    _require_finite(numerator, "numerator")
    _require_finite(denominator, "denominator")
    _require_reason(reason)
    if denominator == 0:
        raise ZeroDivisionError("accounting quotient with zero denominator")

    scale = max(_scale(numerator), _scale(denominator))
    num = _scaled_int(numerator, scale)
    den = _scaled_int(denominator, scale)
    if den < 0:
        num, den = -num, -den
    scaled_result = _divide_round(num * 10**QUOTIENT_SCALE, den, ROUND_HALF_EVEN)
    result = Decimal(f"{scaled_result}E-{QUOTIENT_SCALE}")

    if scaled_result * den != num * 10**QUOTIENT_SCALE:
        approximate = Context(prec=PRECISION).divide(numerator, denominator)
        step = Decimal(f"1E-{QUOTIENT_SCALE}")
        _record(RoundingEvent(reason, approximate, result, step, ROUND_HALF_EVEN), recorder)
    return result


def to_canonical(value: Decimal) -> str:
    """Canonical JSON text of a decimal: ``-?(0|[1-9][0-9]*)(\\.[0-9]*[1-9])?``.

    The value is normalized first and its scale checked afterwards. Nothing is rounded.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"expected Decimal, got {type(value).__name__}")
    _require_finite(value, "value")

    sign, digits, exponent = value.as_tuple()
    mantissa = int("".join(map(str, digits)))
    if mantissa == 0:
        return "0"
    if not isinstance(exponent, int):
        raise ValueError("value must be finite")
    while mantissa % 10 == 0:
        mantissa //= 10
        exponent += 1

    if exponent >= 0:
        if exponent > PRECISION:
            raise ValueError("decimal magnitude out of the supported range")
        body = str(mantissa) + "0" * exponent
    else:
        scale = -exponent
        if scale > MAX_SCALE:
            raise PrecisionNotRepresentable(
                f"{scale} significant decimals; the maximum is {MAX_SCALE}"
            )
        padded = str(mantissa).rjust(scale + 1, "0")
        body = f"{padded[:-scale]}.{padded[-scale:]}"

    text = f"-{body}" if sign else body
    if not CANONICAL_PATTERN.fullmatch(text):
        raise RuntimeError(f"canonical form violated: {text!r}")
    return text


def from_canonical(text: str) -> Decimal:
    """Parse canonical decimal text, rejecting any non-canonical spelling."""
    if not isinstance(text, str) or not CANONICAL_PATTERN.fullmatch(text) or text == "-0":
        raise ValueError(f"not a canonical decimal: {text!r}")
    if "." in text and len(text.split(".", 1)[1]) > MAX_SCALE:
        raise PrecisionNotRepresentable(f"more than {MAX_SCALE} decimals: {text!r}")
    return Decimal(text)


def _record(event: RoundingEvent, recorder: RoundingRecorder | None) -> None:
    if recorder is not None:
        recorder(event)
    _log.info(
        "rounding_applied",
        reason=event.reason,
        original=str(event.original),
        result=str(event.result),
        step=str(event.step),
        rounding=event.rounding,
    )


def _require_finite(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _require_rounding(rounding: str) -> None:
    if rounding not in ALLOWED_ROUNDINGS:
        raise ValueError(f"rounding rule not allowed: {rounding!r}")


def _require_reason(reason: str) -> None:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("every rounding point needs a declared reason")


def _scale(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("value must be finite")
    return -exponent if exponent < 0 else 0


def _scaled_int(value: Decimal, scale: int) -> int:
    """Exact integer ``value * 10**scale``; ``scale`` must cover all decimals of ``value``."""
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("value must be finite")
    mantissa = int("".join(map(str, digits)))
    shift = exponent + scale
    if shift < 0:
        raise ValueError("scale does not cover all decimals")
    magnitude = mantissa * 10**shift
    return -magnitude if sign else magnitude


def _divide_round(numerator: int, denominator: int, rounding: str) -> int:
    """Integer division ``numerator / denominator`` (denominator > 0) with a decimal rule."""
    floor, remainder = divmod(numerator, denominator)
    if remainder == 0:
        return floor
    ceiling = floor + 1
    non_negative = numerator >= 0
    toward_zero = floor if non_negative else ceiling
    away_from_zero = ceiling if non_negative else floor

    if rounding == ROUND_FLOOR:
        return floor
    if rounding == ROUND_CEILING:
        return ceiling
    if rounding == ROUND_DOWN:
        return toward_zero
    if rounding == ROUND_UP:
        return away_from_zero

    twice = 2 * remainder
    if twice < denominator:
        return floor
    if twice > denominator:
        return ceiling
    if rounding == ROUND_HALF_UP:
        return away_from_zero
    if rounding == ROUND_HALF_DOWN:
        return toward_zero
    return floor if floor % 2 == 0 else ceiling
