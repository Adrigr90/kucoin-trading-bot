#!/usr/bin/env bash
# kbot, stage E4c-1 (02 §13.1, point 4): the local backups. Run as root, after
# 04_verificar.sh, with the code at the approved tag (02_codigo.sh):
#
#   sudo bash /opt/kbot/deploy/install/05_copias.sh
#
# It is idempotent. It installs the public keys of the two recipients (never a private
# key), the directory of the weekly global objects and the four units, makes a first copy
# of each kind as a check and enables the two timers. The copies can only be decrypted
# with the owner's private keys, which are never on the server.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
repo=/opt/kbot
backups=/srv/kbot/backups
recipients="$repo/deploy/etc/kbot/age-recipients.txt"
units=(kbot-backup.service kbot-backup.timer kbot-backup-globals.service kbot-backup-globals.timer)

step() { printf '\n== %s\n' "$*"; }

step "the public keys of the two recipients"
if grep -qi 'AGE-SECRET-KEY' "$recipients"; then
  echo "$recipients holds a private key: stopping" >&2
  exit 1
fi
install -d -o root -g root -m 0755 /etc/kbot
install -o root -g root -m 0644 "$recipients" /etc/kbot/age-recipients.txt

step "the directories of the copies"
if ! mountpoint -q /srv/kbot; then
  echo "/srv/kbot is not mounted: run 01_sistema.sh first" >&2
  exit 1
fi
install -d -o kbot_backup -g kbot_backup -m 0750 "$backups"
# The weekly global objects are written by postgres and read by kbot_backup (E4c-2).
install -d -o postgres -g kbot_backup -m 2750 "$backups/globales"
sync -f "$backups"

step "the units of the copies"
for unit in "${units[@]}"; do
  install -o root -g root -m 0644 "$repo/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemd-analyze verify --man=no --generators=no "${units[@]/#//etc/systemd/system/}"

step "a first copy of each kind, as a check"
systemctl start kbot-backup.service
systemctl start kbot-backup-globals.service
newest="$(find "$backups" -maxdepth 1 -type d -name 'caliente-*' | sort | tail -n 1)"
if [[ -z "$newest" || ! -s "$newest/volcado.age" || ! -s "$newest/manifiesto.json" ]]; then
  echo "the hot copy was not published" >&2
  exit 1
fi
if [[ -n "$(find "$backups" \( -name 'volcado.dump' -o -name 'volcado.sql' \) -print -quit)" ]]; then
  echo "a plain dump is on the disk" >&2
  exit 1
fi
echo "hot copy: $(basename "$newest"), $(stat -c %s "$newest/volcado.age") bytes encrypted"

step "the timers"
systemctl enable --now kbot-backup.timer kbot-backup-globals.timer
systemctl list-timers kbot-backup.timer kbot-backup-globals.timer --no-pager

step "done: hourly copies at minute 40 and the global objects on Sundays at 03:40 UTC"
