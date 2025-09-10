#!/usr/bin/env bash
set -euo pipefail

# Always run from repo root
cd "$(cd "$(dirname "$0")/.." && pwd)"

BASE="http://127.0.0.1:${PORT:-5055}"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p logs
OUT="$(pwd)/logs/verify_${STAMP}.md"

PY="${VIRTUAL_ENV:-}/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3 || true)"

pp() {
  if [[ -n "${PY}" && -x "${PY}" ]]; then
    "${PY}" - <<'PY' 2>/dev/null || cat
import sys, json
src=sys.stdin.read()
try:
  obj=json.loads(src)
  print(json.dumps(obj, indent=2, ensure_ascii=False))
except Exception:
  sys.stdout.write(src)
PY
  else
    cat
  fi
}

sec() { echo -e "\n## $1\n" >> "$OUT"; }

req() { # method path [curl-args...]
  local M="$1"; shift
  local P="$1"; shift
  local TMP_BODY; TMP_BODY="$(mktemp)"
  local CODE BYTES TIME
  CODE="$(curl -sS -o "$TMP_BODY" -w '%{http_code}' -X "$M" "$BASE$P" "$@")" || CODE="000"
  BYTES="$(wc -c < "$TMP_BODY" | tr -d ' ')"
  TIME="$(curl -sS -o /dev/null -w '%{time_total}' -X "$M" "$BASE$P" "$@" || echo 'ERR')"
  {
    echo '```'
    echo "\$ $M $BASE$P"
    echo
    echo "HTTP $CODE  bytes=$BYTES  time=${TIME}s"
    echo
    cat "$TMP_BODY" | pp
    echo
    echo '```'
  } >> "$OUT"
  rm -f "$TMP_BODY"
}

echo "# FalklandV2 Verify — $(date -u +"%Y-%m-%d %H:%M:%S UTC")" > "$OUT"
echo "> Base: $BASE" >> "$OUT"

sec "Health";                       req GET /health
sec "About (fingerprint)";          req GET /about
sec "Status (pre)";                 req GET /api/status

sec "Clear debug contacts (best-effort)"
if curl -sS -o /dev/null -w '' -X POST "$BASE/debug/clear_contacts"; then
  req POST /debug/clear_contacts
else
  echo "_route missing_" >> "$OUT"
fi

sec "Reload catalog";               req GET /radar/reload_catalog
sec "Force-spawn Hostile";          req GET /radar/force_spawn_hostile
sec "Force-spawn Friendly";         req GET /radar/force_spawn_friendly
sec "Status (post-spawn)";          req GET /api/status
sec "Tail (last 10)";               req GET "/flight/tail?n=10"

# --- Cell grid sanity ------------------------------------------------------
echo "## Cellmap sanity" >> "$OUT"
CELLMAP="$(curl -s "$BASE/debug/cellmap?n=6" || true)"
if echo "$CELLMAP" | grep -q '"ok": true'; then
  CELLS=$(echo "$CELLMAP" | ${PY:-python3} - <<'PY'
import sys,json
d=json.load(sys.stdin)
s=set(c.get("cell") for c in d.get("contacts",[]))
print(",".join(sorted(s)))
PY
)
  echo -e "\nCells: $CELLS" >> "$OUT"
  UNIQUE_COUNT=$(echo "$CELLS" | awk -F',' '{print NF}')
  COUNT=$(echo "$CELLMAP" | ${PY:-python3} - <<'PY'
import sys,json
print(len(json.load(sys.stdin).get("contacts",[])))
PY
)
  if [ "$COUNT" -gt 1 ] && [ "$UNIQUE_COUNT" -eq 1 ]; then
    echo -e "\nWARN: all contacts share the same cell" >> "$OUT"
  fi
else
  echo -e "\n(debug/cellmap unavailable)" >> "$OUT"
fi

# --- Lock/Unlock test driven by top_threat_id ------------------------------
sec "Auto Lock/Unlock (using top_threat_id)"
# fetch top_threat_id
TOP_ID="$(curl -sS "$BASE/api/status" | ${PY:-python3} - <<'PY'
import sys, json
try:
    j=json.load(sys.stdin)
    print(j.get("top_threat_id") or "")
except Exception:
    print("")
PY
)"
{
  echo '```'
  echo "top_threat_id: ${TOP_ID:-<none>}"
  echo '```'
} >> "$OUT"

if [[ -n "${TOP_ID}" ]]; then
  req GET "/api/command?cmd=/radar%20lock%20${TOP_ID}"
  sec "Status (after lock)";        req GET /api/status
  req GET "/api/command?cmd=/radar%20unlock"
  sec "Status (after unlock)";      req GET /api/status
else
  {
    echo
    echo "_No top_threat_id available; skipping lock/unlock._"
    echo
  } >> "$OUT"
fi
# --------------------------------------------------------------------------

sec "Done"
echo "Report written to: $OUT"
echo "$OUT"
