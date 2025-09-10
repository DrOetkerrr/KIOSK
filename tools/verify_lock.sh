#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-http://127.0.0.1:5055}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="logs/verify_lock_${STAMP}.md"
mkdir -p logs

pp() {
  python3 - <<'PY' 2>/dev/null || cat
import sys, json
src=sys.stdin.read()
try:
  obj=json.loads(src)
  print(json.dumps(obj, indent=2, ensure_ascii=False))
except Exception:
  sys.stdout.write(src)
PY
}

echo "# Verify: Radar Lock/Primary — $(date -u +"%Y-%m-%d %H:%M:%S UTC")" > "$OUT"
echo "> Base: $BASE" >> "$OUT"

echo -e "\n## Prep" >> "$OUT"
curl -sS "$BASE/debug/clear_contacts" -X POST >/dev/null || true
curl -sS "$BASE/radar/reload_catalog" >/dev/null

echo -e "\n## Force Hostile" >> "$OUT"
# Retry force-spawn up to 5 times to avoid transient empties
TARGET=""; ADD=""; ATTEMPTS=0
while [[ -z "$TARGET" && $ATTEMPTS -lt 5 ]]; do
  ATTEMPTS=$((ATTEMPTS+1))
  ADD="$(curl -sS -m 3 "$BASE/radar/force_spawn_hostile" || true)"
  TARGET="$(echo "$ADD" | python3 - <<'PY'
import sys, json
s=sys.stdin.read().strip()
try:
    j=json.loads(s)
    print(j.get('added',{}).get('id',''))
except Exception:
    print('')
PY
)"
  [[ -n "$TARGET" ]] || sleep 0.4
done
echo '```' >> "$OUT"; echo "${ADD:-{}}" | pp >> "$OUT"; echo '```' >> "$OUT"

echo -e "\n## Lock" >> "$OUT"
curl -sS "$BASE/api/command?cmd=/radar%20lock%20$TARGET" >/dev/null
STAT=$(curl -sS -m 3 "$BASE/api/status" || echo '{}')
echo '```' >> "$OUT"; echo "$STAT" | pp >> "$OUT"; echo '```' >> "$OUT"
LOCK_OK=$(echo "$STAT" | python3 - <<'PY'
import sys,json
try:
    j=json.load(sys.stdin)
except Exception:
    j={}
p=j.get('primary') if isinstance(j,dict) else None
print('LOCK_OK' if p and isinstance(p,dict) else 'LOCK_FAIL')
print('PRIMARY_ID', (p.get('id') if isinstance(p,dict) else None))
PY
)
echo "$LOCK_OK" >> "$OUT"

echo -e "\n## Unlock" >> "$OUT"
curl -sS "$BASE/api/command?cmd=/radar%20unlock" >/dev/null
STAT2=$(curl -sS -m 3 "$BASE/api/status" || echo '{}')
echo '```' >> "$OUT"; echo "$STAT2" | pp >> "$OUT"; echo '```' >> "$OUT"
UNLOCK_OK=$(echo "$STAT2" | python3 - <<'PY'
import sys,json
try:
    j=json.load(sys.stdin)
except Exception:
    j={}
print('UNLOCK_OK' if (isinstance(j,dict) and 'primary' not in j) else 'UNLOCK_FAIL')
PY
)
echo "$UNLOCK_OK" >> "$OUT"

if ! grep -q 'LOCK_OK' <<<"$LOCK_OK" || ! grep -q 'UNLOCK_OK' <<<"$UNLOCK_OK"; then
  echo "\nResult: FAIL" >> "$OUT"
  echo "$OUT"
  exit 1
fi

echo "\nResult: OK" >> "$OUT"
echo "$OUT"
