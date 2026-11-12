#!/usr/bin/env bash
# kbot, stage E4b (02 §9 and §13.1, point 3): PostgreSQL 18 of production. Run as root,
# after 01_sistema.sh and 03_codigo.sh (the migrations need the environment of the code):
#
#   sudo bash /opt/kbot/deploy/install/02_postgresql.sh
#
# Installs PostgreSQL 18 from PGDG (arm64), creates the cluster 18/main with its data on
# the data volume, applies the production configuration (no TCP, peer only), the roles of
# sql/roles.sql, the migrations 0001 → 0003 as kbot_owner, and checks the result with
# `kbot.ops.pgcheck`. Idempotent: an existing cluster, role or migration is kept.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
repo=/opt/kbot
data=/srv/kbot
version=18
cluster=main
pgdata="$data/postgresql/$version/$cluster"
conf="/etc/postgresql/$version/$cluster"

step() { printf '\n== %s\n' "$*"; }

if ! mountpoint -q "$data"; then
  echo "$data is not a mount point: run 01_sistema.sh first" >&2
  exit 1
fi
if [[ ! -x "$repo/.venv/bin/alembic" ]]; then
  echo "$repo/.venv is missing: run 03_codigo.sh first" >&2
  exit 1
fi

step "PostgreSQL $version from PGDG, without a default cluster"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq postgresql-common
if ! grep -q '^create_main_cluster = false' /etc/postgresql-common/createcluster.conf; then
  echo "create_main_cluster = false" >>/etc/postgresql-common/createcluster.conf
fi
if [[ ! -f /etc/apt/sources.list.d/pgdg.sources && ! -f /etc/apt/sources.list.d/pgdg.list ]]; then
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
fi
apt-get install -y -qq "postgresql-$version" "postgresql-client-$version"

step "cluster $version/$cluster with its data on $pgdata"
if ! pg_lsclusters --no-header | awk '{print $1"/"$2}' | grep -qx "$version/$cluster"; then
  install -d -o postgres -g postgres -m 0700 "$data/postgresql"
  pg_createcluster "$version" "$cluster" --datadir "$pgdata" --locale C.UTF-8 --encoding UTF8
fi
actual="$(pg_lsclusters --no-header | awk -v c="$version/$cluster" '$1"/"$2==c {print $6}')"
if [[ "$actual" != "$pgdata" ]]; then
  echo "the cluster $version/$cluster keeps its data in $actual, not in $pgdata" >&2
  exit 1
fi

step "production configuration: no TCP, peer only, the limits of 02"
install -o postgres -g postgres -m 0640 "$repo/deploy/etc/postgresql/pg_hba.conf" "$conf/pg_hba.conf"
install -o postgres -g postgres -m 0644 "$repo/deploy/etc/postgresql/kbot.conf" "$conf/conf.d/kbot.conf"
rm -f "$conf/pg_ident.conf.kbot" 2>/dev/null || true
systemctl enable "postgresql@$version-$cluster" >/dev/null
pg_ctlcluster "$version" "$cluster" restart
pg_lsclusters
psql_admin() { sudo -u postgres psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -d postgres "$@"; }
psql_admin -Atc "SELECT version()"
[[ "$(psql_admin -Atc "SHOW listen_addresses")" == "" ]]

step "roles and database of sql/roles.sql (once)"
if [[ "$(psql_admin -Atc "SELECT count(*) FROM pg_roles WHERE rolname = 'kbot_owner'")" == "0" ]]; then
  psql_admin -f "$repo/sql/roles.sql"
else
  echo "roles already present; sql/roles.sql not reapplied"
fi

step "migrations as kbot_owner (Alembic)"
sudo -u postgres env KBOT_DB_ADMIN_URL="postgresql+psycopg://postgres@/kbot?host=/var/run/postgresql" \
  "$repo/.venv/bin/alembic" -c "$repo/alembic.ini" upgrade head
sudo -u postgres env KBOT_DB_ADMIN_URL="postgresql+psycopg://postgres@/kbot?host=/var/run/postgresql" \
  "$repo/.venv/bin/alembic" -c "$repo/alembic.ini" current

step "checks of 02 §9 on the live cluster (kbot.ops.pgcheck, as postgres)"
sudo -u postgres "$repo/.venv/bin/python" -m kbot.ops.pgcheck --catalog "$repo/sql/security_definer_catalog.json"

step "F1-A6 with the real system users (point 31)"
bash "$repo/deploy/install/f1a6_servidor.sh"

step "done: PostgreSQL is ready; the owner decides when to enable svc-ingest and the archive timer"
