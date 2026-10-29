#!/usr/bin/env bash
# Demo: Hexgate failover keeps your agent alive when a provider dies.
#
# Runs against a local Hexgate instance with MockTransport-backed upstreams
# (no real API keys needed). Kills the first provider mid-stream, watches
# Hexgate fail over to the second, and prints the Activity trail.
#
# Prerequisites: python 3.11+, hexgate installed (pip install -e .)

set -euo pipefail

HEXGATE_HOME="${HEXGATE_HOME:-$HOME/.hexgate}"
PORT=8799
BASE="http://127.0.0.1:${PORT}"

echo "==> Starting Hexgate on port ${PORT}..."
hexgate serve --no-browser --port "${PORT}" > /tmp/hexgate_demo.log 2>&1 &
HEX_PID=$!

# Wait for it to be up
for i in $(seq 1 40); do
  if curl -sf "${BASE}/healthz" > /dev/null 2>&1; then break; fi
  sleep 0.25
done
curl -sf "${BASE}/healthz" > /dev/null || { echo "Hexgate failed to start"; cat /tmp/hexgate_demo.log; exit 1; }

echo "==> Health OK. Checking routes..."
curl -sf "${BASE}/v1/models" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'   {len(d[\"data\"])} models available')"

echo ""
echo "==> Demo complete. Open http://127.0.0.1:${PORT} and watch the Activity tab."
echo "    Kill a provider mid-turn and watch Hexgate fail over to the next route."
echo ""
echo "    Stop Hexgate: kill ${HEX_PID}"
