"""The price indicators of F3-E2, computed as TA-Lib 0.8.3 does (``02`` §12.2, point 2).

Each takes the usable points of one run in order (``series.compute``) and gives its first
value after its warm-up, the lookback of TA-Lib plus one. The arithmetic follows the C code
of TA-Lib 0.8.3, read at its tag: running sums, the EMA factor written so that its two
weights add up to exactly 1, and Wilder's smoothing as ``S - S * (1/n) + x``. TA-Lib fuses
some products into one rounding (``fma``), which Python 3.12 lacks, so a value can differ
in its last bits; the comparison tolerates far more (§12.2, point 5).
"""

from __future__ import annotations

import math
from collections import deque

from kbot.indicators.series import Indicator, Kind, Point


def _period(n: int, least: int = 2) -> int:
    if n < least:
        raise ValueError(f"a period must be at least {least}")
    return n


def ema_weights(n: int) -> tuple[float, float]:
    """``(k, beta)`` of an EMA of period ``n`` as TA-Lib 0.8.3 writes them: ``beta`` is
    ``(n-1)/(n+1)`` and ``k = 1 - beta``, recomputed so that ``k + beta`` is exactly 1."""
    beta = (n - 1) / (n + 1)
    k = 1.0 - beta
    if beta < 0.5:
        beta = 1.0 - k
    return k, beta


class SMA(Indicator):
    """Mean of the last ``n`` closes, with TA-Lib's running total."""

    kind = Kind.WINDOW

    def __init__(self, n: int = 20) -> None:
        self.n = _period(n)
        self.name = f"SMA({n})"
        self.inputs = self.warmup = n
        self.reset()

    def reset(self) -> None:
        self._closes: deque[float] = deque()
        self._total = 0.0

    def update(self, point: Point) -> tuple[float, ...] | None:
        self._closes.append(point.close)
        self._total += point.close
        if len(self._closes) < self.n:
            return None
        mean = self._total / self.n
        self._total -= self._closes.popleft()
        return (mean,)


class _Ema:
    """An EMA seeded with the mean of its first ``n`` values, then ``beta*E + k*x``."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.k, self.beta = ema_weights(n)
        self.seed: list[float] = []
        self.value: float | None = None

    def start(self, values: list[float]) -> float:
        self.value = sum(values) / self.n
        return self.value

    def step(self, x: float) -> float:
        if self.value is None:
            raise RuntimeError("an EMA steps only after its seed")
        self.value = self.beta * self.value + self.k * x
        return self.value

    def update(self, x: float) -> float | None:
        if self.value is None:
            self.seed.append(x)
            if len(self.seed) < self.n:
                return None
            return self.start(self.seed)
        return self.step(x)


class EMA(Indicator):
    """In candle ``n``, the SMA of the first ``n`` closes; then ``alpha = 2/(n+1)``."""

    def __init__(self, n: int = 20) -> None:
        self.n = _period(n)
        self.name = f"EMA({n})"
        self.warmup = n
        self.reset()

    def reset(self) -> None:
        self._ema = _Ema(self.n)

    def update(self, point: Point) -> tuple[float, ...] | None:
        value = self._ema.update(point.close)
        return None if value is None else (value,)


class RSI(Indicator):
    """Wilder's RSI: simple means of the first ``n`` changes, then Wilder's smoothing;
    ``100 G / (G + B)``, and 50 when ``G + B`` is 0 (a flat series, as TA-Lib 0.8.3)."""

    def __init__(self, n: int = 14) -> None:
        self.n = _period(n)
        self.name = f"RSI({n})"
        self.warmup = n + 1
        self.reset()

    def reset(self) -> None:
        self._previous: float | None = None
        self._changes = 0
        self._gain = 0.0
        self._loss = 0.0

    def update(self, point: Point) -> tuple[float, ...] | None:
        close, previous = point.close, self._previous
        self._previous = close
        if previous is None:
            return None
        change = close - previous
        gain, loss = (change, 0.0) if change > 0 else (0.0, -change)
        self._changes += 1
        inverse = 1.0 / self.n
        if self._changes < self.n:
            self._gain += gain
            self._loss += loss
            return None
        if self._changes == self.n:
            self._gain = (self._gain + gain) * inverse
            self._loss = (self._loss + loss) * inverse
        else:
            self._gain = (self._gain * (self.n - 1) + gain) * inverse
            self._loss = (self._loss * (self.n - 1) + loss) * inverse
        total = self._gain + self._loss
        return (100.0 * (self._gain / total) if total > 0.0 else 50.0,)


class MACD(Indicator):
    """Line = EMA(f) - EMA(s), both started at candle ``s``: the slow one with the mean of
    its ``s`` closes, the fast one with the mean of the ``f`` closes that end there. Signal =
    EMA(q) of the line, started at candle ``s+q-1`` with the mean of its first ``q`` values.
    Outputs ``(line, signal, histogram)`` from candle ``s+q-1``, as TA-Lib computes it."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        if not 2 <= fast < slow or signal < 1:
            raise ValueError("MACD needs 2 <= fast < slow and a signal of at least 1")
        self.fast, self.slow, self.signal = fast, slow, signal
        self.name = f"MACD({fast},{slow},{signal})"
        self.warmup = slow + signal - 1
        self.reset()

    def reset(self) -> None:
        self._closes: list[float] = []
        self._fast = _Ema(self.fast)
        self._slow = _Ema(self.slow)
        self._signal = _Ema(self.signal) if self.signal > 1 else None

    def update(self, point: Point) -> tuple[float, ...] | None:
        if self._slow.value is None:
            self._closes.append(point.close)
            if len(self._closes) < self.slow:
                return None
            slow = self._slow.start(self._closes)
            fast = self._fast.start(self._closes[-self.fast :])
            self._closes = []
        else:
            fast = self._fast.step(point.close)
            slow = self._slow.step(point.close)
        line = fast - slow
        if self._signal is None:  # a signal of period 1 is the line itself
            return (line, line, 0.0)
        signal = self._signal.update(line)
        return None if signal is None else (line, signal, line - signal)


class Bollinger(Indicator):
    """Middle = SMA(n) (TA-Lib's running total); bands = middle +- k sigma, with sigma the
    population deviation of the ``n`` closes, from their own mean in two passes (0 when the
    variance is not positive). Outputs ``(upper, middle, lower)``."""

    kind = Kind.WINDOW

    def __init__(self, n: int = 20, k: float = 2.0) -> None:
        self.n = _period(n)
        self.k = k
        self.name = f"Bollinger({n},{k:g})"
        self.inputs = self.warmup = n
        self.reset()

    def reset(self) -> None:
        self._sma = SMA(self.n)
        self._closes: deque[float] = deque(maxlen=self.n)

    def update(self, point: Point) -> tuple[float, ...] | None:
        self._closes.append(point.close)
        middle = self._sma.update(point)
        if middle is None:
            return None
        mean = sum(self._closes) / self.n
        variance = sum((x - mean) * (x - mean) for x in self._closes) / self.n
        deviation = math.sqrt(variance) if variance > 0.0 else 0.0
        width = self.k * deviation
        return (middle[0] + width, middle[0], middle[0] - width)


def true_range(high: float, low: float, previous_close: float) -> float:
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


class ATR(Indicator):
    """Wilder's ATR: true ranges from candle 2; in candle ``n+1`` the mean of the first
    ``n``; then ``(n-1)/n * ATR + TR/n``."""

    def __init__(self, n: int = 14) -> None:
        self.n = _period(n, least=1)
        self.name = f"ATR({n})"
        self.warmup = n + 1
        self.reset()

    def reset(self) -> None:
        self._previous: Point | None = None
        self._ranges: list[float] = []
        self._atr: float | None = None

    def update(self, point: Point) -> tuple[float, ...] | None:
        previous, self._previous = self._previous, point
        if previous is None:
            return None
        tr = true_range(point.high, point.low, previous.close)
        if self._atr is None:
            self._ranges.append(tr)
            if len(self._ranges) < self.n:
                return None
            self._atr = sum(self._ranges) / self.n
            return (self._atr,)
        beta = (self.n - 1) / self.n  # TA-Lib's wBeta, and wAlpha = 1 - wBeta
        self._atr = beta * self._atr + (1.0 - beta) * tr
        return (self._atr,)


# TA-Lib's fixed band for a sum of DI, which are ratios without units (TA_EPSILON).
DI_EPSILON = 1e-14


class _Directional:
    """+DM, -DM and TR of one run, smoothed as TA-Lib 0.8.3 does: the sum of the first
    ``n-1``, then ``S - S * (1/n) + x``. ``update`` returns ``(+DI, -DI, DX or None)``
    from candle ``n+1``; DX is None where it is not defined."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.inverse = 1.0 / n
        self.previous: Point | None = None
        self.count = 0
        self.plus = self.minus = self.tr = 0.0

    def update(self, point: Point) -> tuple[float, float, float | None] | None:
        previous, self.previous = self.previous, point
        if previous is None:
            return None
        up = point.high - previous.high
        down = previous.low - point.low
        plus = up if up > down and up > 0.0 else 0.0
        minus = down if down > up and down > 0.0 else 0.0
        tr = true_range(point.high, point.low, previous.close)
        self.count += 1
        if self.count < self.n:
            self.plus += plus
            self.minus += minus
            self.tr += tr
            return None
        self.plus = self.plus - self.plus * self.inverse + plus
        self.minus = self.minus - self.minus * self.inverse + minus
        self.tr = self.tr - self.tr * self.inverse + tr
        if not self.tr > 0.0:  # an exact test: a running sum of ranges, never a residue
            return (0.0, 0.0, None)
        plus_di = 100.0 * (self.plus / self.tr)
        minus_di = 100.0 * (self.minus / self.tr)
        total = minus_di + plus_di
        if -DI_EPSILON < total < DI_EPSILON:
            return (plus_di, minus_di, None)
        return (plus_di, minus_di, 100.0 * (abs(minus_di - plus_di) / total))


class DMI(Indicator):
    """``(+DI, -DI, DX)`` of Wilder from candle ``n+1``. Where DX is not defined, its
    output repeats the previous one (0 the first time), as TA-Lib 0.8.3."""

    def __init__(self, n: int = 14) -> None:
        self.n = _period(n)
        self.name = f"DMI({n})"
        self.warmup = n + 1
        self.reset()

    def reset(self) -> None:
        self._directional = _Directional(self.n)
        self._dx = 0.0

    def update(self, point: Point) -> tuple[float, ...] | None:
        result = self._directional.update(point)
        if result is None:
            return None
        plus_di, minus_di, dx = result
        if dx is not None:
            self._dx = dx
        return (plus_di, minus_di, self._dx)


class ADX(Indicator):
    """Wilder's ADX, as TA-Lib 0.8.3: in candle ``2n`` the sum of the DX defined in the
    ``n`` candles from ``n+1``, over ``n`` (one not defined adds 0); then
    ``ADX - (ADX - DX) * (1/n)`` where DX is defined, and the same ADX where it is not."""

    def __init__(self, n: int = 14) -> None:
        self.n = _period(n)
        self.name = f"ADX({n})"
        self.warmup = 2 * n
        self.reset()

    def reset(self) -> None:
        self._directional = _Directional(self.n)
        self._seeded = 0
        self._sum = 0.0
        self._adx: float | None = None

    def update(self, point: Point) -> tuple[float, ...] | None:
        result = self._directional.update(point)
        if result is None:
            return None
        dx = result[2]
        if self._adx is None:
            self._seeded += 1
            self._sum += dx if dx is not None else 0.0
            if self._seeded < self.n:
                return None
            self._adx = self._sum / self.n
            return (self._adx,)
        if dx is not None:
            self._adx = self._adx - (self._adx - dx) * self._directional.inverse
        return (self._adx,)
