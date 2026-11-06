#!/usr/bin/env bash
# CI only: PostgreSQL 18 from the official PGDG repository, installed on the runner itself.
# Peer authentication only works over a local Unix socket, so a service container reached
# over TCP cannot test it (spec 02 §9, CI layer 3).
set -euo pipefail

sudo apt-get update -qq
sudo apt-get install -y -qq postgresql-common
echo "create_main_cluster = false" | sudo tee -a /etc/postgresql-common/createcluster.conf >/dev/null
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt-get install -y -qq postgresql-18 postgresql-client-18

# Remove any cluster that came with the runner image so the socket on port 5432 is ours.
pg_lsclusters --no-header | while read -r version name _rest; do
  sudo pg_dropcluster --stop "$version" "$name"
done

sudo pg_createcluster 18 kbot --port 5432
conf="/etc/postgresql/18/kbot"
sudo install -o postgres -g postgres -m 0640 ci/postgres/pg_hba.conf "$conf/pg_hba.conf"
sudo install -o postgres -g postgres -m 0640 ci/postgres/pg_ident.conf "$conf/pg_ident.conf"
sudo install -o postgres -g postgres -m 0644 ci/postgres/kbot.conf "$conf/conf.d/kbot.conf"
sudo pg_ctlcluster 18 kbot start

pg_lsclusters
psql -X -h /var/run/postgresql -U postgres -d postgres -Atc "SELECT version()"
