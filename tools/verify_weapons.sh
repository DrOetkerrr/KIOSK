#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-http://127.0.0.1:5055}"

echo "# Verify: Weapons v1 — $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
echo "> Base: $BASE"

# Prep: clear contacts, reload catalog
curl -sS "$BASE/debug/clear_contacts" -X POST >/dev/null || true
curl -sS "$BASE/radar/reload_catalog" >/dev/null || true

# Force hostile ~14nm
ADD=$(curl -sS "$BASE/radar/force_spawn_hostile")
ID=$(echo "$ADD" | python3 - <<'PY'
import sys,json
try:
    j=json.load(sys.stdin)
    print(j.get('added',{}).get('id',''))
except Exception:
    print('')
PY
)

S1=$(curl -sS "$BASE/api/status")
BEFORE_AMMO=$(echo "$S1" | python3 - <<'PY'
import sys,json
j=json.load(sys.stdin)
weps=j.get('weapons',[])
for w in weps:
    if w.get('name')=='MM38 Exocet':
        print(w.get('ammo',0)); break
PY
)
IN_RANGE=$(echo "$S1" | python3 - <<'PY'
import sys,json
j=json.load(sys.stdin)
ok='FAIL'
for w in j.get('weapons',[]):
    if w.get('name')=='MM38 Exocet' and w.get('in_range') is True:
        ok='OK RANGE (Exocet)'; break
print(ok)
PY
)
echo "\n## Range check"
echo "$IN_RANGE"

# Fire test (ammo unchanged)
curl -sS -X POST "$BASE/weapons/fire?name=MM38%20Exocet&mode=test" >/dev/null
S2=$(curl -sS "$BASE/api/status")
AFTER_TEST_AMMO=$(echo "$S2" | python3 - <<'PY'
import sys,json
j=json.load(sys.stdin)
weps=j.get('weapons',[])
for w in weps:
    if w.get('name')=='MM38 Exocet':
        print(w.get('ammo',0)); break
PY
)

# Arm (if needed)
curl -sS -X POST "$BASE/weapons/arm?name=MM38%20Exocet&state=Armed" >/dev/null

# Fire real (ammo decremented by 1)
curl -sS -X POST "$BASE/weapons/fire?name=MM38%20Exocet&mode=real" >/dev/null
S3=$(curl -sS "$BASE/api/status")
AFTER_REAL_AMMO=$(echo "$S3" | python3 - <<'PY'
import sys,json
j=json.load(sys.stdin)
weps=j.get('weapons',[])
for w in weps:
    if w.get('name')=='MM38 Exocet':
        print(w.get('ammo',0)); break
PY
)

echo "\n## Ammo"
echo "Before: $BEFORE_AMMO"
echo "After test: $AFTER_TEST_AMMO"
echo "After real: $AFTER_REAL_AMMO"

PASS=1
[[ "$IN_RANGE" == OK* ]] || PASS=0
[[ "$BEFORE_AMMO" == "$AFTER_TEST_AMMO" ]] || PASS=0
if ! [[ "$AFTER_REAL_AMMO" =~ ^[0-9]+$ ]]; then PASS=0; fi
if [[ $((BEFORE_AMMO-1)) -ne $AFTER_REAL_AMMO ]]; then PASS=0; fi 2>/dev/null || true

echo
if [[ $PASS -eq 1 ]]; then
  echo "Result: PASS"
  exit 0
else
  echo "Result: FAIL"
  exit 1
fi

