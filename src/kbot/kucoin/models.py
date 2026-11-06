"""Contracts of the KuCoin payloads that the bot reads (07 §2 E1, A4).

The models are strict about types so that a format change fails loudly: UTA V2 sends
every decimal as a string, Classic Futures sends JSON numbers, and timestamps are
integers whose unit depends on the field (s, ms or ns; see ``docs/kucoin/matriz_v1_v2.md``).
Fields the bot does not use are ignored. Decimals are exact (``Decimal``) and finite.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _decimal_from_text(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"expected a decimal as a string, got {type(value).__name__}")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("decimal must be finite")
    return number


def _decimal_from_number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError(f"expected a JSON number, got {type(value).__name__}")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("decimal must be finite")
    return number


def _strict_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected an integer, got {type(value).__name__}")
    return value


TextDecimal = Annotated[Decimal, BeforeValidator(_decimal_from_text)]
NumberDecimal = Annotated[Decimal, BeforeValidator(_decimal_from_number)]
Int = Annotated[int, BeforeValidator(_strict_int)]


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Envelope(Model):
    code: Literal["200000"]
    data: Any


# --- UTA V2 (REST) -----------------------------------------------------------------


class SpotInstrument(Model):
    symbol: str
    baseCurrency: str  # KuCoin's field names
    quoteCurrency: str
    tickSize: TextDecimal
    baseOrderStep: TextDecimal
    minBaseOrderSize: TextDecimal
    minFunds: TextDecimal
    feeCurrency: str
    tradingStatus: str


class FuturesInstrument(Model):
    symbol: str
    baseCurrency: str
    quoteCurrency: str
    settlementCurrency: str
    isInverse: bool
    tickSize: TextDecimal
    lotSize: TextDecimal
    unitSize: TextDecimal  # contract multiplier, in base currency
    maxLeverage: TextDecimal
    makerFeeRate: TextDecimal
    takerFeeRate: TextDecimal
    tradingStatus: str


class InstrumentList(Model):
    tradeType: Literal["SPOT", "FUTURES"]
    list: list[dict[str, Any]]


class Candle(Model):
    """One REST candle. Columns confirmed against the labelled WebSocket candle."""

    open_time_s: Int
    open: TextDecimal
    high: TextDecimal
    low: TextDecimal
    close: TextDecimal
    volume: TextDecimal
    turnover: TextDecimal

    @model_validator(mode="before")
    @classmethod
    def _from_row(cls, value: Any) -> Any:
        if isinstance(value, list):
            if len(value) != 7:
                raise ValueError(f"a candle row has 7 columns, got {len(value)}")
            names = ("open_time_s", "open", "high", "low", "close", "volume", "turnover")
            return dict(zip(names, value, strict=True))
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Candle:
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("candle prices are inconsistent")
        if self.low <= 0 or self.volume < 0 or self.turnover < 0:
            raise ValueError("candle has a non-positive price or a negative volume")
        if self.open_time_s % 60:
            raise ValueError("a candle opens on a whole minute")
        return self


class KlineData(Model):
    tradeType: Literal["SPOT", "FUTURES"]
    symbol: str
    klineType: Literal["TRADE"] | None = None
    list: list[Candle]


class Trade(Model):
    sequence: Int
    tradeId: str
    price: TextDecimal
    size: TextDecimal
    side: Literal["BUY", "SELL"]
    ts: Int  # ns


class TradeList(Model):
    tradeType: Literal["SPOT", "FUTURES"]
    list: list[Trade]


class Ticker(Model):
    symbol: str
    bestBidPrice: TextDecimal
    bestBidSize: TextDecimal
    bestAskPrice: TextDecimal
    bestAskSize: TextDecimal
    lastPrice: TextDecimal
    baseVolume: TextDecimal
    quoteVolume: TextDecimal


class TickerList(Model):
    tradeType: Literal["SPOT", "FUTURES"]
    ts: Int  # ns
    list: list[Ticker]


class FundingRate(Model):
    symbol: str
    nextFundingRate: TextDecimal
    fundingTime: Int  # ms
    fundingRateCap: TextDecimal
    fundingRateFloor: TextDecimal
    currentGranularity: Int  # ms


class FundingPoint(Model):
    fundingRate: TextDecimal
    ts: Int  # ms


class FundingHistory(Model):
    symbol: str
    list: list[FundingPoint]


# --- Classic Futures (REST; JSON numbers) ---------------------------------------------


class Contract(Model):
    symbol: str
    status: str
    baseCurrency: str
    quoteCurrency: str
    settleCurrency: str
    isInverse: bool
    multiplier: NumberDecimal
    lotSize: NumberDecimal
    tickSize: NumberDecimal
    indexSymbol: str
    fundingRateSymbol: str
    maxLeverage: Int


class RiskTier(Model):
    symbol: str
    level: Int
    minRiskLimit: NumberDecimal
    maxRiskLimit: NumberDecimal
    maxLeverage: Int
    initialMargin: NumberDecimal
    maintainMargin: NumberDecimal


class MarkPrice(Model):
    symbol: str
    granularity: Int
    timePoint: Int  # ms
    value: NumberDecimal
    indexPrice: NumberDecimal


class IndexPoint(Model):
    symbol: str
    granularity: Int
    timePoint: Int  # ms
    value: NumberDecimal


class IndexPage(Model):
    dataList: list[IndexPoint]
    hasMore: bool


# --- UTA WebSocket ------------------------------------------------------------------

Level = tuple[TextDecimal, TextDecimal]


class BookData(Model):
    s: str
    C: Int  # sequence of the snapshot
    O: Int  # noqa: E741 - KuCoin's field name
    M: Int  # ns; meaning not documented (B1)
    a: list[Level]
    b: list[Level]

    @model_validator(mode="after")
    def _ordered(self) -> BookData:
        asks = [price for price, _ in self.a]
        bids = [price for price, _ in self.b]
        if asks != sorted(set(asks)) or bids != sorted(set(bids), reverse=True):
            raise ValueError("book levels are not strictly ordered")
        if any(size <= 0 for _, size in self.a + self.b) or any(p <= 0 for p in asks + bids):
            raise ValueError("book level with a non-positive price or size")
        if asks and bids and bids[0] >= asks[0]:
            raise ValueError("crossed book")
        return self


class BookMessage(Model):
    T: Literal["obu.SPOT", "obu.FUTURES"]
    dp: Literal["5", "50"]
    t: Literal["snapshot"]
    P: Int  # ns
    d: BookData

    @model_validator(mode="after")
    def _depth(self) -> BookMessage:
        if max(len(self.d.a), len(self.d.b)) > int(self.dp):
            raise ValueError("more levels than the subscribed depth")
        return self


class TradeData(Model):
    s: str
    p: TextDecimal
    q: TextDecimal
    S: Literal["buy", "sell"]
    M: Int  # ns
    ti: str


class TradeMessage(Model):
    T: Literal["trade.SPOT", "trade.FUTURES"]
    P: Int
    d: TradeData


class KlineData1m(Model):
    s: str
    i: Literal["1min"]
    O: Int  # noqa: E741 - open time, s
    C: Int  # close time, s
    o: TextDecimal
    h: TextDecimal
    l: TextDecimal  # noqa: E741
    c: TextDecimal
    v: TextDecimal
    a: TextDecimal  # turnover

    @model_validator(mode="after")
    def _consistent(self) -> KlineData1m:
        if self.C - self.O != 60 or self.O % 60:
            raise ValueError("a 1min candle spans one whole minute")
        if not self.l <= min(self.o, self.c) <= max(self.o, self.c) <= self.h:
            raise ValueError("candle prices are inconsistent")
        return self


class KlineMessage(Model):
    T: Literal["kline.SPOT", "kline.FUTURES"]
    P: Int
    d: KlineData1m


class MarkPriceData(Model):
    s: str
    mp: TextDecimal
    ip: TextDecimal
    oi: TextDecimal
    ts: Int  # ms


class MarkPriceMessage(Model):
    T: Literal["mark-price"]
    P: Int
    d: MarkPriceData


class TickerData(Model):
    s: str
    a: TextDecimal
    A: TextDecimal
    b: TextDecimal
    B: TextDecimal
    l: TextDecimal  # noqa: E741 - last price
    q: TextDecimal
    M: Int  # ns


class TickerMessage(Model):
    T: Literal["ticker.SPOT", "ticker.FUTURES"]
    P: Int
    d: TickerData


class Welcome(Model):
    message: Literal["welcome"]
    sessionId: str
    pingInterval: Int = Field(ge=1000)  # ms


class Ack(Model):
    id: str
    result: Literal[True]


class Pong(Model):
    type: Literal["pong"]
    id: str
    ts: Int  # ns
