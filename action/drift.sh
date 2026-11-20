#!/usr/bin/env bash
# Runs `intensify drift` for the GitHub Action and records outputs.
#
# Environment: SPEC_PATH, OUT_DIR, optional BASE_BRANCH; GITHUB_OUTPUT and GITHUB_STEP_SUMMARY
# are provided by Actions (falling back to stdout when run locally).
set -euo pipefail

out="${GITHUB_OUTPUT:-/dev/stdout}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
sarif="$OUT_DIR/intensify-drift.sarif"
markdown="$OUT_DIR/intensify-drift.md"

args=(drift "$SPEC_PATH" --sarif "$sarif" --markdown "$markdown")
if [[ -n "${BASE_BRANCH:-}" ]]; then
  # Checkouts are shallow; fetch just the base branch tip.
  git fetch --quiet --no-tags --depth=1 origin "$BASE_BRANCH"
  args+=(--base "$(git rev-parse FETCH_HEAD)")
fi

set +e
intensify "${args[@]}"
code=$?
set -e

echo "exit-code=$code" >> "$out"
if [[ -f "$sarif" ]]; then echo "sarif=$sarif" >> "$out"; fi
if [[ -f "$markdown" ]]; then
  echo "markdown=$markdown" >> "$out"
  cat "$markdown" >> "$summary"
fi

# 1 (drift) is handled by a later step so reports are uploaded first; 2 means it couldn't run.
if [[ "$code" -ge 2 ]]; then exit "$code"; fi
