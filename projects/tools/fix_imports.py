# tools/fix_imports.py
from __future__ import annotations
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "projects" / "falklands" / "core",
    ROOT / "projects" / "falklands" / "systems",
    ROOT / "projects" / "falklands",
]

# Patterns we will normalize to:
#   from projects.falklands.systems.radar import RadarSystem
RADAR_STD = "from projects.falklands.systems.radar import RadarSystem\n"

radar_live_variants = [
    r"from\s+projects\.falklands\.systems\.radar_live\s+import\s+RadarLive",
    r"from\s+\.\.\s*systems\.radar_live\s+import\s+RadarLive",
    r"from\s+falklands\.systems\.radar_live\s+import\s+RadarLive",
]

radar_live_in_radar = [
    r"from\s+projects\.falklands\.systems\.radar\s+import\s+RadarLive",
    r"from\s+\.\.\s*systems\.radar\s+import\s+RadarLive",
    r"from\s+falklands\.systems\.radar\s+import\s+RadarLive",
]

radar_system_variants = [
    r"from\s+\.\.\s*systems\.radar\s+import\s+RadarSystem",
    r"from\s+falklands\.systems\.radar\s+import\s+RadarSystem",
]

def normalize_imports(text: str) -> str:
    orig = text

    # any import of RadarLive (from radar_live or radar), rewrite to RadarSystem
    for pat in radar_live_variants + radar_live_in_radar:
        text = re.sub(pat, RADAR_STD, text)

    # any relative/old RadarSystem import → standard absolute import
    for pat in radar_system_variants:
        text = re.sub(pat, RADAR_STD, text)

    # If code later refers to RadarLive symbol, alias it safely:
    # add a line: "RadarLive = RadarSystem" just once if "RadarLive" appears.
    if "RadarLive" in text and "RadarLive = RadarSystem" not in text:
        # add alias right after the standardized import
        text = text.replace(RADAR_STD, RADAR_STD + "RadarLive = RadarSystem  # alias for legacy code\n")

    return text if text != orig else orig

def main():
    changed = 0
    for base in TARGETS:
        for p in base.rglob("*.py"):
            if p.name == "fix_imports.py":
                continue
            s = p.read_text(encoding="utf-8")
            s2 = normalize_imports(s)
            if s2 != s:
                p.write_text(s2, encoding="utf-8")
                changed += 1
                print(f"[fixed] {p.relative_to(ROOT)}")
    print(f"Done. Files changed: {changed}")

if __name__ == "__main__":
    main()