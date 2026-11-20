"""The regime and the strategy fichas of the Phase 3 (F3-E3; ``02`` §12.3). The design was
approved by the reviewer, and each of the three fichas by the owner, on 20 November 2026.

* ``regime``: the two labels of a candle: direction, from the ADX, and volatility, from the
  relative ATR and the Bollinger width against their last 100 values.
* ``version``: ``strategy_version`` = commit + the SHA-256 of the canonical parameters.
* ``rules``: what the three fichas share. It holds the directions by market, the walk of the
  trigger candles, whose instants never go back (F3E3-01), the initial stop and the entry
  checks (F3E3-02). It also holds the level exits and the trailing stop.
* ``frames``: one timeframe of one instrument with its indicator values, read only as they
  were available at an instant, and the provenance of a result in original minutes.
* ``ficha``: what every ficha does with its frames: find its signals and walk their trigger.
* ``trend``, ``breakout`` and ``mean_reversion``: the three approved fichas.

Saving the signals, the combiner and the backtest engine with its positions are F3-E4.
"""
