#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-http://127.0.0.1:5055}"

echo "# Verify: Grid refs — $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
echo "> Base: $BASE"

ADD=$(curl -sS "$BASE/radar/force_spawn_hostile")
python3 - <<'PY'
import sys,json,re
try:
    j=json.loads(sys.stdin.read())
    a=j.get('added',{})
    cell=a.get('cell','')
    ok=bool(re.match(r'^[A-Z]{1,2}[1-9][0-9]?$', cell))
    print('SPAWN_CELL', cell)
    print('CELL_OK' if ok else 'CELL_FAIL')
    sys.exit(0 if ok else 1)
except Exception as e:
    print('ERR', e)
    sys.exit(2)
PY

S=$(curl -sS "$BASE/api/status")
python3 - <<'PY'
import sys,json,re
j=json.loads(sys.stdin.read())
ok=True
for d in j.get('contacts',[]):
    cell=str(d.get('cell',''))
    if not re.match(r'^[A-Z]{1,2}[1-9][0-9]?$', cell):
        print('BAD_CELL', cell)
        ok=False
        break
print('ALL_OK' if ok else 'SOME_BAD')
sys.exit(0 if ok else 1)
PY
