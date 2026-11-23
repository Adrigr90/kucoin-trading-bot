"""The backtest engine of the Phase 3 (F3-E4; ``02`` §12.4, approved on 20 November 2026).

* ``execution``: the execution model of the F3, simple and declared. It holds the fills,
  the fixed fee and slippage, and the results in R, gross and net. The simulator of the F5
  (§7) will replace it.
* ``position``: one position from its decision to its exit or to the liquidation at the end
  of the window, with its knowledge instants and its provenance (F3E4-02 and F3E4-03).
* ``engine``: a run over one instrument and one window. It takes the signals and decisions
  of the fichas, the combiner (F3E4-01) and the positions, and keeps the record.

The fichas, the combiner and the rules of a position are those of ``kbot.strategies``: the
paper trading will use the same ones, with another clock and another data layer.

The exploratory evaluation of F3-E5a (§12.5, design approved on 23 November 2026):

* ``partitions``: the partitions of the history, the folds of the development and the
  additional sample; the test is sealed.
* ``reader``: the minutes of the history on the PC and their presence, never one of the
  test.
* ``reservation``: the atomic reservation of a query of the validation and its copy
  (F3E5-01 and F3E5-06).
* ``fees``: the costs of the reports, with the fee of each instrument read from KuCoin.
* ``report``: the figures, the coverage of the contract and the report of a family
  (F3E5-02 and F3E5-05).
* ``explore``: the tool, ``python -m kbot.backtest.explore``.
"""
