#!/usr/bin/env bash
# Open-or-update a single sticky issue, keyed by a unique label.
#
# Why: a weekly detector without stickiness opens a NEW issue every run while
# the same unresolved episode persists, burying the inbox in duplicates.
# Ported from coaxk/subarr-subgen (where it had already caused that mess).
# Sticky semantics:
#
#   - An OPEN issue carrying $STICKY_LABEL already exists  -> add a comment
#     (one issue accumulates "still happening" updates).
#   - None open (never created, or we fixed + CLOSED the last one) -> create
#     a fresh one from $BODY_FILE.
#
# So one issue per unresolved episode; closing it after we act resets the
# cycle, and a genuinely new occurrence opens a new issue.
#
# Uses gh (native, no third-party action). Requires GH_TOKEN in the env and
# the repo's issues:write permission. Labels passed in must already exist
# (gh does not auto-create them).
#
# Usage:
#   sticky-issue.sh <repo> <sticky_label> <title> <body_file> <labels> <note>
set -euo pipefail

REPO="$1"          # owner/name
STICKY_LABEL="$2"  # the unique key label used to find the open issue
TITLE="$3"         # title used only when creating
BODY_FILE="$4"     # body used only when creating
LABELS="$5"        # comma-separated labels applied on create (must exist)
NOTE="$6"          # comment body appended when the issue already exists

existing="$(gh issue list --repo "$REPO" --state open --label "$STICKY_LABEL" \
  --json number --jq '.[0].number // empty')"

if [ -n "$existing" ]; then
  echo "sticky issue #$existing exists (label=$STICKY_LABEL) — commenting instead of opening a duplicate"
  gh issue comment "$existing" --repo "$REPO" --body "$NOTE"
else
  echo "no open issue for label=$STICKY_LABEL — creating"
  gh issue create --repo "$REPO" --title "$TITLE" --body-file "$BODY_FILE" --label "$LABELS"
fi
