"""The network isolation, checked from inside a real service context (02 §13.1, E4-D04).

``python -m kbot.ops.netcheck`` runs as the service user, inside ``kbot-netcheck@<user>``,
which applies the same systemd isolation as the service. Three cases, each decided by
what the kernel answers:

- the Unix socket of PostgreSQL must accept a connection (peer: the user's own role);
- KuCoin must be unreachable while ``IPAddressDeny=any`` is in force (B19, point 35);
- the instance metadata must be rejected (the guard of E4-D04), except for root and,
  from E4c, ``kbot_backup``.

Exit 0 when every case holds, 1 otherwise, with one line per case.
"""

from __future__ import annotations

import getpass
import socket
import sys

from kbot.db.roles import DATABASE, SOCKET_DIR

METADATA = ("169.254.169.254", 80)
KUCOIN = ("api.kucoin.com", 443)
METADATA_ALLOWED = {"root", "kbot_backup"}


def can_connect(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def postgres_socket_works(user: str) -> bool:
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


def main() -> int:
    user = getpass.getuser()
    cases = {
        "postgresql socket": postgres_socket_works(user),
        "kucoin unreachable": not can_connect(*KUCOIN),
        "metadata rejected": can_connect(*METADATA) == (user in METADATA_ALLOWED),
    }
    for name, ok in cases.items():
        print(f"{'OK  ' if ok else 'FAIL'} {name} (as {user})")
    return 0 if all(cases.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
