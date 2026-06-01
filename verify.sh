#!/bin/bash
echo "served settings.bundle.js size:"
docker exec subarr-next stat -c '%s bytes  %y' /usr/local/lib/python3.12/site-packages/subarr/static/v1/home-hifi/settings.bundle.js
echo ""
echo "RailFooter markers (should be >0 if new code is live):"
docker exec subarr-next grep -c '/api/home/dashboard' /usr/local/lib/python3.12/site-packages/subarr/static/v1/home-hifi/settings.bundle.js
docker exec subarr-next grep -c 'VRAM\|vram' /usr/local/lib/python3.12/site-packages/subarr/static/v1/home-hifi/settings.bundle.js
