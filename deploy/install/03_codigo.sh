#!/usr/bin/env bash
# kbot, stage E4a (02 §13.1, point 1): the code on the server. A clone of the repository
# at the tag the owner approved, owned by the administrator and read-only for the
# services, with the exact dependencies of uv.lock installed by a pinned uv whose
# download is verified against deploy/install/uv.sha256 (the checksum Claude verified on
# the PC against the tarball and the .sha256 that the release publishes; E4a-07).
# Run as root:
#
#   sudo bash /opt/kbot/deploy/install/03_codigo.sh <tag>
#
# The first time, clone the repository by hand (docs/operacion/comandos.md) and run this
# script from the clone; later runs update the clone to the new tag. At the end it
# verifies the systemd units, which 01_sistema.sh installed, now that their interpreter
# exists (E4a-02).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
tag="${1:?tag approved by the owner, for example v0.2.0-e4a}"
repo=/opt/kbot
uv_version="0.13.7"
tarball="uv-aarch64-unknown-linux-gnu.tar.gz"

step() { printf '\n== %s\n' "$*"; }

step "the clone at $tag"
git -C "$repo" fetch --tags origin
git -C "$repo" -c advice.detachedHead=false checkout --quiet "refs/tags/$tag"
if git -C "$repo" status --short | grep -q .; then
  echo "the clone has local changes" >&2
  exit 1
fi
chown -R ubuntu:ubuntu "$repo"
chmod -R o-w "$repo"
chmod o+rx "$repo"

step "uv $uv_version, verified against deploy/install/uv.sha256"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --proto '=https' --tlsv1.2 \
  -o "$tmp/$tarball" "https://github.com/astral-sh/uv/releases/download/$uv_version/$tarball"
(cd "$tmp" && sha256sum --check --strict "$repo/deploy/install/uv.sha256")
tar -xzf "$tmp/$tarball" -C "$tmp"
install -o root -g root -m 0755 "$tmp/uv-aarch64-unknown-linux-gnu/uv" /usr/local/bin/uv

step "exactly uv.lock, without the development tools"
sudo -u ubuntu -H env UV_PROJECT_ENVIRONMENT="$repo/.venv" uv sync --frozen --no-dev --directory "$repo"
chmod -R o-w "$repo/.venv"

step "the services can read the code and the environment"
sudo -u kbot_ingest "$repo/.venv/bin/python" -c "import kbot.ingest, kbot.archive, kbot.ops; print('ok')"

step "the systemd units, verified with their interpreter in place"
systemctl daemon-reload
units=()
for unit in "$repo"/deploy/systemd/*; do
  units+=("/etc/systemd/system/$(basename "$unit")")
done
systemd-analyze verify --man=no --generators=no "${units[@]}"

step "done: $(git -C "$repo" describe --tags)"
