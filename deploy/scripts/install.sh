#!/usr/bin/env bash
# subarr quickstart installer.
#
# Drops a Tier-2 compose.yaml + .env stub into the current directory,
# picks a free host port if 9922 is taken, and brings the stack up.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/coaxk/subarr/main/deploy/scripts/install.sh | bash
#   # or, after curl-down:
#   bash install.sh [--tier 1|2|3] [--dir ./subarr]

set -euo pipefail

# ─── Config + defaults ─────────────────────────────────────────────
TIER="2"
TARGET_DIR=""
RAW_URL_BASE="https://raw.githubusercontent.com/coaxk/subarr/main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier) TIER="$2"; shift 2 ;;
    --dir)  TARGET_DIR="$2"; shift 2 ;;
    -h|--help)
      cat <<EOF
subarr quickstart installer

Options:
  --tier {1,2,3}   permission tier (default: 2, recommended)
                   1 = no docker access (manual config)
                   2 = read-only socket proxy (auto-detect URLs)
                   3 = tier 2 + config-volume mounts (auto-extract API keys)
  --dir <path>     install dir (default: ./subarr)

See deploy/templates/README.md in the repo for tier details.
EOF
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$TIER" in
  1) TEMPLATE="tier1-no-docker.compose.yaml" ;;
  2) TEMPLATE="tier2-socket-proxy.compose.yaml" ;;
  3) TEMPLATE="tier3-config-mounts.compose.yaml" ;;
  *) echo "invalid --tier '$TIER' (must be 1, 2, or 3)" >&2; exit 2 ;;
esac

if [[ -z "$TARGET_DIR" ]]; then
  TARGET_DIR="./subarr"
fi
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

# ─── Pre-flight ────────────────────────────────────────────────────
echo "==> subarr installer — tier $TIER → $TARGET_DIR"

if ! command -v docker &>/dev/null; then
  echo "error: docker not found in PATH. Install Docker first:" >&2
  echo "       https://docs.docker.com/engine/install/" >&2
  exit 3
fi

if ! docker compose version &>/dev/null; then
  echo "error: 'docker compose' (v2) required. Found older 'docker-compose'?" >&2
  echo "       Upgrade Docker Engine to a recent version." >&2
  exit 3
fi

# ─── Port detection ────────────────────────────────────────────────
# Prefer 9922; pick a random high port if taken.
PORT=9922
if ss -tln 2>/dev/null | grep -q ":${PORT} " || \
   netstat -tln 2>/dev/null | grep -q ":${PORT} "; then
  PORT=$(( ( RANDOM % 10000 ) + 30000 ))
  echo "==> port 9922 already in use; picked $PORT instead"
else
  echo "==> port $PORT available"
fi

# ─── Fetch template + env ─────────────────────────────────────────
echo "==> fetching compose template ($TEMPLATE)"
curl -fsSL "$RAW_URL_BASE/deploy/templates/$TEMPLATE" -o compose.yaml

if [[ ! -f .env ]]; then
  echo "==> fetching .env.example"
  curl -fsSL "$RAW_URL_BASE/deploy/templates/.env.example" -o .env
  # Best-effort detection of common volume host paths
  if [[ -d /mnt/nas/Media ]]; then
    sed -i.bak "s|MEDIA_ROOT=.*|MEDIA_ROOT=/mnt/nas/Media|" .env && rm .env.bak
  fi
  TZ_VALUE="$(timedatectl show --property=Timezone --value 2>/dev/null || date +%Z)"
  if [[ -n "$TZ_VALUE" ]]; then
    sed -i.bak "s|TZ=.*|TZ=$TZ_VALUE|" .env && rm .env.bak
  fi
  echo "==> created .env (review + edit MEDIA_ROOT, TZ, integration creds)"
else
  echo "==> .env already present; not overwriting"
fi

# Adjust port in compose if non-default
if [[ "$PORT" != "9922" ]]; then
  sed -i.bak "s|\"9922:9922\"|\"${PORT}:9922\"|" compose.yaml && rm compose.yaml.bak
  echo "==> compose.yaml updated to map host port $PORT → container 9922"
fi

# ─── Network sanity ────────────────────────────────────────────────
if ! docker network inspect media-stack &>/dev/null; then
  echo ""
  echo "==> note: docker network 'media-stack' doesn't exist on this host"
  echo "    Edit compose.yaml networks section to use your existing"
  echo "    *arr stack network name, OR create one:"
  echo "      docker network create media-stack"
  echo "    Then re-run: docker compose up -d"
  echo ""
fi

# ─── Bring up ──────────────────────────────────────────────────────
echo "==> starting subarr"
docker compose up -d

echo ""
echo "──────────────────────────────────────────────────────────────"
echo "  subarr is starting up."
echo ""
echo "  URL:    http://localhost:$PORT"
echo "  Logs:   docker compose -f $TARGET_DIR/compose.yaml logs -f subarr"
echo "  Stop:   docker compose -f $TARGET_DIR/compose.yaml down"
echo ""
echo "  Next: open the URL in your browser and walk through the wizard."
echo "──────────────────────────────────────────────────────────────"
