#!/usr/bin/env bash
set -euo pipefail

BASE="http://127.0.0.1:${PORT:-5055}"

PY="${VIRTUAL_ENV:-}/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3 || true)"

pass=true

_py_eval() {
  # usage: _py_eval label json_body python_code
  local label="$1"; shift
  local body="$1"; shift
  local code="$1"; shift || true
  local res
  res=$(printf '%s' "$body" | ${PY:-python3} - 2>/dev/null <<PY || echo FAIL
import sys, json
try:
    j=json.load(sys.stdin)
    ${code}
except Exception:
    print('FAIL')
PY
)
  case "$res" in
    OK)   echo "[$label] PASS" ;;
    SKIP) echo "[$label] SKIP" ;;
    *)    echo "[$label] FAIL"; pass=false ;;
  esac
}

check_health() {
  local body
  body="$(curl -sS "$BASE/health" || true)"
  _py_eval health "$body" "print('OK' if bool(j.get('ok')) else 'FAIL')"
}

check_status() {
  local body
  body="$(curl -sS "$BASE/api/status" || true)"
  # ok flag
  _py_eval status.ok "$body" "print('OK' if j.get('ok') else 'FAIL')"
  # weapons non-empty
  _py_eval weapons "$body" "arr=j.get('weapons') or []; print('OK' if isinstance(arr,list) and len(arr)>=1 else 'FAIL')"
  # contacts shape (optional)
  _py_eval contacts.shape "$body" "arr=j.get('contacts') or []; 
if not isinstance(arr,list) or not arr:
    print('SKIP')
else:
    c=arr[0];
    ok = isinstance(c.get('cell'), str) and isinstance(c.get('name'), str) and isinstance(c.get('type'), str)
    ok = ok and isinstance(c.get('id'), (int,))
    ok = ok and isinstance(c.get('range_nm'), (int,float)) and isinstance(c.get('course'), (int,)) and isinstance(c.get('speed'), (int,))
    print('OK' if ok else 'FAIL')
"
}

echo "BASE=$BASE"
check_health
check_status

if $pass; then
  echo "SMOKE: PASS"
  exit 0
else
  echo "SMOKE: FAIL"
  exit 1
fi
