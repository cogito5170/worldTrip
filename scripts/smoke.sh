#!/usr/bin/env bash
# 배포 연기 시험 -- 서버를 띄워 healthz -> 앱 -> /api/plan -> /mcp -> 원장 을 끝까지 돌린다.
# 셸 변수는 아스키만(se_new CLAUDE.md).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
port="${PORT:-18766}"
ledger_dir="$(mktemp -d)"
log_file="$(mktemp)"
cd "$here"
# FRONTEND=<gentleMonster>/gentle_monster/apps/worldtrip 를 주면 화면까지 붙여서 본다
WORLDTRIP_LEDGER_ROOT="$ledger_dir" WORLDTRIP_PORT="$port" WORLDTRIP_FRONTEND="${FRONTEND:-}" python3 -m worldtrip serve >"$log_file" 2>&1 &
server_pid=$!
cleanup() { kill "$server_pid" 2>/dev/null || true; rm -rf "$ledger_dir" "$log_file"; }
trap cleanup EXIT
base="http://127.0.0.1:$port"
for _ in $(seq 1 50); do curl -fsS "$base/healthz" >/dev/null 2>&1 && break; sleep 0.1; done
curl -fsS "$base/healthz" | python3 -c 'import json,sys; assert json.load(sys.stdin)["ok"]; print("healthz ok")'
if [ -n "${FRONTEND:-}" ]; then
  curl -fsS "$base/" | grep -q "World Trip" && echo "frontend ok"
  curl -fsS "$base/app.js" | grep -q "/api/plan" && echo "frontend js ok"
else
  curl -fsS "$base/" | grep -q "worldtrip app" && echo "no-frontend guide ok"
fi
req='{"request":{"start_date":"2026-11-02","nights":6,"travelers":2,"budget_krw":12000000,"style":"mid","must":["rome"],"consider":["florence"],"max_cities":2}}'
verdict="$(curl -fsS -X POST -H 'Content-Type: application/json' -d "$req" "$base/api/plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["verdict"])')"
echo "api/plan verdict: $verdict"; test "$verdict" = "ACCEPT"
mcp='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"city_guide","arguments":{"city":"paris"}}}'
city="$(curl -fsS -X POST -H 'Content-Type: application/json' -d "$mcp" "$base/mcp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["structuredContent"]["city"]["id"])')"
echo "mcp city_guide: $city"; test "$city" = "paris"
entries="$(curl -fsS "$base/api/ledger" | python3 -c 'import json,sys; c=json.load(sys.stdin)["chain"]; assert c["ok"]; print(c["entries"])')"
echo "ledger entries: $entries"; test "$entries" = "1"
echo "SMOKE OK"
