-- Cluster-wide objects only (spec 02 §9, roles and credentials).
-- Applied once by the PostgreSQL administrator as postgres, over the Unix socket with peer.
-- No role has a password: with PASSWORD NULL, password authentication always fails.
-- Object privileges and functions belong in the migrations, not here.

-- Owners: they never log in.
CREATE ROLE kbot_owner NOLOGIN PASSWORD NULL;
CREATE ROLE kbot_fn_owner NOLOGIN PASSWORD NULL;

-- Service roles: one per process (02 §1.1), peer login from their own system user.
CREATE ROLE kbot_ingest LOGIN PASSWORD NULL CONNECTION LIMIT 5;
CREATE ROLE kbot_news LOGIN PASSWORD NULL CONNECTION LIMIT 5;
CREATE ROLE kbot_strategy LOGIN PASSWORD NULL CONNECTION LIMIT 5;
CREATE ROLE kbot_risk LOGIN PASSWORD NULL CONNECTION LIMIT 5;
CREATE ROLE kbot_broker LOGIN PASSWORD NULL CONNECTION LIMIT 5;
CREATE ROLE kbot_watchdog LOGIN PASSWORD NULL CONNECTION LIMIT 5;
CREATE ROLE kbot_web LOGIN PASSWORD NULL CONNECTION LIMIT 5;
CREATE ROLE kbot_telegram LOGIN PASSWORD NULL CONNECTION LIMIT 5;

-- Backups: read-only.
CREATE ROLE kbot_backup LOGIN PASSWORD NULL CONNECTION LIMIT 2;
GRANT pg_read_all_data TO kbot_backup;

-- Provisional values, to be adjusted with the consumption measured in phase 2.
ALTER ROLE kbot_ingest SET statement_timeout = '30s';
ALTER ROLE kbot_news SET statement_timeout = '30s';
ALTER ROLE kbot_strategy SET statement_timeout = '30s';
ALTER ROLE kbot_risk SET statement_timeout = '30s';
ALTER ROLE kbot_broker SET statement_timeout = '30s';
ALTER ROLE kbot_watchdog SET statement_timeout = '30s';
ALTER ROLE kbot_web SET statement_timeout = '30s';
ALTER ROLE kbot_telegram SET statement_timeout = '30s';
ALTER ROLE kbot_backup SET statement_timeout = 0;

-- The project database. PUBLIC loses its default CONNECT and TEMPORARY rights, so no
-- service can create objects even in a temporary schema.
CREATE DATABASE kbot OWNER kbot_owner;
REVOKE ALL ON DATABASE kbot FROM PUBLIC;
GRANT CONNECT ON DATABASE kbot TO
    kbot_ingest, kbot_news, kbot_strategy, kbot_risk, kbot_broker,
    kbot_watchdog, kbot_web, kbot_telegram, kbot_backup;
