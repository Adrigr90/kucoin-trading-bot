"""Alembic environment.

The administrator connects as ``postgres`` over the Unix socket with peer authentication
and every migration runs after ``SET ROLE kbot_owner``, so all objects belong to
``kbot_owner``, which itself cannot log in (spec 02 §9, role attributes).
"""

import os

from alembic import context
from sqlalchemy import create_engine, pool

DEFAULT_ADMIN_URL = "postgresql+psycopg://postgres@/kbot?host=/var/run/postgresql"


def run_migrations_online() -> None:
    url = os.environ.get("KBOT_DB_ADMIN_URL", DEFAULT_ADMIN_URL)
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("SET ROLE kbot_owner")
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported; migrations run against the database")

run_migrations_online()
