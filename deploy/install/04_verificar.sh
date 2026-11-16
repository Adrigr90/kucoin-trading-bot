#!/usr/bin/env bash
# kbot, stages E4a and E4b (02 §9 and §13.1): the verification of the whole installation,
# once the system (01), the code (02) and PostgreSQL (03) are in place. Run as root:
#
#   sudo bash /opt/kbot/deploy/install/04_verificar.sh
#
# Every check runs even if an earlier one fails, and the script exits 1 if any failed, so
# one run shows everything that is wrong. Nothing here changes the server.
set -uo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
repo=/opt/kbot
python="$repo/.venv/bin/python"
failures=0

check() { # description command...
  local name="$1"
  shift
  printf '\n== %s\n' "$name"
  if "$@"; then
    echo "-- passed: $name"
  else
    echo "-- FAILED: $name" >&2
    failures=$((failures + 1))
  fi
}

units=()
for unit in "$repo"/deploy/systemd/*; do
  units+=("/etc/systemd/system/$(basename "$unit")")
done

check "systemd units (interpreter and PostgreSQL in place)" \
  systemd-analyze verify --man=no --generators=no "${units[@]}"
check "hardening of the server (F2-A10)" "$python" -m kbot.ops
check "PostgreSQL against 02 §9 (catalog, roles, settings, privileges, migrations)" \
  sudo -u postgres "$python" -m kbot.ops.pgcheck --repo "$repo"
check "F1-A6 with the real system users (point 31)" bash "$repo/deploy/install/f1a6_servidor.sh"

if [[ $failures -gt 0 ]]; then
  echo "$failures checks failed" >&2
  exit 1
fi
printf '\nAll checks passed.\n'
