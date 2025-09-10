#!/usr/bin/env bash
set -euo pipefail

BASE="http://127.0.0.1:${PORT:-5055}"

PY="${VIRTUAL_ENV:-}/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3 || true)"

pass=true

check_health() {
  local body
  body="$(curl -sS "$BASE/health" || true)"
  if echo "$body" | ${PY:-python3} - <<'PY' 2>/dev/null; then
import sys, json
try:
  j=json.load(sys.stdin)
  ok=bool(j.get('ok'))
  print('OK' if ok else 'FAIL')
except Exception:
  print('FAIL')
PY
  then :; else echo "[health] parser error"; pass=false; fi | (
    read res; if [[ "$res" == "OK" ]]; then echo "[health] PASS"; else echo "[health] FAIL"; exit 1; fi
  ) || pass=false
}

check_status() {
  local body
  body="$(curl -sS "$BASE/api/status" || true)"
  # ok flag
  if echo "$body" | ${PY:-python3} - <<'PY' 2>/dev/null; then
import sys, json
try:
  j=json.load(sys.stdin)
  print('OK' if j.get('ok') else 'FAIL')
except Exception:
  print('FAIL')
PY
  then :; else echo "[status.ok] parser error"; pass=false; fi | (
    read res; if [[ "$res" == "OK" ]]; then echo "[status.ok] PASS"; else echo "[status.ok] FAIL"; exit 1; fi
  ) || pass=false
  # weapons non-empty
  if echo "$body" | ${PY:-python3} - <<'PY' 2>/dev/null; then
import sys, json
try:
  j=json.load(sys.stdin)
  arr=j.get('weapons') or []
  print('OK' if isinstance(arr, list) and len(arr)>=1 else 'FAIL')
except Exception:
  print('FAIL')
PY
  then :; else echo "[status.weapons] parser error"; pass=false; fi | (
    read res; if [[ "$res" == "OK" ]]; then echo "[weapons] PASS"; else echo "[weapons] FAIL"; exit 1; fi
  ) || pass=false
  # contacts shape sanity (if contacts exist, check first)
  if echo "$body" | ${PY:-python3} - <<'PY' 2>/dev/null; then
import sys, json
try:
  j=json.load(sys.stdin)
  arr=j.get('contacts') or []
  if not isinstance(arr, list) or not arr:
    print('SKIP')
  else:
    c=arr[0]
    ok = isinstance(c.get('cell'), str) and isinstance(c.get('name'), str) and isinstance(c.get('type'), str)
    ok = ok and isinstance(c.get('id'), (int,))
    ok = ok and isinstance(c.get('range_nm'), (int,float)) and isinstance(c.get('course'), (int,)) and isinstance(c.get('speed'), (int,))
    print('OK' if ok else 'FAIL')
except Exception:
  print('FAIL')
PY
  then :; else echo "[contacts.shape] parser error"; pass=false; fi | (
    read res; case "$res" in OK) echo "[contacts] PASS";; SKIP) echo "[contacts] SKIP (none)";; *) echo "[contacts] FAIL"; exit 1;; esac
  ) || pass=false
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

