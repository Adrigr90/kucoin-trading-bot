#!/usr/bin/env bash
# kbot, stage E4a (02 §9 and §13.1): harden a fresh Ubuntu Server 24.04 ARM64 instance of
# Oracle Cloud and prepare it for the services. Run once as root on the server:
#
#   sudo bash /opt/kbot/deploy/install/01_sistema.sh <owner IPv4>/32 [data volume device]
#
# It is idempotent: running it again only reapplies the same state. It never touches
# PostgreSQL (02_postgresql.sh, stage E4b) nor the code (03_codigo.sh). Every step is
# listed in docs/operacion/comandos.md.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
owner_ip="${1:?owner IPv4 address with its prefix, for example 83.0.0.1/32}"
data_device="${2:-/dev/oracleoci/oraclevdb}"
repo=/opt/kbot
data=/srv/kbot

step() { printf '\n== %s\n' "$*"; }

step "packages: security updates, firewall, fail2ban, nftables, age"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ufw fail2ban nftables unattended-upgrades age chrony

step "system users: one per service, named after its SQL role, without a shell"
getent group kbot >/dev/null || groupadd --system kbot
for user in kbot_ingest kbot_archive kbot_backup kbot_restore; do
  if ! id "$user" >/dev/null 2>&1; then
    useradd --system --shell /usr/sbin/nologin --home-dir /nonexistent --no-create-home "$user"
  fi
done
# The recorder shares the single REST budget of the machine with other REST users (E1).
usermod -a -G kbot kbot_ingest
# The SFTP user of the owner's PC (02 §13.1, point 4): no shell, no home, SFTP only (E4c).
if ! id kbot_sync >/dev/null 2>&1; then
  useradd --system --shell /usr/sbin/nologin --home-dir /nonexistent --no-create-home kbot_sync
fi

step "data volume on $data (ext4, noatime)"
if [[ -b "$data_device" ]]; then
  if ! blkid -o value -s TYPE "$data_device" | grep -qx ext4; then
    mkfs.ext4 -q -L kbot-data "$data_device"
  fi
  mkdir -p "$data"
  if ! grep -q " $data " /etc/fstab; then
    echo "LABEL=kbot-data $data ext4 defaults,noatime,nofail 0 2" >>/etc/fstab
  fi
  mountpoint -q "$data" || mount "$data"
else
  echo "no data volume at $data_device: $data stays on the boot volume (tests only)" >&2
  mkdir -p "$data"
fi
# The archive root exists beforehand and durably (E3b-02): the archiver never creates it.
install -d -o kbot_archive -g kbot_archive -m 0750 "$data/archive"
install -d -o kbot_backup -g kbot_backup -m 0750 "$data/backups"
install -d -o root -g root -m 0755 "$data/sftp"
sync "$data"

step "sshd: keys only, no root, only the administrator"
install -o root -g root -m 0644 "$repo/deploy/etc/ssh/sshd_config.d/10-kbot.conf" \
  /etc/ssh/sshd_config.d/10-kbot.conf
sshd -t
systemctl reload ssh

step "ufw: deny everything incoming except SSH from the owner's address"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow from "$owner_ip" to any port 22 proto tcp comment 'SSH from the owner'
ufw logging low
ufw --force enable

step "metadata guard (E4-D04): only root may reach the instance metadata"
install -o root -g root -m 0644 "$repo/deploy/etc/kbot/metadata-guard.nft" /etc/kbot-metadata-guard.nft
nft -c -f /etc/kbot-metadata-guard.nft
nft -f /etc/kbot-metadata-guard.nft
if ! grep -q kbot-metadata-guard /etc/nftables.conf; then
  echo 'include "/etc/kbot-metadata-guard.nft"' >>/etc/nftables.conf
fi
systemctl enable nftables >/dev/null

step "fail2ban on sshd"
install -o root -g root -m 0644 "$repo/deploy/etc/fail2ban/jail.d/kbot.conf" /etc/fail2ban/jail.d/kbot.conf
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

step "security updates every day, without automatic reboots"
install -o root -g root -m 0644 "$repo/deploy/etc/apt/apt.conf.d/52kbot-unattended" \
  /etc/apt/apt.conf.d/52kbot-unattended
systemctl enable --now unattended-upgrades >/dev/null

step "shared REST lock (E4-D06) and the systemd units"
install -o root -g root -m 0644 "$repo/deploy/etc/tmpfiles.d/kbot.conf" /etc/tmpfiles.d/kbot.conf
systemd-tmpfiles --create /etc/tmpfiles.d/kbot.conf
for unit in "$repo"/deploy/systemd/*; do
  install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/svc-ingest.service \
  /etc/systemd/system/kbot-archive-prepare.service /etc/systemd/system/kbot-archive-daily.service \
  /etc/systemd/system/kbot-archive-daily.timer

step "done: nothing is enabled yet; 02_postgresql.sh and 03_codigo.sh come next"
