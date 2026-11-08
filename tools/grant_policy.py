"""Grant policy of the migrations, derived from the role table of spec 02 §9.

Two layers:

* ``ALLOWED_GRANTS`` is the exact list of privileges the migrations may grant.
  ``tools/sql_guard.py`` rejects any other GRANT, and ``tests/db/test_privileges.py`` checks
  that the live database has exactly these privileges, no more and no fewer. A phase that
  needs a new grant adds it here in the same reviewed change as its migration.
* ``section_9_conflict()`` encodes the "No puede" column of §9, together with the related
  rules of §6.1 (only ``kbot_owner`` writes the limits) and §8 (no direct UPDATE or DELETE).
  ``check_policy()`` applies it to every entry of ``ALLOWED_GRANTS``, so the exact list
  itself cannot contradict the specification.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from kbot.db import roles

SCHEMA = "schema"
TABLE = "table"

SCHEMA_PRIVILEGES = frozenset({"usage"})
TABLE_PRIVILEGES = frozenset({"select", "insert"})


@dataclass(frozen=True, order=True)
class Grant:
    role: str
    privilege: str
    kind: str
    target: str

    def __str__(self) -> str:
        return f"{self.privilege.upper()} on {self.kind} {self.target} to {self.role}"


# Phase 2, stage E2 (02 §3.8): the market tables, where kbot_ingest reads and inserts.
MARKET_TABLES = (
    "market.instruments",
    "market.risk_tiers",
    "market.data_quality_minute",
    "market.candles",
    "market.ws_candles",
    "market.exhausted_index_rebuilds",
    "market.data_gaps",
    "market.minute_status",
    "market.depth_slot_status",
    "market.depth_metrics",
    "market.depth_snapshots",
    "market.funding_rates",
    "market.universe_snapshots",
)

ALLOWED_GRANTS: frozenset[Grant] = frozenset(
    {
        # Risk applies the DM limits and the watchdog checks them (02 §6.1).
        Grant("kbot_risk", "usage", SCHEMA, "config"),
        Grant("kbot_risk", "select", TABLE, "config.risk_limits"),
        Grant("kbot_watchdog", "usage", SCHEMA, "config"),
        Grant("kbot_watchdog", "select", TABLE, "config.risk_limits"),
        # svc-ingest records market data: SELECT and INSERT only, never UPDATE or DELETE.
        Grant("kbot_ingest", "usage", SCHEMA, "market"),
        *(Grant("kbot_ingest", "select", TABLE, table) for table in MARKET_TABLES),
        *(Grant("kbot_ingest", "insert", TABLE, table) for table in MARKET_TABLES),
    }
)

# §9 "No puede": schemas these roles must never touch.
FORBIDDEN_SCHEMAS = {
    "kbot_ingest": frozenset({"trading", "news", "config"}),
    "kbot_news": frozenset({"trading"}),
}
# §9: only kbot_risk approves (risk decisions, risk reservations and approved orders, 02 §1.3).
APPROVAL_TABLES = frozenset(
    {"trading.risk_decisions", "trading.risk_reservations", "trading.approved_orders"}
)
# §9: kbot_risk cannot execute (orders, fills, positions and the ledger, 02 §1.3).
EXECUTION_TABLES = frozenset(
    {"trading.paper_orders", "trading.paper_fills", "trading.paper_positions"}
)
# §9: kbot_telegram reads and requests a pause; it can do nothing else.
TELEGRAM_WRITABLE = frozenset({"ops.admin_requests"})


def section_9_conflict(grant: Grant) -> str | None:
    """Return why ``grant`` contradicts spec 02 §9, or None when it does not."""
    schema = grant.target.split(".", 1)[0]
    writes = grant.kind == TABLE and grant.privilege != "select"

    if grant.role not in roles.LOGIN_ROLES:
        return f"{grant.role} is not a login role and receives no grants"
    if grant.role == roles.BACKUP:
        return "kbot_backup reads through pg_read_all_data only and never writes"
    if schema in FORBIDDEN_SCHEMAS.get(grant.role, frozenset()):
        return f"§9: {grant.role} has nothing in {schema}"
    if writes and schema == "config":
        return "only kbot_owner writes config, through migrations (§6.1)"
    if writes and grant.target in APPROVAL_TABLES and grant.role != "kbot_risk":
        return f"§9: only kbot_risk approves; {grant.role} cannot write {grant.target}"
    if (
        writes
        and grant.role == "kbot_risk"
        and (grant.target in EXECUTION_TABLES or schema == "ledger")
    ):
        return f"§9: kbot_risk cannot execute; it cannot write {grant.target}"
    if writes and grant.role == "kbot_telegram" and grant.target not in TELEGRAM_WRITABLE:
        return f"§9: kbot_telegram may only request a pause, not write {grant.target}"
    return None


def check_policy(grants: Iterable[Grant] = ALLOWED_GRANTS) -> list[str]:
    """Problems in a grant list: malformed entries or entries that contradict §9."""
    problems: list[str] = []
    for grant in sorted(grants):
        schema = grant.target.split(".", 1)[0]
        if schema not in roles.SCHEMAS:
            problems.append(f"{grant}: not a project schema")
        if grant.kind == SCHEMA and (
            grant.privilege not in SCHEMA_PRIVILEGES or "." in grant.target
        ):
            problems.append(f"{grant}: malformed schema grant")
        elif grant.kind == TABLE and (
            grant.privilege not in TABLE_PRIVILEGES or grant.target.count(".") != 1
        ):
            problems.append(f"{grant}: malformed table grant")
        elif grant.kind not in {SCHEMA, TABLE}:
            problems.append(f"{grant}: unknown object kind")
        conflict = section_9_conflict(grant)
        if conflict:
            problems.append(f"{grant}: {conflict}")
    return problems
