-- Phase 1: empty schemas, hardened public schema and the DM risk limits.
-- Runs as kbot_owner (SET ROLE in migrations/env.py), so kbot_owner owns every object.

REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- Business tables arrive with the phase that needs them.
CREATE SCHEMA market;
CREATE SCHEMA trading;
CREATE SCHEMA ledger;
CREATE SCHEMA news;
CREATE SCHEMA ai;
CREATE SCHEMA ops;
CREATE SCHEMA config;

-- DM §6 limits, writable only by kbot_owner through migrations (spec 02 §6.1).
-- Percentages are stored as written in the DM: 0.5 means 0.5 per cent.
CREATE TABLE config.risk_limits (
    version integer PRIMARY KEY CHECK (version > 0),
    initial_capital_usdt numeric NOT NULL CHECK (initial_capital_usdt > 0),
    max_risk_per_trade_pct numeric NOT NULL CHECK (max_risk_per_trade_pct > 0),
    max_daily_loss_pct numeric NOT NULL CHECK (max_daily_loss_pct > 0),
    max_futures_leverage numeric NOT NULL CHECK (max_futures_leverage >= 1),
    max_drawdown_pct numeric NOT NULL CHECK (max_drawdown_pct > 0),
    stop_loss_required boolean NOT NULL,
    futures_margin_mode text NOT NULL CHECK (futures_margin_mode = 'isolated'),
    averaging_down_forbidden boolean NOT NULL,
    limits_hash text NOT NULL CHECK (limits_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

COMMENT ON TABLE config.risk_limits IS
    'DM section 6 risk limits. Insert-only through migrations; the highest version is in force. '
    'limits_hash is SHA-256 of the RFC 8785 canonical JSON of the limit values.';

INSERT INTO config.risk_limits (
    version,
    initial_capital_usdt,
    max_risk_per_trade_pct,
    max_daily_loss_pct,
    max_futures_leverage,
    max_drawdown_pct,
    stop_loss_required,
    futures_margin_mode,
    averaging_down_forbidden,
    limits_hash
) VALUES (
    1,
    10000,
    0.5,
    2,
    2,
    10,
    true,
    'isolated',
    true,
    'a9c038cb25b193017be417fa0915ae6076f3d94c26b2fac7c0a8d3697ff57ec1'
);

-- Readers of the limits: risk applies them, the watchdog checks them.
GRANT USAGE ON SCHEMA config TO kbot_risk, kbot_watchdog;
GRANT SELECT ON TABLE config.risk_limits TO kbot_risk, kbot_watchdog;
