-- Phase 3, stage F3-E5b (02 §12.6): the approved versions of the fichas and the signals.
-- Runs as kbot_owner (SET ROLE in migrations/env.py), so kbot_owner owns every object.

-- The approved versions of the parameters of each ficha (02 §12.6.2). Insert-only through
-- migrations, like config.risk_limits: no service writes config. Empty here: which families
-- continue is the owner's decision (02 §12.5.7), and each approval comes in its own migration.
CREATE TABLE config.strategy_versions (
    family text NOT NULL CHECK (family IN ('tendencia', 'breakout', 'reversion_media')),
    params_hash text NOT NULL CHECK (params_hash ~ '^[0-9a-f]{64}$'),
    -- The RFC 8785 canonical JSON whose SHA-256 is params_hash (02 §12.3.3).
    params text NOT NULL CHECK (params <> ''),
    ficha text NOT NULL CHECK (ficha ~ '^§12\.3\.[0-9]+$'),
    approved_on date NOT NULL,
    approval text NOT NULL CHECK (approval <> ''),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (family, params_hash)
);

COMMENT ON TABLE config.strategy_versions IS
    'Approved versions of the parameters of each ficha (02 §12.6.2). Insert-only through '
    'migrations. The version of a signal is its code commit plus params_hash.';

-- One row per signal (02 §12.6.1), unique by the key of 02 §1.3, insert-only.
CREATE TABLE trading.signals (
    signal_id uuid PRIMARY KEY,
    family text NOT NULL CHECK (family IN ('tendencia', 'breakout', 'reversion_media')),
    code_commit text NOT NULL CHECK (code_commit ~ '^[0-9a-f]{40}$'),
    params_hash text NOT NULL CHECK (params_hash ~ '^[0-9a-f]{64}$'),
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL CHECK (symbol ~ '^[A-Z0-9]{1,20}(-[A-Z0-9]{1,20})?$'),
    timeframe text NOT NULL CHECK (timeframe IN ('5m', '15m', '1h', '4h', '1d')),
    open_time timestamptz NOT NULL,
    direction int2 NOT NULL CHECK (direction IN (-1, 1)),
    signal_at timestamptz NOT NULL,
    context_open timestamptz NOT NULL,
    context_direction text CHECK (context_direction IN ('tendencia', 'rango', 'transicion')),
    context_volatility text CHECK (context_volatility IN ('expansion', 'compresion', 'normal')),
    signal_direction text CHECK (signal_direction IN ('tendencia', 'rango', 'transicion')),
    signal_volatility text CHECK (signal_volatility IN ('expansion', 'compresion', 'normal')),
    levels jsonb NOT NULL,
    provenance jsonb NOT NULL,
    provenance_minutes int4 NOT NULL CHECK (provenance_minutes > 0),
    rest_only_minutes int4 NOT NULL CHECK (rest_only_minutes >= 0),
    run_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (family, code_commit, params_hash, market, symbol, timeframe, open_time, direction),
    FOREIGN KEY (family, params_hash) REFERENCES config.strategy_versions (family, params_hash),
    -- Spot only goes long (02 §6.2.1).
    CHECK (market = 'FUTURES' OR direction = 1),
    CHECK (rest_only_minutes <= provenance_minutes),
    CHECK (date_trunc('minute', open_time AT TIME ZONE 'UTC') = open_time AT TIME ZONE 'UTC'),
    CHECK (signal_at > open_time),
    CHECK (context_open < signal_at),
    -- A signal is never recorded before it exists.
    CHECK (recorded_at >= signal_at)
);

COMMENT ON TABLE trading.signals IS
    'Signals of the approved versions (02 §12.6.1). Insert-only: kbot_strategy only inserts, '
    'with ON CONFLICT DO NOTHING; nobody updates or deletes.';

-- kbot_strategy (02 §12.6.3; F3-A10): reads the recorded candles, the instruments and the
-- universe, and the approved versions, and inserts signals. Nothing else.
GRANT USAGE ON SCHEMA market, trading, config TO kbot_strategy;
GRANT SELECT ON TABLE market.candles, market.instruments, market.universe_snapshots
    TO kbot_strategy;
GRANT SELECT ON TABLE config.strategy_versions TO kbot_strategy;
GRANT INSERT ON TABLE trading.signals TO kbot_strategy;
