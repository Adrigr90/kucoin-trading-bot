#!/usr/bin/env bash
# kbot, stage E4a (02 §9 and §13.1): harden a fresh Ubuntu Server 24.04 ARM64 instance of
# Oracle Cloud and prepare it for the services. Run once as root on the server:
#
#   sudo bash /opt/kbot/deploy/install/01_sistema.sh <owner IPv4>/32 [data volume device]
#
# It is idempotent: running it again only reapplies the same state. It never touches
# PostgreSQL (02_postgresql.sh, stage E4b) nor the code (03_codigo.sh). Every step is
# listed in docs/operacion/comandos.md.
#
# The data volume of D11 (110 GB) must be attached: without it the script stops before
# creating anything (review of E4a, E4a-03). Only KBOT_ALLOW_BOOT_VOLUME=1, meant for a
# throwaway test instance, lets the data live on the boot volume.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
owner_ip="${1:?owner IPv4 address with its prefix, for example 83.0.0.1/32}"
data_device="${2:-/dev/oracleoci/oraclevdb}"
repo=/opt/kbot
data=/srv/kbot
label=kbot-data

step() { printf '\n== %s\n' "$*"; }

step "data volume on $data (ext4, noatime)"
if mountpoint -q "$data"; then
  mounted="$(findmnt -no LABEL "$data" || true)"
  if [[ "$mounted" != "$label" ]]; then
    echo "$data is mounted but it is not the volume $label (label: '${mounted:-none}')" >&2
    exit 1
  fi
elif [[ -b "$data_device" ]]; then
  existing="$(blkid -o value -s LABEL "$data_device" || true)"
  if [[ -z "$(blkid -o value -s TYPE "$data_device" || true)" ]]; then
    mkfs.ext4 -q -L "$label" "$data_device"
  elif [[ "$existing" != "$label" ]]; then
    echo "$data_device holds a file system that is not $label (label: '${existing:-none}'): refusing to reuse it" >&2
    exit 1
  fi
  mkdir -p "$data"
  if ! grep -q "LABEL=$label $data " /etc/fstab; then
    echo "LABEL=$label $data ext4 defaults,noatime,nofail 0 2" >>/etc/fstab
  fi
  mount "$data"
elif [[ "${KBOT_ALLOW_BOOT_VOLUME:-0}" == "1" ]]; then
  echo "no data volume at $data_device: KBOT_ALLOW_BOOT_VOLUME=1, using the boot volume (test instance only)" >&2
  mkdir -p "$data"
else
  echo "no data volume at $data_device: attach the 110 GB volume of D11 first (docs/operacion/oracle_instancia.md)" >&2
  exit 1
fi

step "packages: security updates, firewall, fail2ban, nftables, age"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ufw fail2ban nftables unattended-upgrades age chrony

step "system users: one per service, named after its SQL role, without a shell"
getent group kbot >/dev/null || groupadd --system kbot
for user in kbot_ingest kbot_archive kbot_backup kbot_restore kbot_sync; do
  if ! id "$user" >/dev/null 2>&1; then
    useradd --system --shell /usr/sbin/nologin --home-dir /nonexistent --no-create-home "$user"
  fi
done
# The recorder shares the single REST budget of the machine with other REST users (E1).
usermod -a -G kbot kbot_ingest

step "directories of the data volume"
# The archive root exists beforehand and durably (E3b-02): the archiver never creates it.
install -d -o kbot_archive -g kbot_archive -m 0750 "$data/archive"
install -d -o kbot_backup -g kbot_backup -m 0750 "$data/backups"
install -d -o root -g root -m 0755 "$data/sftp"
sync -f "$data"

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
# Its own unit, loaded after ufw, and only the table inet kbot_guard. The default nftables
# file of Ubuntu is never used: it starts by flushing the whole ruleset (E4a-01).
install -d -o root -g root -m 0755 /etc/kbot
install -o root -g root -m 0644 "$repo/deploy/etc/kbot/metadata-guard.nft" /etc/kbot/metadata-guard.nft
nft -c -f /etc/kbot/metadata-guard.nft
systemctl disable --now nftables >/dev/null 2>&1 || true
install -o root -g root -m 0644 "$repo/deploy/systemd/kbot-metadata-guard.service" \
  /etc/systemd/system/kbot-metadata-guard.service
systemctl daemon-reload
systemctl enable --now kbot-metadata-guard >/dev/null
systemctl reload kbot-metadata-guard
nft list table inet kbot_guard >/dev/null
ufw status | grep -q '^Status: active'

step "fail2ban on sshd"
install -o root -g root -m 0644 "$repo/deploy/etc/fail2ban/jail.d/kbot.conf" /etc/fail2ban/jail.d/kbot.conf
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

step "security updates every day, without automatic reboots"
install -o root -g root -m 0644 "$repo/deploy/etc/apt/apt.conf.d/52kbot-unattended" \
  /etc/apt/apt.conf.d/52kbot-unattended
systemctl enable --now unattended-upgrades >/dev/null

step "shared REST lock (E4-D06) and the systemd units (verified in 03_codigo.sh)"
install -o root -g root -m 0644 "$repo/deploy/etc/tmpfiles.d/kbot.conf" /etc/tmpfiles.d/kbot.conf
systemd-tmpfiles --create /etc/tmpfiles.d/kbot.conf
for unit in "$repo"/deploy/systemd/*; do
  install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload

step "done: nothing is enabled yet; 02_postgresql.sh and 03_codigo.sh come next"
