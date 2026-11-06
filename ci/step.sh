#!/usr/bin/env bash
# Run one CI command and publish the end of its output as a GitHub annotation: a notice
# when it passes, an error when it fails. Annotations of a public repository can be read
# through the API without signing in, unlike the job logs.
# Usage: ci/step.sh "<title>" <command> [arguments...]
set -uo pipefail

title="$1"
shift
log="$(mktemp)"

"$@" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}

escape_data() {
  local text="$1"
  text="${text//'%'/'%25'}"
  text="${text//$'\r'/'%0D'}"
  text="${text//$'\n'/'%0A'}"
  printf '%s' "$text"
}

safe_title="$(escape_data "$title")"
safe_title="${safe_title//':'/'%3A'}"
safe_title="${safe_title//','/'%2C'}"

if [ "$status" -eq 0 ]; then
  echo "::notice title=${safe_title}::$(escape_data "$(tail -n 4 "$log")")"
else
  echo "::error title=${safe_title} (exit ${status})::$(escape_data "$(tail -n 60 "$log")")"
fi
rm -f "$log"
exit "$status"
