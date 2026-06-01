#!/bin/bash
echo "=== all settings.bundle.js paths ==="
docker exec subarr-next find /app -name 'settings.bundle.js' -printf '%p %s bytes\n'
echo ""
echo "=== settings.html script tags ==="
docker exec subarr-next cat /app/src/subarr/static/v1/settings.html | grep -i 'bundle\|script'
echo ""
echo "=== count of RailFooter usage markers in deployed settings bundle ==="
docker exec subarr-next sh -c 'grep -oE "useState\(null\),\[(a|t)\]=useState\(null\)" /app/src/subarr/static/v1/home-hifi/settings.bundle.js | head -3'
docker exec subarr-next sh -c 'grep -oE "/api/home/dashboard|/api/queue" /app/src/subarr/static/v1/home-hifi/settings.bundle.js | sort -u'
echo ""
echo "=== quick check what the file ends with (sanity check it's not truncated) ==="
docker exec subarr-next sh -c 'tail -c 200 /app/src/subarr/static/v1/home-hifi/settings.bundle.js'
