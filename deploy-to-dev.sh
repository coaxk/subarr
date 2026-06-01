#!/bin/bash
# Quick deploy: docker cp the changed source/bundles into the running
# subarr-next dev container and restart. Faster than docker build for
# WSL-on-Windows because it bypasses the 9P fs caching issue.
#
# v2 (post-#10 launch-polish): the running container has the package
# installed at three different paths and the HTTP server resolves
# static files from the SITE-PACKAGES copy, NOT the source tree. We
# copy to all three so the changes are picked up regardless of which
# path serves a given request:
#   1. /app/src/...                                   (dev source tree)
#   2. /app/build/lib/subarr/...                      (build artifact)
#   3. /usr/local/lib/python3.12/site-packages/subarr/...  (pip install)
# The third is the one that actually matters; the first two we keep
# in sync so future inspectors aren't misled.
set -e
cd /mnt/c/Projects/subarr-next

# (source-relative-path . container-target-path) pairs.
# For each source we write into all three install locations.
SRC_PATHS=(
  src/subarr/static/v1/home-hifi/settings.bundle.js
  src/subarr/static/v1/home-hifi/settings.bundle.js.map
  src/subarr/static/v1/home-hifi/coverage.bundle.js
  src/subarr/static/v1/home-hifi/coverage.bundle.js.map
  src/subarr/static/v1/home-hifi/chrome.jsx
  src/subarr/static/v1/home-hifi/settings.jsx
  src/subarr/static/v1/home-hifi/coverage.jsx
  src/subarr/static/v1/settings.html
  src/subarr/static/v1/coverage.html
  src/subarr/routers/telemetry.py
  src/subarr/routers/vision.py
  src/subarr/routers/integrations.py
  src/subarr/telemetry.py
  src/subarr/config.py
  src/subarr/integrations/ollama.py
  src/subarr/integrations/plex.py
  src/subarr/migrations/005_telemetry_error_timestamp.sql
)

PY_VER=$(docker exec subarr-next python -c 'import sys; print(f"python{sys.version_info[0]}.{sys.version_info[1]}")')
SITE_PKG="/usr/local/lib/$PY_VER/site-packages/subarr"

for src in "${SRC_PATHS[@]}"; do
  [ -f "$src" ] || { echo "  skip (missing): $src"; continue; }
  # Strip the leading "src/subarr/" prefix to compute the in-package path
  rel="${src#src/subarr/}"
  # 1. source tree
  docker cp "$src" "subarr-next:/app/$src"
  # 2. build artifact (may not always exist; ignore failure)
  docker cp "$src" "subarr-next:/app/build/lib/subarr/$rel" 2>/dev/null || true
  # 3. site-packages (the one that actually serves HTTP)
  docker cp "$src" "subarr-next:$SITE_PKG/$rel"
  echo "  cp $rel"
done

echo "restarting subarr-next..."
docker restart subarr-next
echo "done. site-packages: $SITE_PKG"
