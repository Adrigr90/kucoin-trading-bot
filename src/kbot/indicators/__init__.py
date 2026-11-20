"""The indicators of the Phase 3 (F3-E2; ``02`` §12.2, approved on 20 November 2026).

* ``series``: what an indicator sees of a candle (``Point``), what it gives (``Value``) and
  the one way every indicator is run over a series (``compute``): usable candles, resets,
  inputs, provenance and availability.
* ``price``: SMA, EMA, RSI, MACD, Bollinger, ATR, and ±DI, DX and ADX, computed as TA-Lib
  0.8.3 does.
* ``volume``: VWAP of the UTC day and relative volume, the uses of volume allowed while B8
  is open (02 §12.1, point 7).
* ``levels``: supports and resistances from pivots.
"""
