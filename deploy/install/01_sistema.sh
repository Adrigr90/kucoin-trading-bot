#!/usr/bin/env bash
# kbot, stage E4a (02 §9 and §13.1): harden a fresh Ubuntu Server 24.04 ARM64 instance of
# Oracle Cloud and prepare it for the services. Run once as root on the server:
#
#   sudo bash /opt/kbot/deploy/install/01_sistema.sh [data volume device]
#
# It is idempotent: running it again only reapplies the same state. It never touches
# the code (02_codigo.sh) nor PostgreSQL (03_postgresql.sh, stage E4b); 04_verificar.sh
# checks the whole at the end. Every step is listed in docs/operacion/comandos.md.
#
# The firewall is the one the Oracle image ships (iptables, loaded at boot by
# netfilter-persistent), never ufw: Oracle warns that ufw on its Ubuntu images can break
# the rules the instance needs to reach its boot volume (owner decision of 16 Nov 2026).
# Its IPv4 rules stay exactly as the image ships them: new connections only for SSH, and
# Oracle's own rules. SSH is limited to the owner's address in the security list of Oracle,
# which the owner can change from the console at any time; the server does not repeat that
# limit, so a new home address never locks the owner out (owner decision of 17 Nov 2026,
# review FW-02). IPv6, which the image leaves open, is closed.
#
# The data volume of D11 (110 GB) must be attached: without it the script stops before
# creating anything (review of E4a, E4a-03). Only KBOT_ALLOW_BOOT_VOLUME=1, meant for a
# throwaway test instance, lets the data live on the boot volume.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
data_device="${1:-/dev/oracleoci/oraclevdb}"
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
apt-get install -y -qq fail2ban nftables unattended-upgrades age chrony iptables-persistent

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

step "retire the generic nftables.service before touching the firewall (E4a-01)"
# Stopping it runs its ExecStop, `nft flush ruleset`, which would also wipe the rules of
# the firewall if they were already loaded; so it goes first, and its failure is not fatal.
systemctl disable --now nftables >/dev/null 2>&1 || true
systemctl mask nftables >/dev/null 2>&1 || true

step "firewall of the Oracle image: its IPv4 rules as shipped, IPv6 closed"
v4=/etc/iptables/rules.v4
ssh_image='-A INPUT -p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT'
# The IPv4 file of the image is never edited; it must still be the image's.
if ! grep -q '^# iptables configuration for Oracle Cloud Infrastructure' "$v4" ||
  [[ "$(grep -cxF -- "$ssh_image" "$v4")" != "1" ]] ||
  ! grep -q -- '-A InstanceServices -d 169.254.0.2/32 .*--dport 3260' "$v4"; then
  echo "$v4 is not the rules file of the Oracle image: stopping" >&2
  exit 1
fi
ip6tables-restore --test <"$repo/deploy/etc/iptables/rules.v6"
install -o root -g root -m 0640 "$repo/deploy/etc/iptables/rules.v6" /etc/iptables/rules.v6
systemctl enable netfilter-persistent >/dev/null
# The files are loaded replacing the whole filter table, never with `netfilter-persistent
# reload`: the image sets IPTABLES_RESTORE_NOFLUSH=yes in /etc/default/netfilter-persistent,
# so that reload appends the rules of the files to the chains already loaded instead of
# replacing them (on the instance, 17 Nov 2026, every rule of INPUT, FORWARD and OUTPUT
# ended up twice). At boot the chains are empty and netfilter-persistent loads the same
# files. fail2ban, restarted below, adds its own chain again on its next ban.
iptables-restore <"$v4"
ip6tables-restore </etc/iptables/rules.v6

step "metadata guard (E4-D04): only root and the Oracle Cloud Agent may reach the instance metadata"
# Its own unit, loaded after the firewall of the image, and only the table inet kbot_guard.
# The default nftables file of Ubuntu is never used: it flushes the whole ruleset (E4a-01).
install -d -o root -g root -m 0755 /etc/kbot
install -o root -g root -m 0644 "$repo/deploy/etc/kbot/metadata-guard.nft" /etc/kbot/metadata-guard.nft
nft -c -f /etc/kbot/metadata-guard.nft
install -o root -g root -m 0644 "$repo/deploy/systemd/kbot-metadata-guard.service" \
  /etc/systemd/system/kbot-metadata-guard.service
systemctl daemon-reload
systemctl enable --now kbot-metadata-guard >/dev/null
systemctl reload kbot-metadata-guard
nft list table inet kbot_guard >/dev/null

step "fail2ban on sshd"
install -o root -g root -m 0644 "$repo/deploy/etc/fail2ban/jail.d/kbot.conf" /etc/fail2ban/jail.d/kbot.conf
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

step "the firewall as loaded, with fail2ban in place"
# A first look. The full comparison, chain by chain and in order (review FW-01), is
# kbot.ops in 04_verificar.sh, once the code is installed. The rules are read whole before
# grep: `iptables -S | grep -q` fails under pipefail when grep stops reading early and
# iptables gets a broken pipe (it happened on the instance, 17 Nov 2026).
rules4="$(iptables -S)"
rules6="$(ip6tables -S)"
if [[ "$(grep -cxF -- "$ssh_image" <<<"$rules4")" != "1" ]]; then
  echo "the SSH rule of the image is not loaded exactly once" >&2
  exit 1
fi
grep -q -- '-A InstanceServices -d 169.254.0.2/32 .*--dport 3260' <<<"$rules4" ||
  { echo "Oracle's rules are gone" >&2; exit 1; }
grep -qxF -- '-P INPUT DROP' <<<"$rules6" || { echo "IPv6 is not closed" >&2; exit 1; }

step "security updates every day, without automatic reboots"
install -o root -g root -m 0644 "$repo/deploy/etc/apt/apt.conf.d/52kbot-unattended" \
  /etc/apt/apt.conf.d/52kbot-unattended
systemctl enable --now unattended-upgrades >/dev/null

step "shared REST lock (E4-D06) and the systemd units (verified in 04_verificar.sh)"
install -o root -g root -m 0644 "$repo/deploy/etc/tmpfiles.d/kbot.conf" /etc/tmpfiles.d/kbot.conf
systemd-tmpfiles --create /etc/tmpfiles.d/kbot.conf
for unit in "$repo"/deploy/systemd/*; do
  install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload

step "done: nothing is enabled yet; 02_codigo.sh, 03_postgresql.sh and 04_verificar.sh come next"
