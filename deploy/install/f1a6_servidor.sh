#!/usr/bin/env bash
# kbot, stage E4b (02 §9 and §13.1; point 31 of 04): F1-A6 repeated on the server with the
# real system users, without the map of the CI. Run as root:
#
#   sudo bash /opt/kbot/deploy/install/f1a6_servidor.sh
#
# For each service user: it connects as its own role and as no other, and its session has
# the statement_timeout of 02 §9; a user without a role is refused; the owners cannot log
# in; and no service can create anything in any schema. A refusal only counts when
# PostgreSQL answers with the expected SQLSTATE: 42501 (insufficient privilege) for a
# CREATE, never a failed connection or another error (review of E4b, E4b-01). Exit 1 if
# anything fails.
set -uo pipefail

host=/var/run/postgresql
db=kbot
failures=0

expect() { # description expected actual
  if [[ "$2" == "$3" ]]; then
    echo "OK   $1"
  else
    echo "FAIL $1 (expected '$2', got '$3')"
    failures=$((failures + 1))
  fi
}

# query <system user> <role> <sql>: prints the first output line, or SQLSTATE=<code> for an
# error of SQL, or CONNECT=<psql exit code> when the connection itself fails.
query() {
  local out err code
  err="$(mktemp)"
  out="$(sudo -n -u "$1" psql -X -w -q -At -h "$host" -U "$2" -d "$db" \
    -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -c "$3" 2>"$err")"
  code=$?
  if [[ $code -eq 0 ]]; then
    echo "${out%%$'\n'*}"
  elif grep -q '^ERROR: ' "$err"; then
    echo "SQLSTATE=$(sed -n 's/^ERROR: *\([0-9A-Z]\{5\}\).*/\1/p' "$err" | head -n 1)"
  else
    echo "CONNECT=$code"
  fi
  rm -f "$err"
}

roles=(kbot_ingest kbot_archive kbot_backup)
declare -A timeout=([kbot_ingest]=30s [kbot_archive]=1h [kbot_backup]=0)

for user in "${roles[@]}"; do
  expect "$user connects as its own role" "$user" "$(query "$user" "$user" "SELECT current_user")"
  expect "$user has its statement_timeout" "${timeout[$user]}" \
    "$(query "$user" "$user" "SHOW statement_timeout")"
  for other in "${roles[@]}" kbot_owner kbot_fn_owner postgres; do
    [[ "$other" == "$user" ]] && continue
    expect "$user cannot connect as $other" "CONNECT=2" "$(query "$user" "$other" "SELECT 1")"
  done
done
for user in kbot_sync kbot_restore nobody; do
  expect "$user (no role) is refused" "CONNECT=2" "$(query "$user" "$user" "SELECT 1")"
  expect "$user cannot connect as kbot_ingest" "CONNECT=2" "$(query "$user" kbot_ingest "SELECT 1")"
done
for owner in kbot_owner kbot_fn_owner; do
  expect "$owner cannot log in from the administrator" "CONNECT=2" \
    "$(query postgres "$owner" "SELECT 1")"
done

# No service creates anything. Each attempt runs inside a transaction that is rolled back,
# so nothing stays even if a CREATE wrongly succeeds; only 42501 counts as the refusal.
for user in "${roles[@]}"; do
  for schema in market trading ledger news ai ops config public; do
    expect "$user cannot create in $schema" "SQLSTATE=42501" \
      "$(query "$user" "$user" "BEGIN; CREATE TABLE $schema.f1a6_should_fail (x int); ROLLBACK;")"
  done
  expect "$user cannot create a temporary table" "SQLSTATE=42501" \
    "$(query "$user" "$user" "BEGIN; CREATE TEMP TABLE f1a6_tmp (x int); ROLLBACK;")"
  expect "$user cannot become kbot_owner" "SQLSTATE=42501" \
    "$(query "$user" "$user" "SET ROLE kbot_owner")"
done

if [[ $failures -gt 0 ]]; then
  echo "$failures failures" >&2
  exit 1
fi
echo "F1-A6 on the server: all passed"
