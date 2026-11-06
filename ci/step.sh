#!/usr/bin/env bash
# Run one CI command. On failure, publish the end of its output as a GitHub annotation:
# annotations of a public repository can be read through the API without access to logs.
# Usage: ci/step.sh "<title>" <command> [arguments...]
set -uo pipefail

title="$1"
shift
log="$(mktemp)"

"$@" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}

if [ "$status" -ne 0 ]; then
  tail_text="$(tail -n 60 "$log")"
  tail_text="${tail_text//'%'/'%25'}"
  tail_text="${tail_text//$'\r'/'%0D'}"
  tail_text="${tail_text//$'\n'/'%0A'}"
  safe_title="${title//':'/'%3A'}"
  safe_title="${safe_title//','/'%2C'}"
  echo "::error title=${safe_title} (exit ${status})::${tail_text}"
fi
rm -f "$log"
exit "$status"
