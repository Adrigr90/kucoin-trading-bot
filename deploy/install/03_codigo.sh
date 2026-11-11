#!/usr/bin/env bash
# kbot, stage E4a (02 §13.1, point 1): the code on the server. A clone of the repository
# at the tag the owner approved, owned by the administrator and read-only for the
# services, with the exact dependencies of uv.lock installed by a pinned uv whose
# download is verified by its SHA-256. Run as root:
#
#   sudo bash /opt/kbot/deploy/install/03_codigo.sh <tag>
#
# The first time, clone the repository by hand (docs/operacion/comandos.md) and run this
# script from the clone; later runs update the clone to the new tag.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo)" >&2
  exit 1
fi
tag="${1:?tag approved by the owner, for example v0.2.0-e4a}"
repo=/opt/kbot
uv_version="0.13.7"
# SHA-256 of uv-aarch64-unknown-linux-gnu.tar.gz for that version, from its release
# (checked on the PC when the version was pinned; the CI installs the same version).
uv_sha256="${KBOT_UV_SHA256:?SHA-256 of the uv release tarball for aarch64}"

step() { printf '\n== %s\n' "$*"; }

step "the clone at $tag"
git -C "$repo" fetch --tags origin
git -C "$repo" -c advice.detachedHead=false checkout --quiet "refs/tags/$tag"
git -C "$repo" status --short | grep -q . && { echo "the clone has local changes" >&2; exit 1; }
chown -R ubuntu:ubuntu "$repo"
chmod -R o-w "$repo"
chmod o+rx "$repo"

step "uv $uv_version, verified"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
tarball="uv-aarch64-unknown-linux-gnu.tar.gz"
curl -fsSL --proto '=https' --tlsv1.2 \
  -o "$tmp/$tarball" "https://github.com/astral-sh/uv/releases/download/$uv_version/$tarball"
echo "$uv_sha256  $tmp/$tarball" | sha256sum --check --strict
tar -xzf "$tmp/$tarball" -C "$tmp"
install -o root -g root -m 0755 "$tmp/uv-aarch64-unknown-linux-gnu/uv" /usr/local/bin/uv

step "exactly uv.lock, without the development tools"
sudo -u ubuntu -H env UV_PROJECT_ENVIRONMENT="$repo/.venv" uv sync --frozen --no-dev --directory "$repo"
chmod -R o-w "$repo/.venv"

step "the services can read the code and the environment"
sudo -u kbot_ingest "$repo/.venv/bin/python" -c "import kbot.ingest, kbot.archive; print('ok')"
step "done: $(git -C "$repo" describe --tags)"
