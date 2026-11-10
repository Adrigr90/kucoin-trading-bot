-- Phase 2, stage E3: historical storage (spec 02 §3.9). Runs as kbot_owner, in the one
-- transaction of the migration.
--
-- 0002 is already in main and never changes (owner decision of 9 November 2026). This
-- migration replaces its twelve time-keyed tables with the same tables partitioned by UTC
-- day. No database holds data in them yet, and this migration refuses to run if one does:
-- it first locks the twelve tables in a fixed order, so that no other connection can
-- write between the check and the drop, then adds a CHECK (false), which fails on the
-- first row that exists. On data or on lock contention it aborts and changes nothing.

SET LOCAL lock_timeout = '5s';

LOCK TABLE
    market.minute_status, market.candles, market.ws_candles, market.exhausted_index_rebuilds,
    market.trading_seen_late, market.late_ws_candles, market.late_ws_repeats,
    market.depth_metrics, market.depth_snapshots, market.depth_late_books,
    market.data_quality_minute, market.depth_slot_status
    IN ACCESS EXCLUSIVE MODE;

ALTER TABLE market.minute_status ADD CONSTRAINT minute_status_vacia_0003 CHECK (false);
ALTER TABLE market.candles ADD CONSTRAINT candles_vacia_0003 CHECK (false);
ALTER TABLE market.ws_candles ADD CONSTRAINT ws_candles_vacia_0003 CHECK (false);
ALTER TABLE market.exhausted_index_rebuilds
    ADD CONSTRAINT exhausted_index_rebuilds_vacia_0003 CHECK (false);
ALTER TABLE market.trading_seen_late ADD CONSTRAINT trading_seen_late_vacia_0003 CHECK (false);
ALTER TABLE market.late_ws_candles ADD CONSTRAINT late_ws_candles_vacia_0003 CHECK (false);
ALTER TABLE market.late_ws_repeats ADD CONSTRAINT late_ws_repeats_vacia_0003 CHECK (false);
ALTER TABLE market.depth_metrics ADD CONSTRAINT depth_metrics_vacia_0003 CHECK (false);
ALTER TABLE market.depth_snapshots ADD CONSTRAINT depth_snapshots_vacia_0003 CHECK (false);
ALTER TABLE market.depth_late_books ADD CONSTRAINT depth_late_books_vacia_0003 CHECK (false);
ALTER TABLE market.data_quality_minute
    ADD CONSTRAINT data_quality_minute_vacia_0003 CHECK (false);
ALTER TABLE market.depth_slot_status ADD CONSTRAINT depth_slot_status_vacia_0003 CHECK (false);

-- No CASCADE: the twelve go together, and nothing else depends on them.
DROP TABLE
    market.minute_status, market.candles, market.ws_candles, market.exhausted_index_rebuilds,
    market.trading_seen_late, market.late_ws_candles, market.late_ws_repeats,
    market.depth_metrics, market.depth_snapshots, market.depth_late_books,
    market.data_quality_minute, market.depth_slot_status;

-- The same tables as in 0002 (comments there), partitioned by the UTC day of their time
-- column; every primary and unique key already includes it. Partitions are created by
-- market.prepare_partitions(), never here: a fixed date would be wrong on any other day.
-- Every minute column is aligned to the UTC minute, so a minute belongs to one UTC day.

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
    CHECK (flow_state <> 'S1' OR (anomalies = 0 AND sequence_breaks = 0 AND reconnections = 0)),
    CHECK (date_trunc('minute', minute_start AT TIME ZONE 'UTC') = minute_start AT TIME ZONE 'UTC')
) PARTITION BY RANGE (minute_start);

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
    ws_volume numeric CHECK (ws_volume >= 0),
    ws_turnover numeric CHECK (ws_turnover >= 0),
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
            AND flow_channel IS NOT NULL AND flow_channel = 'trade' AND flow_state IS NOT NULL
            AND ((state = 'cerrada_cruzada' AND evidence = 'vela_cruzada_consistente'
                    AND flow_state = 'S1'
                    AND ((ws_volume IS NOT NULL AND ws_turnover IS NOT NULL)
                        OR (volume = 0 AND turnover = 0 AND ws_volume IS NULL
                            AND ws_turnover IS NULL)))
                OR (state = 'discrepante' AND evidence = 'vela_cruzada_discrepante'
                    AND flow_state = 'S1')
                OR (state = 'cerrada_rest' AND evidence = 'vela_rest_suficiente'
                    AND flow_state IN ('S2', 'S3') AND ws_volume IS NULL AND ws_turnover IS NULL)))
        OR (series IN ('mark', 'index') AND market = 'FUTURES' AND volume IS NULL
            AND turnover IS NULL AND ws_volume IS NULL AND ws_turnover IS NULL
            AND state = 'cerrada_propia' AND samples IS NOT NULL AND samples = 60
            AND ((evidence = 'construida_flujo' AND flow_channel IS NOT NULL
                    AND flow_channel = 'mark-price' AND flow_state IS NOT NULL
                    AND flow_state = 'S1')
                OR (series = 'index' AND evidence = 'reconstruida_classic'
                    AND flow_channel IS NULL AND flow_state IS NULL)))
    ),
    CHECK (date_trunc('minute', open_time AT TIME ZONE 'UTC') = open_time AT TIME ZONE 'UTC')
) PARTITION BY RANGE (open_time);

CREATE TABLE market.ws_candles (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    open_time timestamptz NOT NULL,
    open numeric NOT NULL,
    high numeric NOT NULL,
    low numeric NOT NULL CHECK (low > 0),
    close numeric NOT NULL,
    volume numeric NOT NULL CHECK (volume >= 0),
    turnover numeric NOT NULL CHECK (turnover >= 0),
    published_at timestamptz NOT NULL,
    traded boolean NOT NULL,
    flow_channel text NOT NULL DEFAULT 'trade' CHECK (flow_channel = 'trade'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, open_time),
    FOREIGN KEY (market, symbol, flow_channel, open_time)
        REFERENCES market.data_quality_minute (market, symbol, channel, minute_start),
    CHECK (low <= open AND low <= close AND open <= high AND close <= high),
    CHECK (volume = 0 OR traded),
    CHECK (date_trunc('minute', open_time AT TIME ZONE 'UTC') = open_time AT TIME ZONE 'UTC')
) PARTITION BY RANGE (open_time);

CREATE TABLE market.exhausted_index_rebuilds (
    market text NOT NULL CHECK (market = 'FUTURES'),
    symbol text NOT NULL,
    minute_start timestamptz NOT NULL,
    attempts int2 NOT NULL CHECK (attempts > 0),
    flow_channel text NOT NULL DEFAULT 'mark-price' CHECK (flow_channel = 'mark-price'),
    flow_state text NOT NULL CHECK (flow_state IN ('S2', 'S3')),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, minute_start),
    FOREIGN KEY (market, symbol, flow_channel, minute_start, flow_state)
        REFERENCES market.data_quality_minute (market, symbol, channel, minute_start, flow_state),
    CHECK (date_trunc('minute', minute_start AT TIME ZONE 'UTC') = minute_start AT TIME ZONE 'UTC')
) PARTITION BY RANGE (minute_start);

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
        (status = 'datos' AND candle_series IS NOT NULL AND candle_series = 'last'
            AND gap_start IS NULL
            AND (candle_state IN ('cerrada_rest', 'cerrada_cruzada')
                OR (candle_state = 'discrepante' AND flow_state = 'S1')))
        OR (status = 'sin_operaciones_probable' AND candle_series IS NULL AND gap_start IS NULL
            AND candle_state = 'ausencia_sin_trades_probable' AND flow_state = 'S1')
        OR (status = 'hueco' AND candle_series IS NULL AND gap_start IS NOT NULL
            AND candle_state = 'ausencia_no_verificable' AND flow_state IN ('S2', 'S3'))
        OR (status = 'sin_explicar' AND candle_series IS NULL AND gap_start IS NULL
            AND ((candle_state = 'ausencia_no_verificable' AND flow_state IN ('S2', 'S3'))
                OR candle_state IN ('abierta', 'pendiente_estabilizacion')))
    ),
    CHECK (date_trunc('minute', minute_start AT TIME ZONE 'UTC') = minute_start AT TIME ZONE 'UTC')
) PARTITION BY RANGE (minute_start);

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
) PARTITION BY RANGE (slot_start);

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
) PARTITION BY RANGE (slot_start);

-- The slot of a minute's snapshot lies inside that minute, and the minute is aligned to the
-- UTC minute: a snapshot never points at a slot of another day (review of E3).
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
    CHECK (slot_start >= minute_start AND slot_start - minute_start < '00:01:00'),
    CHECK (date_trunc('minute', minute_start AT TIME ZONE 'UTC') = minute_start AT TIME ZONE 'UTC')
) PARTITION BY RANGE (minute_start);

-- Evidence never crosses a day (review of the E3 design, second round): a late book whose
-- range of slots, from the slot of its P to the last slot closed when it arrived, crosses
-- midnight is stored as one row per UTC day it affects. Each row names only slots of its own
-- day (first_slot_start to last_slot_start), so retiring a day never changes what the slots
-- of another day count as. slot_start keeps the slot of the book's P.
CREATE TABLE market.depth_late_books (
    market text NOT NULL,
    symbol text NOT NULL,
    depth int2 NOT NULL,
    slot_start timestamptz NOT NULL,
    first_slot_start timestamptz NOT NULL,
    last_slot_start timestamptz NOT NULL,
    sequence int8 NOT NULL CHECK (sequence >= 0),
    published_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, depth, first_slot_start, sequence),
    FOREIGN KEY (market, symbol, depth, first_slot_start)
        REFERENCES market.depth_slot_status (market, symbol, depth, slot_start),
    FOREIGN KEY (market, symbol, depth, last_slot_start)
        REFERENCES market.depth_slot_status (market, symbol, depth, slot_start),
    CHECK (published_at >= slot_start AND first_slot_start >= slot_start
        AND last_slot_start >= first_slot_start),
    CHECK (date_trunc('day', first_slot_start AT TIME ZONE 'UTC')
        = date_trunc('day', last_slot_start AT TIME ZONE 'UTC'))
) PARTITION BY RANGE (first_slot_start);

CREATE TABLE market.trading_seen_late (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    minute_start timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    flow_channel text NOT NULL DEFAULT 'trade' CHECK (flow_channel = 'trade'),
    flow_state text NOT NULL DEFAULT 'S1' CHECK (flow_state = 'S1'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, minute_start),
    FOREIGN KEY (market, symbol, flow_channel, minute_start, flow_state)
        REFERENCES market.data_quality_minute (market, symbol, channel, minute_start, flow_state),
    CHECK (date_trunc('minute', minute_start AT TIME ZONE 'UTC') = minute_start AT TIME ZONE 'UTC')
) PARTITION BY RANGE (minute_start);

CREATE TABLE market.late_ws_candles (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    open_time timestamptz NOT NULL,
    published_at timestamptz NOT NULL,
    open numeric NOT NULL,
    high numeric NOT NULL,
    low numeric NOT NULL CHECK (low > 0),
    close numeric NOT NULL,
    volume numeric NOT NULL CHECK (volume >= 0),
    turnover numeric NOT NULL CHECK (turnover >= 0),
    received_at timestamptz NOT NULL,
    latest boolean NOT NULL,
    flow_channel text NOT NULL DEFAULT 'trade' CHECK (flow_channel = 'trade'),
    flow_state text NOT NULL DEFAULT 'S1' CHECK (flow_state = 'S1'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, open_time, published_at, latest),
    FOREIGN KEY (market, symbol, flow_channel, open_time, flow_state)
        REFERENCES market.data_quality_minute (market, symbol, channel, minute_start, flow_state),
    CHECK (low <= open AND low <= close AND open <= high AND close <= high),
    CHECK (latest OR volume > 0),
    CHECK (date_trunc('minute', open_time AT TIME ZONE 'UTC') = open_time AT TIME ZONE 'UTC')
) PARTITION BY RANGE (open_time);

CREATE TABLE market.late_ws_repeats (
    market text NOT NULL CHECK (market IN ('SPOT', 'FUTURES')),
    symbol text NOT NULL,
    open_time timestamptz NOT NULL,
    published_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    flow_channel text NOT NULL DEFAULT 'trade' CHECK (flow_channel = 'trade'),
    flow_state text NOT NULL DEFAULT 'S1' CHECK (flow_state = 'S1'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market, symbol, open_time, published_at),
    FOREIGN KEY (market, symbol, flow_channel, open_time, flow_state)
        REFERENCES market.data_quality_minute (market, symbol, channel, minute_start, flow_state),
    CHECK (date_trunc('minute', open_time AT TIME ZONE 'UTC') = open_time AT TIME ZONE 'UTC')
) PARTITION BY RANGE (open_time);

-- The historical archive (02 §3.9). Insert-only: every state change is a new row.

-- A sealed day: its twelve partitions carry CHECK (false) NOT VALID, so the rows they hold
-- stay and no new row can enter. Written only by market.seal_day.
CREATE TABLE market.archive_seals (
    day date NOT NULL PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- A published generation of a sealed day: its twelve Parquet files, also the empty ones,
-- read from one REPEATABLE READ snapshot taken after the seal.
CREATE TABLE market.archive_generations (
    generation_id uuid NOT NULL PRIMARY KEY,
    day date NOT NULL REFERENCES market.archive_seals (day),
    schema_version text NOT NULL,
    format_version text NOT NULL,
    snapshot_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (generation_id, day)
);

CREATE TABLE market.archive_files (
    generation_id uuid NOT NULL,
    day date NOT NULL,
    table_name text NOT NULL CHECK (table_name IN (
        'data_quality_minute', 'candles', 'ws_candles', 'exhausted_index_rebuilds',
        'minute_status', 'depth_slot_status', 'depth_metrics', 'depth_snapshots',
        'depth_late_books', 'trading_seen_late', 'late_ws_candles', 'late_ws_repeats'
    )),
    path text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    row_count int8 NOT NULL CHECK (row_count >= 0),
    bytes int8 NOT NULL CHECK (bytes > 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (generation_id, table_name),
    FOREIGN KEY (generation_id, day) REFERENCES market.archive_generations (generation_id, day)
);

CREATE TABLE market.archive_generation_states (
    generation_id uuid NOT NULL REFERENCES market.archive_generations (generation_id),
    state text NOT NULL CHECK (state IN (
        'archivado_local', 'replicado_fuera', 'verificado_fuera',
        'elegible_para_exclusion_backup'
    )),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (generation_id, state)
);

-- A retired day: its partitions were detached and dropped. Written only by
-- market.retire_day, which nobody may execute until stage E4 passes a restore test.
CREATE TABLE market.archive_retirements (
    day date NOT NULL PRIMARY KEY REFERENCES market.archive_seals (day),
    generation_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (generation_id, day) REFERENCES market.archive_generations (generation_id, day)
);

-- The three SECURITY DEFINER functions (02 §3.9; exception to §9 for these three only:
-- owned by kbot_owner, the owner of the tables, and with bounded dynamic SQL). Each one
-- works on a fixed list of the twelve tables, takes a typed argument or none, runs with a
-- safe search_path in UTC, builds its statements only with format(), %I and %L from one of
-- the approved templates (tools/sql_guard.py), never uses CASCADE, waits at most 5 s for
-- a lock and takes the same advisory lock, so two of them never run at once.

-- The partitions of [today - 30, today + 14] in UTC: the recovery horizon of the recorder
-- and two weeks ahead. A day that was sealed is never prepared again. A partition that
-- exists must be the one expected: same parent and bounds, owned by kbot_owner and with no
-- privilege for any other role, PUBLIC included, on the table or on any of its columns
-- (review of E3a, E3a-04).
CREATE FUNCTION market.prepare_partitions() RETURNS int4
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = market, pg_temp
    SET TimeZone = 'UTC'
    SET lock_timeout = '5s'
AS $$
DECLARE
    v_offset int4;
    v_day date;
    v_table text;
    v_name text;
    v_bounds text;
    v_created int4 := 0;
BEGIN
    PERFORM pg_advisory_xact_lock(9301);
    FOR v_offset IN -30..14 LOOP
        v_day := current_date + v_offset;
        CONTINUE WHEN EXISTS (SELECT 1 FROM market.archive_seals s WHERE s.day = v_day);
        v_bounds := format(
            'FOR VALUES FROM (%L) TO (%L)',
            v_day::timestamp AT TIME ZONE 'UTC',
            (v_day + 1)::timestamp AT TIME ZONE 'UTC'
        );
        FOREACH v_table IN ARRAY ARRAY[
            'data_quality_minute', 'candles', 'ws_candles', 'exhausted_index_rebuilds',
            'minute_status', 'depth_slot_status', 'depth_metrics', 'depth_snapshots',
            'depth_late_books', 'trading_seen_late', 'late_ws_candles', 'late_ws_repeats'
        ] LOOP
            v_name := v_table || '_p' || to_char(v_day, 'YYYYMMDD');
            IF to_regclass(format('market.%I', v_name)) IS NULL THEN
                EXECUTE format(
                    'CREATE TABLE market.%I PARTITION OF market.%I FOR VALUES FROM (%L) TO (%L)',
                    v_name, v_table,
                    v_day::timestamp AT TIME ZONE 'UTC',
                    (v_day + 1)::timestamp AT TIME ZONE 'UTC'
                );
                v_created := v_created + 1;
            ELSIF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_inherits i
                JOIN pg_catalog.pg_class c ON c.oid = i.inhrelid
                WHERE i.inhrelid = to_regclass(format('market.%I', v_name))
                  AND i.inhparent = to_regclass(format('market.%I', v_table))
                  AND pg_catalog.pg_get_expr(c.relpartbound, c.oid) = v_bounds
                  AND c.relowner = 'kbot_owner'::regrole
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_catalog.aclexplode(c.relacl) a
                      WHERE a.grantee <> c.relowner
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pg_catalog.pg_attribute t, pg_catalog.aclexplode(t.attacl) a
                      WHERE t.attrelid = c.oid AND t.attnum > 0 AND a.grantee <> c.relowner
                  )
            ) THEN
                RAISE EXCEPTION
                    'prepare_partitions: % is not the partition of % for %, owned by kbot_owner and with no other grants',
                    v_name, v_table, v_day;
            END IF;
        END LOOP;
    END LOOP;
    RETURN v_created;
END
$$;

-- Seal day D, at least 72 h after its end (a REST candle can wait 48 h). Its twelve
-- partitions get CHECK (false) NOT VALID: the rows stay and no new row can enter. The
-- fences go first: each ALTER waits for the writers of its partition. Only then the day
-- must be closed, read in READ COMMITTED so that every write committed before the fences
-- is seen; otherwise the whole call is undone, fences included (review of E3a).
--
-- Closed means that every instrument of the day has its 1,440 terminal statuses. The
-- instruments of the day are those of market.store.universe_members (the evaluation in
-- force when the day started and every one made during it), and any with a trade minute
-- recorded in the day. A day without any of them is empty, and closed.
CREATE FUNCTION market.seal_day(p_day date) RETURNS int4
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = market, pg_temp
    SET TimeZone = 'UTC'
    SET lock_timeout = '5s'
AS $$
DECLARE
    v_table text;
    v_name text;
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_day IS NULL OR clock_timestamp() < (p_day + 4)::timestamp AT TIME ZONE 'UTC' THEN
        RAISE EXCEPTION 'seal_day: % ended less than 72 h ago', p_day;
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'seal_day: runs only in READ COMMITTED, to see every write before its fences';
    END IF;
    PERFORM pg_advisory_xact_lock(9301);
    IF EXISTS (SELECT 1 FROM market.archive_seals s WHERE s.day = p_day) THEN
        RETURN 0;
    END IF;
    v_start := p_day::timestamp AT TIME ZONE 'UTC';
    v_end := (p_day + 1)::timestamp AT TIME ZONE 'UTC';
    FOREACH v_table IN ARRAY ARRAY[
        'data_quality_minute', 'candles', 'ws_candles', 'exhausted_index_rebuilds',
        'minute_status', 'depth_slot_status', 'depth_metrics', 'depth_snapshots',
        'depth_late_books', 'trading_seen_late', 'late_ws_candles', 'late_ws_repeats'
    ] LOOP
        v_name := v_table || '_p' || to_char(p_day, 'YYYYMMDD');
        IF to_regclass(format('market.%I', v_name)) IS NULL THEN
            RAISE EXCEPTION 'seal_day: partition % is missing', v_name;
        END IF;
        EXECUTE format(
            'ALTER TABLE market.%I ADD CONSTRAINT %I CHECK (false) NOT VALID',
            v_name, v_name || '_sellada'
        );
    END LOOP;
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT u.market, u.symbol
            FROM market.universe_snapshots u
            WHERE u.in_universe
              AND u.evaluated_at < v_end
              AND u.evaluated_at >= coalesce(
                  (SELECT max(e.evaluated_at) FROM market.universe_snapshots e
                   WHERE e.evaluated_at <= v_start),
                  v_start
              )
            UNION
            SELECT q.market, q.symbol
            FROM market.data_quality_minute q
            WHERE q.channel = 'trade' AND q.minute_start >= v_start AND q.minute_start < v_end
        ) AS i
        WHERE (
            SELECT count(*) FROM market.minute_status m
            WHERE m.market = i.market AND m.symbol = i.symbol
              AND m.minute_start >= v_start AND m.minute_start < v_end
        ) <> 1440
    ) THEN
        RAISE EXCEPTION 'seal_day: % is not closed for every instrument', p_day;
    END IF;
    INSERT INTO market.archive_seals (day) VALUES (p_day);
    RETURN 12;
END
$$;

-- Retire day D: more than 90 days old, with a generation eligible to be left out of the
-- backups, every partition still sealed and with exactly the rows of its file. The
-- partitions that reference go first, then the referenced ones; a reference from another
-- day makes the detach fail and the whole call change nothing. Nobody may execute it in E3.
CREATE FUNCTION market.retire_day(p_day date) RETURNS int4
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = market, pg_temp
    SET TimeZone = 'UTC'
    SET lock_timeout = '5s'
AS $$
DECLARE
    v_table text;
    v_name text;
    v_generation uuid;
    v_rows int8;
    v_expected int8;
BEGIN
    IF p_day IS NULL OR p_day >= current_date - 90 THEN
        RAISE EXCEPTION 'retire_day: % is not older than 90 days', p_day;
    END IF;
    PERFORM pg_advisory_xact_lock(9301);
    IF EXISTS (SELECT 1 FROM market.archive_retirements r WHERE r.day = p_day) THEN
        RETURN 0;
    END IF;
    SELECT g.generation_id INTO v_generation
    FROM market.archive_generations g
    JOIN market.archive_generation_states s
      ON s.generation_id = g.generation_id AND s.state = 'elegible_para_exclusion_backup'
    WHERE g.day = p_day
    ORDER BY s.recorded_at DESC
    LIMIT 1;
    IF v_generation IS NULL THEN
        RAISE EXCEPTION 'retire_day: % has no generation eligible to leave the backups', p_day;
    END IF;
    FOREACH v_table IN ARRAY ARRAY[
        'minute_status', 'candles', 'ws_candles', 'exhausted_index_rebuilds',
        'trading_seen_late', 'late_ws_candles', 'late_ws_repeats', 'depth_metrics',
        'depth_snapshots', 'depth_late_books', 'data_quality_minute', 'depth_slot_status'
    ] LOOP
        v_name := v_table || '_p' || to_char(p_day, 'YYYYMMDD');
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint c
            WHERE c.conrelid = to_regclass(format('market.%I', v_name))
              AND c.conname = v_name || '_sellada' AND c.contype = 'c' AND NOT c.convalidated
        ) THEN
            RAISE EXCEPTION 'retire_day: % is not sealed', v_name;
        END IF;
        SELECT f.row_count INTO v_expected
        FROM market.archive_files f
        WHERE f.generation_id = v_generation AND f.table_name = v_table;
        EXECUTE format('SELECT count(*) FROM market.%I', v_name) INTO v_rows;
        IF v_expected IS NULL OR v_rows <> v_expected THEN
            RAISE EXCEPTION 'retire_day: % holds % rows and its file %', v_name, v_rows, v_expected;
        END IF;
    END LOOP;
    FOREACH v_table IN ARRAY ARRAY[
        'minute_status', 'candles', 'ws_candles', 'exhausted_index_rebuilds',
        'trading_seen_late', 'late_ws_candles', 'late_ws_repeats', 'depth_metrics',
        'depth_snapshots', 'depth_late_books', 'data_quality_minute', 'depth_slot_status'
    ] LOOP
        v_name := v_table || '_p' || to_char(p_day, 'YYYYMMDD');
        EXECUTE format('ALTER TABLE market.%I DETACH PARTITION market.%I', v_table, v_name);
        EXECUTE format('DROP TABLE market.%I', v_name);
    END LOOP;
    INSERT INTO market.archive_retirements (day, generation_id) VALUES (p_day, v_generation);
    RETURN 12;
END
$$;

REVOKE ALL ON FUNCTION market.prepare_partitions() FROM PUBLIC;
REVOKE ALL ON FUNCTION market.seal_day(date) FROM PUBLIC;
REVOKE ALL ON FUNCTION market.retire_day(date) FROM PUBLIC;

-- kbot_ingest keeps exactly its 0002 privileges on the twelve tables (02 §9), and reads the
-- seals: it does not write late evidence of a sealed day (02 §3.9).
GRANT SELECT, INSERT ON TABLE
    market.data_quality_minute, market.candles, market.ws_candles,
    market.exhausted_index_rebuilds, market.minute_status, market.depth_slot_status,
    market.depth_metrics, market.depth_snapshots, market.depth_late_books,
    market.trading_seen_late, market.late_ws_candles, market.late_ws_repeats
    TO kbot_ingest;
GRANT SELECT ON TABLE market.archive_seals TO kbot_ingest;

-- kbot_archive (02 §3.9): reads what it archives, publishes its generations, and prepares
-- and seals partitions only through its two functions. Retiring stays with nobody.
GRANT USAGE ON SCHEMA market TO kbot_archive;
GRANT SELECT ON TABLE
    market.data_quality_minute, market.candles, market.ws_candles,
    market.exhausted_index_rebuilds, market.minute_status, market.depth_slot_status,
    market.depth_metrics, market.depth_snapshots, market.depth_late_books,
    market.trading_seen_late, market.late_ws_candles, market.late_ws_repeats,
    market.archive_seals, market.archive_generations, market.archive_files,
    market.archive_generation_states, market.archive_retirements
    TO kbot_archive;
GRANT INSERT ON TABLE
    market.archive_generations, market.archive_files, market.archive_generation_states
    TO kbot_archive;
GRANT EXECUTE ON FUNCTION market.prepare_partitions() TO kbot_archive;
GRANT EXECUTE ON FUNCTION market.seal_day(date) TO kbot_archive;
