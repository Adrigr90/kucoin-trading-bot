"""The network isolation, checked from inside a real user context (02 §13.1, E4-D04).

Two cases, run as the service user by the templates of deploy/systemd (review of E4a,
E4a-04: each case runs where nothing else could produce its result):

- ``b19`` inside ``kbot-netcheck@<user>``, which sets ``IPAddressDeny=any``: the Unix
  socket of PostgreSQL still accepts the users that have a role (peer), and KuCoin is
  unreachable (B19, point 35);
- ``guard`` inside ``kbot-guardcheck@<user>``, which sets no address filter at all: the
  instance metadata is rejected by the kernel rule of ``/etc/kbot/metadata-guard.nft``
  for every user but root and, from E4c, ``kbot_backup``, for which it is reachable.

A user without a SQL role (``kbot_restore``, ``kbot_sync``) must be refused by peer.
Exit 0 when every expectation of the case holds, 1 otherwise, one line per expectation.
"""

from __future__ import annotations

import argparse
import getpass
import socket
import sys

from kbot.db.roles import DATABASE, LOGIN_ROLES, SOCKET_DIR

METADATA = ("169.254.169.254", 80)
KUCOIN = ("api.kucoin.com", 443)
METADATA_ALLOWED = frozenset({"root", "kbot_backup"})


def can_connect(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def postgres_socket_accepts(user: str) -> bool:
    """A connection over the Unix socket as the user's own role (peer)."""
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(
            f"host={SOCKET_DIR} dbname={DATABASE} user={user}", connect_timeout=5
        ) as conn:
            return conn.execute("SELECT 1").fetchone() == (1,)
    except psycopg.Error:
        return False


def expectations(case: str, user: str) -> dict[str, bool]:
    """What must hold for this case and user, as the kernel answers right now."""
    if case == "b19":
        has_role = user in LOGIN_ROLES
        return {
            "postgresql socket " + ("accepts" if has_role else "refuses"): (
                postgres_socket_accepts(user) == has_role
            ),
            "kucoin unreachable": not can_connect(*KUCOIN),
        }
    allowed = user in METADATA_ALLOWED
    return {
        "metadata " + ("reachable" if allowed else "rejected"): can_connect(*METADATA) == allowed
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="network isolation check from a user context")
    parser.add_argument("case", choices=("b19", "guard"))
    arguments = parser.parse_args(argv)
    user = getpass.getuser()
    results = expectations(arguments.case, user)
    for name, ok in results.items():
        print(f"{'OK  ' if ok else 'FAIL'} {name} (case {arguments.case}, as {user})")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
