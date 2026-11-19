#!/usr/bin/env bash
# Creates or updates the pull request comment that starts with the drift report marker.
#
# Environment: GH_TOKEN, PR_NUMBER, MARKDOWN (report path), GITHUB_REPOSITORY.
set -euo pipefail

marker='<!-- intensify-drift -->'
comments="repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments"
existing="$(
  gh api --paginate "$comments" \
    --jq ".[] | select(.body | startswith(\"$marker\")) | .id" | head -n 1
)"

if [[ -n "$existing" ]]; then
  gh api --method PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$existing" \
    -F body=@"$MARKDOWN" > /dev/null
else
  gh api --method POST "$comments" -F body=@"$MARKDOWN" > /dev/null
fi
