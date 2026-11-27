#!/usr/bin/env bash
# kbot, stage E4c-3 (02 §13.1, point 4): the owner's external disk. Run as root, after
# 05_copias.sh, with the code at the approved tag (02_codigo.sh) and the public key of
# kbot_sync that the owner generated on the PC in deploy/etc/kbot/kbot_sync.pub
# (docs/operacion/disco_externo.md):
#
#   sudo bash /opt/kbot/deploy/install/06_externo.sh
#
# It is idempotent. It installs the public key (never a private one), lets kbot_sync read the
# copies and the archive, prepares the directories of the verifier and the 256 MiB image of
# recibos/, mounts the read-only views of the SFTP chroot, enables the SFTP block of sshd
# only once sshd accepts the whole configuration (otherwise it puts the previous one back and
# does not reload), and starts the verification every 15 minutes. It never touches the
# administrator's access.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
repo=/opt/kbot
data=/srv/kbot
externo="$data/externo"
chroot="$data/sftp"
key="$repo/deploy/etc/kbot/kbot_sync.pub"
dropin=/etc/ssh/sshd_config.d/10-kbot.conf
mounts=(srv-kbot-sftp-copias.mount srv-kbot-sftp-archivo.mount srv-kbot-sftp-desafios.mount
  srv-kbot-sftp-recibos.mount)
units=("${mounts[@]}" kbot-archive-externo.service kbot-archive-externo.timer
  kbot-archive-daily.service kbot-archive-prepare.service)

step() { printf '\n== %s\n' "$*"; }
as() {
  local user="$1"
  shift
  setpriv --reuid="$user" --regid="$user" --init-groups -- "$@"
}
must_fail() {
  if "$@" 2>/dev/null; then
    echo "allowed, and it must not be: $*" >&2
    exit 1
  fi
}

step "the public key of kbot_sync (never a private one)"
if [[ ! -s "$key" ]]; then
  echo "$key is missing: the owner generates the key first (docs/operacion/disco_externo.md)" >&2
  exit 1
fi
if grep -q 'PRIVATE KEY' "$key" || [[ "$(grep -c . "$key")" -ne 1 ]]; then
  echo "$key must hold exactly one public key, and nothing else: stopping" >&2
  exit 1
fi
ssh-keygen -l -f "$key"
install -d -o root -g root -m 0755 /etc/kbot
install -o root -g root -m 0644 "$key" /etc/kbot/kbot_sync_authorized_keys

step "kbot_sync reads the copies and the archive, and writes nothing in them"
id kbot_sync >/dev/null
usermod -a -G kbot_upload kbot_sync
chgrp -R kbot_sync "$data/archive"
find "$data/archive" -type d -exec chmod g+rxs,g-w,o-rwx {} +
find "$data/archive" -type f -exec chmod g+r,g-wx,o-rwx {} +

step "the directories of the verifier and the 256 MiB of recibos/"
install -d -o kbot_archive -g kbot_archive -m 0700 "$externo"
install -d -o kbot_archive -g kbot_sync -m 2750 "$externo/publico"
install -d -o kbot_archive -g kbot_archive -m 0700 "$externo/privado"
if [[ ! -e "$externo/recibos.img" ]]; then
  truncate -s 256M "$externo/recibos.img"
  mkfs.ext4 -q -F -L kbot-recibos "$externo/recibos.img"
fi
chown root:root "$externo/recibos.img"
chmod 0600 "$externo/recibos.img"

step "the chroot of kbot_sync: root's, and nobody else writes in it"
install -d -o root -g root -m 0755 "$chroot"
for view in copias archivo desafios recibos; do
  if ! mountpoint -q "$chroot/$view"; then
    install -d -o root -g root -m 0755 "$chroot/$view"
  fi
done

step "the units"
for unit in "${units[@]}"; do
  install -o root -g root -m 0644 "$repo/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemd-analyze verify --man=no --generators=no "${units[@]/#//etc/systemd/system/}"
systemctl enable --now "${mounts[@]}"
chown kbot_sync:kbot_archive "$chroot/recibos"
chmod 2750 "$chroot/recibos"
for view in copias archivo desafios; do
  findmnt -n -o OPTIONS "$chroot/$view" | grep -qw ro
done
sync -f "$data"

step "what kbot_sync can and cannot do, from its own identity"
as kbot_sync ls "$chroot/copias" "$chroot/archivo" "$chroot/desafios" >/dev/null
as kbot_sync touch "$chroot/recibos/.prueba"
as kbot_sync rm "$chroot/recibos/.prueba"
must_fail as kbot_sync touch "$chroot/copias/x"
must_fail as kbot_sync touch "$chroot/archivo/x"
must_fail as kbot_sync touch "$chroot/desafios/x"
must_fail as kbot_sync ls "$externo/privado"
must_fail as kbot_sync ls /srv/kbot/subidas

step "sshd: the SFTP block, only if sshd accepts the whole configuration"
backup="$(mktemp)"
cp -p "$dropin" "$backup"
install -o root -g root -m 0644 "$repo/deploy/etc/ssh/sshd_config.d/10-kbot.conf" "$dropin"
if ! sshd -t; then
  cp -p "$backup" "$dropin"
  rm -f "$backup"
  echo "sshd refused the new configuration: the previous one is back, nothing reloaded" >&2
  exit 1
fi
rm -f "$backup"
effective="$(sshd -T -C user=kbot_sync,host=localhost,addr=127.0.0.1)"
grep -qx 'forcecommand internal-sftp -u 0027 -P symlink,hardlink' <<<"$effective"
grep -qx 'chrootdirectory /srv/kbot/sftp' <<<"$effective"
grep -qx 'permittty no' <<<"$effective"
administrator="$(sshd -T -C user=ubuntu,host=localhost,addr=127.0.0.1)"
if grep -q '^forcecommand internal-sftp' <<<"$administrator" || grep -q '^chrootdirectory /' <<<"$administrator"; then
  echo "the SFTP block reaches the administrator: stopping" >&2
  exit 1
fi
systemctl reload ssh

step "a first verification, and the timer"
systemctl start kbot-archive-externo.service
test -s "$externo/publico/generaciones.json"
systemctl enable --now kbot-archive-externo.timer
systemctl list-timers kbot-archive-externo.timer --no-pager

step "done: the owner can now run tools\\pc\\sincronizar.ps1 with the disk connected"
