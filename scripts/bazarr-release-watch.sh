#!/usr/bin/env bash
# Weekly check: has Bazarr published a stable release subarr has not been
# verified against?
#
# Why: subarr talks to Bazarr's HTTP API (status, badges, wanted, history,
# blacklist, providers, tasks) and nothing watched Bazarr releases. 1.6.1
# shipped 2026-09-15 and was noticed only because the maintainer updated his
# own stack. Verifying it found #550, a parameter-name bug present since v1.1.
#
# The verified version lives in .github/bazarr-verified-version. A release that
# differs opens (or comments on) one sticky issue with the checklist below.
# After verifying, bump that file in a PR and close the issue.
#
# Outcomes are named, never inferred from silence:
#   MATCH       latest stable == verified, exit 0
#   NEW         latest stable != verified, report filed (or printed on DRY_RUN)
#   UNREACHABLE the GitHub API gave no usable tag, exit 1 so the run goes red
#
# Test hooks: BAZARR_WATCH_LATEST=<tag> skips the API call;
# BAZARR_WATCH_DRY_RUN=1 prints the report instead of filing it.
set -euo pipefail

VERIFIED_FILE="${VERIFIED_FILE:-.github/bazarr-verified-version}"
UPSTREAM="morpheus65535/bazarr"

verified="$(tr -d '[:space:]' < "$VERIFIED_FILE")"
if ! [[ "$verified" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "BAD-VERIFIED-FILE: '$verified' in $VERIFIED_FILE is not a vX.Y.Z tag" >&2
  exit 1
fi

if [ -n "${BAZARR_WATCH_LATEST:-}" ]; then
  latest="$BAZARR_WATCH_LATEST"
else
  # releases/latest excludes drafts and pre-releases (Bazarr ships a beta most
  # days; only stable releases reach users on the default image tags).
  latest="$(gh api "repos/${UPSTREAM}/releases/latest" --jq .tag_name 2>/dev/null || true)"
fi

if ! [[ "$latest" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "UNREACHABLE: no usable latest-release tag from ${UPSTREAM} (got '${latest}')" >&2
  exit 1
fi

if [ "$latest" = "$verified" ]; then
  echo "MATCH: Bazarr latest stable ${latest} == verified ${verified}"
  exit 0
fi

echo "NEW: Bazarr latest stable ${latest}, subarr verified against ${verified}"

body="$(mktemp)"
{
  echo "Bazarr **${latest}** is out. subarr was last verified against **${verified}**."
  echo
  echo "Release notes: https://github.com/${UPSTREAM}/releases/tag/${latest}"
  echo "Changes since ours: https://github.com/${UPSTREAM}/compare/${verified}...${latest}"
  echo
  echo "### Verify against a live ${latest}"
  echo
  echo "- [ ] Each endpoint subarr calls returns 200 with the fields subarr reads:"
  echo "      \`/api/system/status\`, \`/api/badges\`, \`/api/episodes/wanted\`, \`/api/movies/wanted\`,"
  echo "      \`/api/episodes/history\`, \`/api/movies/history\`, \`/api/episodes/blacklist\`,"
  echo "      \`/api/movies/blacklist\`, \`/api/providers\`, \`/api/providers/episodes\`,"
  echo "      \`/api/providers/movies\`, \`/api/episodes/subtitles\`, \`/api/movies/subtitles\`, \`/api/system/tasks\`"
  echo "      (the list lives in \`src/subarr/integrations/bazarr.py\`)"
  echo "- [ ] Query and form parameter names still match Bazarr's \`add_argument\` names in"
  echo "      \`bazarr/api/**\`. Bazarr ignores unknown parameters, so a rename fails silently (#550)."
  echo "- [ ] Per-file history still filters: \`episodeid\` / \`radarrid\` return only that item."
  echo "- [ ] Release notes: anything touching the WhisperAI provider, the API, auth, or Sonarr/Radarr sync."
  echo "- [ ] subarr-next logs show no Bazarr errors after the update."
  echo
  echo "**Then** bump \`.github/bazarr-verified-version\` to \`${latest}\` in a PR and close this issue."
  echo "Until then this issue gets a weekly comment; the next release after closing opens a fresh one."
  if [ -n "${RUN_URL:-}" ]; then
    echo
    echo "Run: ${RUN_URL}"
  fi
} > "$body"

if [ "${BAZARR_WATCH_DRY_RUN:-0}" = "1" ]; then
  echo "DRY_RUN: would file this report:"
  cat "$body"
  exit 0
fi

bash scripts/sticky-issue.sh \
  "${GITHUB_REPOSITORY:-coaxk/subarr}" \
  "bazarr-release" \
  "Bazarr ${latest} released: verify subarr compatibility" \
  "$body" \
  "bazarr-release,backend" \
  "still unverified as of $(date -u +%Y-%m-%d): Bazarr latest stable is ${latest}, verified file says ${verified}"
