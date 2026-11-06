"""Names of the database, schemas and roles (spec 02 §1.1, §1.3 and §9)."""

from __future__ import annotations

DATABASE = "kbot"
SOCKET_DIR = "/var/run/postgresql"

SCHEMAS = frozenset({"market", "trading", "ledger", "news", "ai", "ops", "config"})

OWNER = "kbot_owner"
FUNCTION_OWNER = "kbot_fn_owner"
BACKUP = "kbot_backup"

SERVICE_ROLES: tuple[str, ...] = (
    "kbot_ingest",
    "kbot_news",
    "kbot_strategy",
    "kbot_risk",
    "kbot_broker",
    "kbot_watchdog",
    "kbot_web",
    "kbot_telegram",
)

OWNER_ROLES: tuple[str, ...] = (OWNER, FUNCTION_OWNER)
LOGIN_ROLES: tuple[str, ...] = (*SERVICE_ROLES, BACKUP)
PROJECT_ROLES: tuple[str, ...] = (*OWNER_ROLES, *LOGIN_ROLES)
