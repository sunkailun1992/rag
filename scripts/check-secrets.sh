#!/usr/bin/env bash
# Report service secret warning scan: report suspicious credentials without rewriting files.
# Project owners decide whether a credential should become a placeholder, move to Nacos, or stay in current config.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

excluded='^(scripts/check-secrets.sh|gradle/wrapper/gradle-wrapper.jar|gradlew|gradlew.bat)$'

patterns=(
  'AIza[[:alnum:]_-]{20,}'
  'sk-[[:alnum:]_-]{20,}'
  'MT[[:alnum:]_.-]{30,}'
  'access_token=[[:alnum:]_-]{20,}'
  'SEC[[:alnum:]]{20,}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'AKIA[[:alnum:]]{16}'
  "'[[:alnum:]]{48,}'"
)

failed=0

while IFS= read -r file; do
  if [[ "$file" =~ $excluded ]]; then
    continue
  fi
  if [[ ! -f "$file" ]] || ! grep -Iq . "$file"; then
    continue
  fi
  for pattern in "${patterns[@]}"; do
    matched_lines="$(grep -nE "$pattern" "$file" 2>/dev/null | cut -d: -f1 || true)"
    if [[ -n "$matched_lines" ]]; then
      echo "Potential secret detected in $file:"
      while IFS= read -r line; do
        echo "  line $line matches a suspicious secret pattern"
      done <<< "$matched_lines"
      failed=1
    fi
  done
done < <(git ls-files --cached --others --exclude-standard)

if [[ "$failed" -ne 0 ]]; then
  echo "Secret scan warning. Do not change credentials automatically; report the file and let the project owner decide whether to move or replace them."
  if [[ "${STRICT_SECRET_SCAN:-0}" == "1" ]]; then
    echo "STRICT_SECRET_SCAN=1 enabled, failing because potential secrets were detected."
    exit 1
  fi
  exit 0
fi

echo "Secret scan passed."
