# falklands/core/intent_bridge.py
from __future__ import annotations
import re

_HEADING_RE = re.compile(r"\b(heading|course)\s*[:=]?\s*(\d{1,3})\b")
_SPEED_RE   = re.compile(r"\b(speed|knots?|kts?)\s*[:=]?\s*(\d{1,2})\b")
_COME_RE    = re.compile(r"\b(come\s+(right|left)\s+to|turn\s+to|set\s+course)\s+(\d{1,3})\b")
_MAKE_TURNS = re.compile(r"\b(make\s+turns\s+for|speed\s+to)\s+(\d{1,2})\s*(knots?|kts?)?\b")
_TARGET_ID  = re.compile(r"\b(contact|target)\s*([A-Z]\d{2,3})\b", re.IGNORECASE)
_WEAPON_NAME= re.compile(r"\b(Sea\s+Cat|Sea\s+Wolf|Exocet|4\.?5(?:\"|-inch)?\s*gun|20\s*mm(?:\s*cannon)?)\b", re.IGNORECASE)
_GRID       = re.compile(r"\b([A-Z])\s?(\d{1,2})\b")

def infer_commands(user_text: str) -> list[str]:
    """
    Turn natural captain speech into one or more canonical /commands.
    Keep this deterministic and explicit.
    """
    t = user_text.strip().lower()
    cmds: list[str] = []

    # === Navigation ===
    # Direct "heading 270" / "course 090"
    m = _HEADING_RE.search(t)
    if m:
        hdg = int(m.group(2)) % 360
        cmds.append(f"/nav set heading={hdg}")
    # Direct "speed 20" / "20 knots"
    m = _SPEED_RE.search(t)
    if m:
        spd = max(0, min(32, int(m.group(2))))  # clamp to 32
        cmds.append(f"/nav set speed={spd}")
    # "come right/left to 270" / "turn to 090" / "set course 180"
    m = _COME_RE.search(t)
    if m:
        hdg = int(m.group(3)) % 360
        cmds.append(f"/nav set heading={hdg}")
    # "make turns for 20 knots" / "speed to 15"
    m = _MAKE_TURNS.search(t)
    if m:
        spd = max(0, min(32, int(m.group(2))))
        cmds.append(f"/nav set speed={spd}")

    # === Radar ===
    if any(p in t for p in [
        "scan radar", "radar scan", "sweep radar", "check radar", "scan the horizon", "perform a scan"
    ]):
        cmds.append("/radar scan")

    if any(p in t for p in [
        "report contacts", "what's on radar", "radar picture", "give me a picture",
        "list contacts", "targets list", "give me a list of current tracked targets", "sitrep", "status report"
    ]):
        cmds.append("/radar show")

    # Add contact manually if you say "mark contact at K13 type aircraft hostile"
    if "mark contact" in t or "add contact" in t:
        # try to parse grid and type
        grid = _GRID.search(user_text.upper())
        ctype = None
        if "aircraft" in t: ctype = "aircraft"
        elif "surface" in t: ctype = "surface"
        elif "missile" in t: ctype = "missile"
        if grid and ctype:
            col, row = grid.groups()
            cmds.append(f"/targets add type={ctype} col={col} row={row}")

    # === Weapons ===
    if any(p in t for p in [
        "bring weapons online", "weapons online", "weapons on", "arm weapons", "go to high alert", "weapons to high alert"
    ]):
        cmds.append("/weapons arm")

    if any(p in t for p in [
        "stand down weapons", "weapons offline", "weapons off", "safe weapons", "weapons to safe", "weapons safe"
    ]):
        cmds.append("/weapons safe")

    if any(p in t for p in [
        "inventory", "weapons inventory", "what do we have", "how many weapons", "ammunition on board"
    ]):
        cmds.append("/weapons show")

    # select weapon by name
    m = _WEAPON_NAME.search(user_text)
    if m and any(kw in t for kw in ["select", "arm the", "use the", "ready the"]):
        name = m.group(0)
        cmds.append(f"/weapons select name={name}")

    # engage contact X with weapon Y
    if "engage" in t or "fire" in t:
        # try to get target id and/or weapon
        tgt = None
        m = _TARGET_ID.search(user_text)
        if m: tgt = m.group(2).upper()
        weap = None
        m2 = _WEAPON_NAME.search(user_text)
        if m2: weap = m2.group(0)
        if tgt and weap:
            cmds.append(f"/weapons engage target={tgt} weapon={weap}")
        elif tgt:
            cmds.append(f"/weapons engage target={tgt}")
        elif weap:
            # will assume current primary target if engine supports it
            cmds.append(f"/weapons engage weapon={weap}")

    # === Engineering ===
    if any(p in t for p in [
        "engineering report", "status of engines", "engine status", "propulsion status", "engineering status"
    ]):
        cmds.append("/engine status")

    if any(p in t for p in [
        "any malfunctions", "report damage", "damage report", "malfunctions on board"
    ]):
        cmds.append("/engine report")

    if "repair" in t or "fix" in t or "countermeasures" in t:
        # naive extract of a system name; you can tighten later
        sys_name = "engines"
        if "radar" in t: sys_name = "radar"
        elif "weapons" in t: sys_name = "weapons"
        elif "hull" in t or "leak" in t: sys_name = "hull"
        cmds.append(f"/engine repair system={sys_name}")

    # De-duplicate while preserving order
    seen = set()
    uniq = []
    for c in cmds:
        if c not in seen:
            uniq.append(c); seen.add(c)
    return uniq