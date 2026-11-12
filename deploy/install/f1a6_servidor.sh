#!/usr/bin/env bash
# kbot, stage E4b (02 §9 and §13.1; point 31 of 04): F1-A6 repeated on the server with the
# real system users, without the map of the CI. Run as root:
#
#   sudo bash /opt/kbot/deploy/install/f1a6_servidor.sh
#
# For each service user: it connects as its own role, and as no other; a user without a
# role (kbot_sync, kbot_restore, nobody) is refused; the owners cannot log in; and no
# service can create anything in any schema. Exit 1 at the first failure.
set -uo pipefail

host=/var/run/postgresql
db=kbot
failures=0

try() { # user role -> prints the exit code of a trivial query
  sudo -n -u "$1" psql -X -w -h "$host" -U "$2" -d "$db" -Atc "SELECT current_user" >/dev/null 2>&1
  echo $?
}
expect() { # description expected actual
  if [[ "$2" == "$3" ]]; then
    echo "OK   $1"
  else
    echo "FAIL $1 (expected $2, got $3)"
    failures=$((failures + 1))
  fi
}

roles=(kbot_ingest kbot_archive kbot_backup)
for user in "${roles[@]}"; do
  expect "$user connects as its own role" 0 "$(try "$user" "$user")"
  for other in "${roles[@]}" kbot_owner kbot_fn_owner postgres; do
    [[ "$other" == "$user" ]] && continue
    expect "$user cannot connect as $other" 2 "$(try "$user" "$other")"
  done
done
for user in kbot_sync kbot_restore nobody; do
  expect "$user (no role) is refused" 2 "$(try "$user" "$user")"
  expect "$user cannot connect as kbot_ingest" 2 "$(try "$user" kbot_ingest)"
done
for owner in kbot_owner kbot_fn_owner; do
  expect "$owner cannot log in from the administrator" 2 "$(try postgres "$owner")"
done

# No service creates anything: a table in each schema and a temporary table.
for user in "${roles[@]}"; do
  for schema in market trading ledger news ai ops config public; do
    code="$(sudo -n -u "$user" psql -X -w -h "$host" -U "$user" -d "$db" -Atc \
      "CREATE TABLE $schema.f1a6_should_fail (x int)" >/dev/null 2>&1; echo $?)"
    expect "$user cannot create in $schema" 3 "$code"
  done
  code="$(sudo -n -u "$user" psql -X -w -h "$host" -U "$user" -d "$db" -Atc \
    "CREATE TEMP TABLE f1a6_tmp (x int)" >/dev/null 2>&1; echo $?)"
  expect "$user cannot create a temporary table" 3 "$code"
done

if [[ $failures -gt 0 ]]; then
  echo "$failures failures" >&2
  exit 1
fi
echo "F1-A6 on the server: all passed"
