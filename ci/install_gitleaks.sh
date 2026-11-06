#!/usr/bin/env bash
# Download gitleaks and refuse it unless its SHA-256 matches the pinned value.
# The value was checked against the release's official checksums file on 2026-11-06.
set -euo pipefail

version="8.30.1"
sha256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
folder="${RUNNER_TEMP:?}/gitleaks"
archive="$folder/gitleaks.tar.gz"

mkdir -p "$folder"
curl --fail --silent --show-error --location --retry 3 --output "$archive" \
  "https://github.com/gitleaks/gitleaks/releases/download/v${version}/gitleaks_${version}_linux_x64.tar.gz"
echo "${sha256}  ${archive}" | sha256sum --check --strict -
tar --extract --gzip --file "$archive" --directory "$folder" gitleaks
"$folder/gitleaks" version
