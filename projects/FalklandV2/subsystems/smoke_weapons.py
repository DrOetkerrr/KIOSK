import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
for p in (REPO_ROOT, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from projects.falklandV2.subsystems import engage as e  # type: ignore
except Exception:  # pragma: no cover - dev convenience script
    from subsystems import engage as e
ship = json.loads((ROOT / "data" / "ship.json").read_text())
def show(cap): print(f"{cap['key']:<12} ready={cap['ready']} valid={cap['valid']} inrng={cap['in_range']} reason={cap['reason']}")

print("== aircraft @ 2.5 nm ==")
caps = e.summarize(ship, {"type":"Aircraft", "range_nm":2.5})
for c in caps: show(c)

print("\n== surface @ 12 nm ==")
caps = e.summarize(ship, {"type":"Ship", "range_nm":12.0})
for c in caps: show(c)

print("\n== test fire seacat ==")
out = e.fire_once(ship, e.FireRequest(weapon="seacat", target_range_nm=None, target_type=None, mode="test"))
print(out)
