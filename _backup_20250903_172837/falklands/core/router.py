# falklands/core/router.py
from __future__ import annotations
import os, re, textwrap
from typing import List, Tuple, Dict, Any

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # SAFE mode if SDK missing

MODEL = os.getenv("FK_MODEL", "gpt-4.1-mini")

SYSTEM_PROMPT = """\
You are Ensign Jim Henson aboard HMS Coventry, South Atlantic, 1982.
Radio style: clipped, formal. Address the Captain as “Captain”.
Stay in character. Keep replies short (one sentence if possible).

When appropriate, include 0–2 action lines after your spoken reply.
Each action line MUST start with a slash '/', using ONLY this small DSL:

/status report
/nav set heading=<0-359> [speed=<0-32>]
/nav set speed=<0-32>
/nav show
/radar list
/radar primary auto
/weapons arm
/weapons safe
/weapons inventory
/weapons select name="<weapon name>"
/weapons fire

Rules:
- Use at most two actions per reply.
- Prefer a single action when enough.
- Don’t repeat “Captain” twice. If your sentence starts with “Captain,” do not also prepend “Aye, Captain.”
- If no action is necessary, output just the spoken reply.
- Never invent weapon names; use what the ship reports.
"""

def _summarize_state(state: Dict[str, Any]) -> str:
    cols = int(state.get("MAP_COLS", 100))
    rows = int(state.get("MAP_ROWS", 100))
    pos  = state.get("ship_position", {})
    c = int(round(float(pos.get("col_f", 50.0))))
    r = int(round(float(pos.get("row_f", 50.0))))
    hdg = int(round(float(state.get("ship_course_deg", 270.0))))
    spd = int(round(float(state.get("ship_speed_kn", 15.0))))
    armed = state.get("weapons_armed", False)
    contacts = state.get("contacts", {})
    pid = state.get("primary_id")
    primary = contacts.get(pid) if pid in contacts else None
    primary_str = "none"
    if primary and primary.get("_detected"):
        nm = primary.get("name","contact")
        clk = primary.get("clock","?")
        rng = primary.get("range_nm","?")
        grid = primary.get("grid","?-??")
        primary_str = f"{nm} at {clk} o'clock, {rng:.1f} NM, grid {grid}"
    ammo = state.get("ammo", {})
    ammo_list = ", ".join(f"{k}:{v}" for k,v in ammo.items()) if ammo else "none"

    return textwrap.dedent(f"""\
        Ship grid {c}-{r:02d}, heading {hdg}°, speed {spd} kn.
        Weapons: {'armed' if armed else 'safe'}; ammo: {ammo_list}.
        Primary contact: {primary_str}.
        Map {cols}x{rows}.
    """).strip()

def _parse_actions(text: str) -> Tuple[str, List[str]]:
    lines = [ln.rstrip() for ln in text.splitlines()]
    spoken_lines = []
    actions = []
    for ln in lines:
        if ln.strip().startswith("/"):
            actions.append(ln.strip())
        else:
            spoken_lines.append(ln)
    spoken = "\n".join(spoken_lines).strip()
    # keep it to max 2 actions (safety)
    return spoken, actions[:2]

class Router:
    """Turns captain’s words into a short reply + optional slash actions via LLM."""
    def __init__(self, engine):
        self.engine = engine
        self.client = OpenAI() if OpenAI and os.getenv("OPENAI_API_KEY") else None

    def handle(self, user_text: str) -> Tuple[str, List[str]]:
        st = self.engine.st.data
        state_brief = _summarize_state(st)
        user_text = user_text.strip()
        if not self.client:
            # SAFE fallback: simple heuristics; no LLM
            t = user_text.lower()
            if "status" in t:
                return ("Aye, Captain. Reporting status.", ["/status report"])
            if "speed" in t:
                return ("Aye, Captain. Adjusting speed.", ["/nav show"])
            if "course" in t or "heading" in t:
                return ("Aye, Captain. Setting course.", ["/nav show"])
            if "arm" in t and "weapon" in t:
                return ("Aye, Captain. Bringing weapons online.", ["/weapons arm"])
            # otherwise
            return ("Aye, Captain.", [])
        # LLM path
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"STATE:\n{state_brief}"},
            {"role": "user",   "content": f"CAPTAIN SAID: {user_text}"},
        ]
        resp = self.client.chat.completions.create(
            model=MODEL,
            temperature=0.3,
            messages=msgs,
        )
        out = resp.choices[0].message.content or "Aye, Captain."
        spoken, actions = _parse_actions(out)
        # guard against empty spoken
        if not spoken:
            spoken = "Aye, Captain."
        return spoken, actions