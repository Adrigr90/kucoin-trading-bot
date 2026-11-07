-- Phase 2, stage E2: market data tables (spec 02 §3.8). Runs as kbot_owner.
--
-- Insert-only: kbot_ingest gets SELECT and INSERT and nothing else (02 §8 and §9).
-- Terminal results are written once; a repeated insert uses ON CONFLICT DO NOTHING and
-- never replaces the first result. Consistency between related rows is enforced here,
-- not only in Python (02 §0 rule 6): composite foreign keys that include the state make
-- it impossible to store metrics of a slot that is not valid, a candle whose evidence
-- contradicts the flow of its minute, or a minute status that contradicts its candle.
-- Closing order of a minute: data_quality_minute, then candles, then minute_status.

CREATE TABLE market.instruments (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL CHECK (symbol ~ '^[A-Z0-9]{1,20}(-[A-Z0-9]{1,20})?$'),
    observed_at timestamptz NOT NULL,
    base_currency text NOT NULL,
    quote_currency text NOT NULL,
    settle_currency text,
    tick_size numeric NOT NULL CHECK (tick_size > 0),
    quantity_step numeric NOT NULL CHECK (quantity_step > 0),
    multiplier numeric NOT NULL CHECK (multiplier > 0),
    min_quantity numeric CHECK (min_quantity >= 0),
    min_funds numeric CHECK (min_funds >= 0),
    trading_status text NOT NULL,
    is_trading boolean NOT NULL,
    raw jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, observed_at),
    CHECK (market = 'FUTURES' OR (multiplier = 1 AND settle_currency IS NULL))
);

CREATE TABLE market.risk_tiers (
    market text NOT NULL CHECK (market = 'FUTURES'),
    symbol text NOT NULL CHECK (symbol ~ '^[A-Z0-9]{1,20}$'),
    observed_at timestamptz NOT NULL,
    level int4 NOT NULL CHECK (level > 0),
    min_risk_limit numeric NOT NULL CHECK (min_risk_limit >= 0),
    max_risk_limit numeric NOT NULL,
    max_leverage int4 NOT NULL CHECK (max_leverage > 0),
    initial_margin numeric NOT NULL,
    maintain_margin numeric NOT NULL CHECK (maintain_margin > 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, observed_at, level),
    CHECK (max_risk_limit > min_risk_limit),
    CHECK (initial_margin > maintain_margin)
);

-- Flow state of each channel and minute (02 §3.4). Written first when a minute closes.
CREATE TABLE market.data_quality_minute (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    channel text NOT NULL
        CHECK (channel IN ('trade', 'ticker', 'kline', 'mark-price', 'obu5', 'obu50')),
    minute_start timestamptz NOT NULL,
    flow_state text NOT NULL CHECK (flow_state IN ('S1', 'S2', 'S3')),
    messages int4 NOT NULL CHECK (messages >= 0),
    anomalies int4 NOT NULL CHECK (anomalies >= 0),
    sequence_breaks int4 NOT NULL CHECK (sequence_breaks >= 0),
    reconnections int4 NOT NULL CHECK (reconnections >= 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, channel, minute_start),
    UNIQUE (market, symbol, channel, minute_start, flow_state),
    CHECK (flow_state <> 'S1' OR (anomalies = 0 AND sequence_breaks = 0 AND reconnections = 0))
);

-- Terminal candles only (02 §3.4); open or stabilizing candles live in memory.
CREATE TABLE market.candles (
    series text NOT NULL CHECK (series IN ('last', 'mark', 'index')),
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    open_time timestamptz NOT NULL,
    open numeric NOT NULL,
    high numeric NOT NULL,
    low numeric NOT NULL CHECK (low > 0),
    close numeric NOT NULL,
    volume numeric CHECK (volume >= 0),
    turnover numeric CHECK (turnover >= 0),
    state text NOT NULL
        CHECK (state IN ('cerrada_rest', 'cerrada_cruzada', 'discrepante', 'cerrada_propia')),
    evidence text NOT NULL CHECK (evidence IN (
        'vela_cruzada_consistente', 'vela_cruzada_discrepante', 'vela_rest_suficiente',
        'construida_flujo', 'reconstruida_classic'
    )),
    samples int4,
    flow_channel text,
    flow_state text,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (series, market, symbol, open_time),
    UNIQUE (series, market, symbol, open_time, state),
    FOREIGN KEY (market, symbol, flow_channel, open_time, flow_state)
        REFERENCES market.data_quality_minute (market, symbol, channel, minute_start, flow_state),
    CHECK (low <= open AND low <= close AND open <= high AND close <= high),
    CHECK (
        (series = 'last' AND volume IS NOT NULL AND turnover IS NOT NULL AND samples IS NULL
            AND flow_channel = 'trade' AND flow_state IS NOT NULL
            AND ((state = 'cerrada_cruzada' AND evidence = 'vela_cruzada_consistente'
                    AND flow_state = 'S1')
                OR (state = 'discrepante' AND evidence = 'vela_cruzada_discrepante'
                    AND flow_state = 'S1')
                OR (state = 'cerrada_rest' AND evidence = 'vela_rest_suficiente'
                    AND flow_state IN ('S2', 'S3'))))
        OR (series IN ('mark', 'index') AND market = 'FUTURES' AND volume IS NULL
            AND turnover IS NULL AND state = 'cerrada_propia' AND samples = 60
            AND ((evidence = 'construida_flujo' AND flow_channel = 'mark-price'
                    AND flow_state = 'S1')
                OR (series = 'index' AND evidence = 'reconstruida_classic'
                    AND flow_channel IS NULL AND flow_state IS NULL)))
    )
);

CREATE TABLE market.data_gaps (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    channel text NOT NULL
        CHECK (channel IN ('trade', 'ticker', 'kline', 'mark-price', 'obu5', 'obu50')),
    symbol text NOT NULL,
    start_at timestamptz NOT NULL,
    end_at timestamptz NOT NULL,
    cause text NOT NULL CHECK (cause IN (
        'desconexion', 'rotacion_planificada', 'sin_pong', 'suscripcion_rechazada',
        'proceso_detenido', 'mensaje_invalido'
    )),
    detail text,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, channel, symbol, start_at),
    CHECK (end_at > start_at)
);

-- Terminal status of each minute of the last series, at the close of the day (02 §3.5).
-- The table of §3.5 is the CHECK below; the foreign keys tie the row to the flow state and
-- to the candle it was decided from, so neither can be contradicted.
CREATE TABLE market.minute_status (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    minute_start timestamptz NOT NULL,
    status text NOT NULL
        CHECK (status IN ('datos', 'hueco', 'sin_operaciones_probable', 'sin_explicar')),
    candle_state text NOT NULL CHECK (candle_state IN (
        'cerrada_rest', 'cerrada_cruzada', 'discrepante', 'ausencia_sin_trades_probable',
        'ausencia_no_verificable', 'abierta', 'pendiente_estabilizacion'
    )),
    flow_channel text NOT NULL CHECK (flow_channel = 'trade'),
    flow_state text NOT NULL,
    candle_series text CHECK (candle_series = 'last'),
    gap_start timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, minute_start),
    FOREIGN KEY (market, symbol, flow_channel, minute_start, flow_state)
        REFERENCES market.data_quality_minute (market, symbol, channel, minute_start, flow_state),
    FOREIGN KEY (candle_series, market, symbol, minute_start, candle_state)
        REFERENCES market.candles (series, market, symbol, open_time, state),
    FOREIGN KEY (market, flow_channel, symbol, gap_start)
        REFERENCES market.data_gaps (market, channel, symbol, start_at),
    CHECK (
        (status = 'datos' AND candle_series = 'last' AND gap_start IS NULL
            AND (candle_state IN ('cerrada_rest', 'cerrada_cruzada')
                OR (candle_state = 'discrepante' AND flow_state = 'S1')))
        OR (status = 'sin_operaciones_probable' AND candle_series IS NULL AND gap_start IS NULL
            AND candle_state = 'ausencia_sin_trades_probable' AND flow_state = 'S1')
        OR (status = 'hueco' AND candle_series IS NULL AND gap_start IS NOT NULL
            AND candle_state = 'ausencia_no_verificable' AND flow_state IN ('S2', 'S3'))
        OR (status = 'sin_explicar' AND candle_series IS NULL AND gap_start IS NULL
            AND candle_state IN (
                'ausencia_no_verificable', 'abierta', 'pendiente_estabilizacion'
            ))
    )
);

-- Exactly one terminal state per channel, instrument and 10-second slot (02 §3.5).
CREATE TABLE market.depth_slot_status (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    depth int2 NOT NULL CHECK (depth IN (5, 50)),
    slot_start timestamptz NOT NULL,
    state text NOT NULL CHECK (state IN (
        'valida', 'libro_inconsistente', 'no_recibida', 'desconectado', 'fuera_de_plazo'
    )),
    sequence int8 CHECK (sequence >= 0),
    snapshot_age_ms int8 CHECK (snapshot_age_ms >= 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, depth, slot_start),
    UNIQUE (market, symbol, depth, slot_start, state),
    CHECK (state <> 'valida' OR (sequence IS NOT NULL AND snapshot_age_ms IS NOT NULL)),
    CHECK (state NOT IN ('desconectado', 'no_recibida')
        OR (sequence IS NULL AND snapshot_age_ms IS NULL))
);

-- Metrics exist only for a valid slot: the foreign key includes state = 'valida'.
-- Exact values only; ratios are computed when reading (02 §0 rule 5).
CREATE TABLE market.depth_metrics (
    market text NOT NULL,
    symbol text NOT NULL,
    depth int2 NOT NULL,
    slot_start timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'valida' CHECK (state = 'valida'),
    best_bid numeric NOT NULL CHECK (best_bid > 0),
    best_ask numeric NOT NULL,
    mid numeric NOT NULL,
    spread numeric NOT NULL,
    bid_levels int2 NOT NULL,
    ask_levels int2 NOT NULL,
    bid_quantity numeric NOT NULL CHECK (bid_quantity > 0),
    ask_quantity numeric NOT NULL CHECK (ask_quantity > 0),
    bid_notional numeric NOT NULL,
    ask_notional numeric NOT NULL,
    bid_notional_10bps numeric NOT NULL CHECK (bid_notional_10bps >= 0),
    ask_notional_10bps numeric NOT NULL CHECK (ask_notional_10bps >= 0),
    bid_notional_50bps numeric NOT NULL,
    ask_notional_50bps numeric NOT NULL,
    bid_deepest_price numeric NOT NULL CHECK (bid_deepest_price > 0),
    ask_deepest_price numeric NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, depth, slot_start),
    FOREIGN KEY (market, symbol, depth, slot_start, state)
        REFERENCES market.depth_slot_status (market, symbol, depth, slot_start, state),
    CHECK (best_bid < best_ask AND spread = best_ask - best_bid AND mid * 2 = best_bid + best_ask),
    CHECK (bid_levels >= 1 AND bid_levels <= depth AND ask_levels >= 1 AND ask_levels <= depth),
    CHECK (bid_notional_10bps <= bid_notional_50bps AND bid_notional_50bps <= bid_notional),
    CHECK (ask_notional_10bps <= ask_notional_50bps AND ask_notional_50bps <= ask_notional),
    CHECK (bid_deepest_price <= best_bid AND ask_deepest_price >= best_ask)
);

-- The last valid book of each minute; the foreign key points at the valid slot it came from.
CREATE TABLE market.depth_snapshots (
    market text NOT NULL,
    symbol text NOT NULL,
    depth int2 NOT NULL,
    minute_start timestamptz NOT NULL,
    slot_start timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'valida' CHECK (state = 'valida'),
    sequence int8 NOT NULL CHECK (sequence >= 0),
    published_at timestamptz NOT NULL,
    exchange_ns int8,
    received_at timestamptz NOT NULL,
    bids jsonb NOT NULL,
    asks jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, depth, minute_start),
    FOREIGN KEY (market, symbol, depth, slot_start, state)
        REFERENCES market.depth_slot_status (market, symbol, depth, slot_start, state),
    CHECK (slot_start >= minute_start)
);

-- Settled rates are observed once (observed_at = funding_time); predicted rates
-- (nextFundingRate) are kept for every observation.
CREATE TABLE market.funding_rates (
    market text NOT NULL CHECK (market = 'FUTURES'),
    symbol text NOT NULL CHECK (symbol ~ '^[A-Z0-9]{1,20}$'),
    kind text NOT NULL CHECK (kind IN ('liquidado', 'previsto')),
    funding_time timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    rate numeric NOT NULL,
    granularity_ms int8 CHECK (granularity_ms > 0),
    rate_cap numeric,
    rate_floor numeric,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, kind, funding_time, observed_at),
    CHECK (kind = 'previsto' OR observed_at = funding_time),
    CHECK (rate_cap IS NULL OR rate_floor IS NULL OR rate_floor < rate_cap)
);

CREATE TABLE market.universe_snapshots (
    evaluated_at timestamptz NOT NULL,
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    rank int4 NOT NULL CHECK (rank > 0),
    in_universe boolean NOT NULL,
    l50_candidate boolean NOT NULL,
    quote_volume_24h numeric NOT NULL CHECK (quote_volume_24h >= 0),
    best_bid numeric CHECK (best_bid > 0),
    best_ask numeric,
    criteria_version text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (evaluated_at, market, symbol),
    UNIQUE (evaluated_at, market, rank),
    CHECK (in_universe OR NOT l50_candidate),
    CHECK (best_bid IS NULL OR best_ask IS NULL OR best_bid < best_ask)
);

COMMENT ON SCHEMA market IS
    'Public KuCoin market data (02 §3.8). Insert-only for kbot_ingest; terminal rows only.';

-- kbot_ingest: SELECT and INSERT in market, nothing else (02 §9).
GRANT USAGE ON SCHEMA market TO kbot_ingest;
GRANT SELECT, INSERT ON TABLE
    market.instruments, market.risk_tiers, market.data_quality_minute, market.candles,
    market.data_gaps, market.minute_status, market.depth_slot_status, market.depth_metrics,
    market.depth_snapshots, market.funding_rates, market.universe_snapshots
    TO kbot_ingest;
