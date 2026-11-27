#!/usr/bin/env bash
# kbot, stage E4c (02 §13.1, point 4): the copies. Run as root, after 04_verificar.sh, with
# the code at the approved tag (02_codigo.sh), and not between minutes 40 and 50, while the
# hourly copy runs:
#
#   sudo bash /opt/kbot/deploy/install/05_copias.sh
#
# It is idempotent. Stage E4c-1: the public keys of the two recipients (never a private
# key), the directories of the copies, the units of the hourly and the weekly copies, a
# first copy of each kind as a check, and their timers. Stage E4c-2:
#   - the user kbot_upload, which uploads the encrypted copies to Object Storage and has no
#     role in PostgreSQL;
#   - the group of the copies, so that kbot_upload reads them and nobody else, also those
#     made before (E4c-1 left them to kbot_backup alone);
#   - the directory of its state, which kbot_backup reads, and its request counter, empty;
#   - the metadata guard that lets kbot_upload, and no other service, reach the instance
#     metadata, checked from the context of each user;
#   - the unit of the copy taken before an update, which 02_codigo.sh starts from then on;
#   - a first upload as a check, which needs the bucket, the dynamic group and the policy of
#     docs/operacion/copias_object_storage.md.
# The copies can only be decrypted with the owner's private keys, never on the server.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
repo=/opt/kbot
backups=/srv/kbot/backups
uploads=/srv/kbot/subidas
recipients="$repo/deploy/etc/kbot/age-recipients.txt"
units=(
  kbot-backup.service kbot-backup.timer kbot-backup-globals.service kbot-backup-globals.timer
  kbot-backup-previa.service kbot-upload.service kbot-upload.timer
)

step() { printf '\n== %s\n' "$*"; }

step "the public keys of the two recipients"
if grep -qi 'AGE-SECRET-KEY' "$recipients"; then
  echo "$recipients holds a private key: stopping" >&2
  exit 1
fi
install -d -o root -g root -m 0755 /etc/kbot
install -o root -g root -m 0644 "$recipients" /etc/kbot/age-recipients.txt

step "the user of the uploads: no shell, no role in PostgreSQL"
if ! id kbot_upload >/dev/null 2>&1; then
  useradd --system --shell /usr/sbin/nologin --home-dir /nonexistent --no-create-home kbot_upload
fi

step "the directories of the copies"
if ! mountpoint -q /srv/kbot; then
  echo "/srv/kbot is not mounted: run 01_sistema.sh first" >&2
  exit 1
fi
install -d -o kbot_backup -g kbot_upload -m 2750 "$backups"
# The weekly global objects are written by postgres and read by kbot_upload (E4c-2).
install -d -o postgres -g kbot_upload -m 2750 "$backups/globales"
# Every copy, also those made before E4c-2: the group kbot_upload reads, nobody else does.
chgrp -R kbot_upload "$backups"
find "$backups" -type d -exec chmod g+rxs,g-w,o-rwx {} +
find "$backups" -type f -exec chmod g+r,g-wx,o-rwx {} +
# The state of the uploads: written by kbot_upload, read by kbot_backup.
install -d -o kbot_upload -g kbot_backup -m 2750 "$uploads"
if [[ ! -e "$uploads/peticiones.json" ]]; then
  printf '%s\n' '{"dias": {}, "formato": "kbot-peticiones-1"}' >"$uploads/peticiones.json"
  chown kbot_upload:kbot_backup "$uploads/peticiones.json"
  chmod 0640 "$uploads/peticiones.json"
fi
sync -f "$backups"

step "the metadata guard: kbot_upload, and no other service, reaches the instance metadata"
install -o root -g root -m 0644 "$repo/deploy/etc/kbot/metadata-guard.nft" \
  /etc/kbot/metadata-guard.nft
nft -c -f /etc/kbot/metadata-guard.nft
systemctl reload kbot-metadata-guard
# Each check exits 0 only when its user gets what it must: kbot_upload reaches the
# metadata, kbot_backup and the other services are rejected (E4-D04).
for user in kbot_upload kbot_backup kbot_ingest kbot_archive kbot_sync; do
  systemctl start "kbot-guardcheck@$user"
done

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

step "a first upload to Object Storage, as a check"
systemctl start kbot-upload.service
"$repo/.venv/bin/python" - "$uploads/estado.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
if not (state["listado"]["ok"] and state["ultima_subida_completa"]):
    sys.exit(f"the upload did not complete: {state['errores']}")
print("verified in the bucket:", ", ".join(sorted(state["verificadas"])))
print("bytes in the bucket:", state["ocupado"], "- requests in 32 days:", state["peticiones"])
PY

step "the timers"
systemctl enable --now kbot-backup.timer kbot-backup-globals.timer kbot-upload.timer
systemctl list-timers kbot-backup.timer kbot-backup-globals.timer kbot-upload.timer --no-pager

step "done: copies at minute 40, uploads at minute 55, global objects on Sundays at 03:40 UTC"
