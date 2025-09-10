#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-http://127.0.0.1:5055}"

echo "# Verify: Golden Layout v1 — $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
echo "> Base: $BASE"

ABOUT=$(curl -sS "$BASE/about")
LAYOK=$(echo "$ABOUT" | python3 - <<'PY'
import sys,json
j=json.load(sys.stdin)
zones=set(j.get('zones',[]) or [])
ok=(j.get('layout_sentinel')=='v1' and all(z in zones for z in ['CARD-OWNFLEET','CARD-PRIMARY','CARD-WEAPONS','CARD-CAP','CARD-RADIO','CARD-RADAR','CARD-CMDS']))
print('ABOUT_OK' if ok else 'ABOUT_FAIL')
sys.exit(0 if ok else 1)
PY
) || true
echo "$LAYOK"

STAT=$(curl -sS "$BASE/api/status")
python3 - <<'PY'
import sys,json
j=json.load(sys.stdin)
need_keys=['ship_cell','ownfleet','weapons','contacts','threats','cap','radio']
missing=[k for k in need_keys if k not in j]
if missing:
    print('STATUS_FAIL missing', ','.join(missing)); sys.exit(1)
own=j.get('ownfleet',[])
if not isinstance(own,list) or len(own)<3:
    print('OWNFLEET_FAIL'); sys.exit(1)
bad=False
for e in own:
    if 'cell' not in e or 'health_pct' not in e:
        bad=True; break
print('OWNFLEET_OK' if not bad else 'OWNFLEET_FAIL')
w=j.get('weapons',[])
print('WEAPONS_OK' if isinstance(w,list) else 'WEAPONS_FAIL')
c=j.get('contacts',[]); t=j.get('threats',[])
print('CONTACTS_OK' if isinstance(c,list) else 'CONTACTS_FAIL')
print('THREATS_OK' if isinstance(t,list) else 'THREATS_FAIL')
cap=j.get('cap',{})
print('CAP_OK' if isinstance(cap,dict) else 'CAP_FAIL')
r=j.get('radio',[])
print('RADIO_OK' if isinstance(r,list) and len(r)<=4 else 'RADIO_FAIL')
sys.exit(0)
PY

