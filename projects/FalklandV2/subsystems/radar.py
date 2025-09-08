"""
Radar subsystem — spawns and reports contacts.
Supports wave spawning with lull periods (driven by game.json -> "radar").
"""

import random
from typing import Dict, List, Optional, Tuple

# Use absolute imports (works when launched as scripts)
from subsystems import contacts


# --- Public API --------------------------------------------------------------

def status_line(pool, ship_xy: Tuple[float, float], locked_id: Optional[int] = None, max_list: int = 3) -> str:
    """Short radar status string with optional locked target and top-N nearest."""
    sx, sy = ship_xy
    contacts_sorted = sorted(pool.contacts, key=lambda c: contacts.dist_nm_xy(c.x, c.y, sx, sy, pool.grid))
    n = len(contacts_sorted)
    line = f"{n} contact(s)"

    if locked_id:
        tgt = next((c for c in contacts_sorted if c.id == locked_id), None)
        if tgt:
            rng = round(contacts.dist_nm_xy(tgt.x, tgt.y, sx, sy, pool.grid), 1)
            cell = contacts.format_cell(int(round(tgt.x)), int(round(tgt.y)))
            line += f" | locked: {cell} {tgt.type} {tgt.allegiance} d={rng}nm (#{tgt.id})"

    if contacts_sorted:
        descs = []
        for c in contacts_sorted[:max_list]:
            rng = round(contacts.dist_nm_xy(c.x, c.y, sx, sy, pool.grid), 1)
            cell = contacts.format_cell(int(round(c.x)), int(round(c.y)))
            descs.append(f"{cell} {c.type} {c.allegiance} d={rng}nm (#{c.id})")
        line += " | " + " | ".join(descs)

    return "RADAR: " + line


def unlock_contact(state: Dict) -> None:
    state.setdefault("radar", {})["locked_contact_id"] = None


def lock_contact(state: Dict, cid: int) -> None:
    state.setdefault("radar", {})["locked_contact_id"] = cid


# --- Wave spawning with lulls ------------------------------------------------

_last_spawn_ts: float = 0.0
_next_spawn_delay: float = 0.0

def auto_tick(engine, now: float) -> List[str]:
    """
    Called each engine tick; may spawn a new wave if delay elapsed.
    Returns list of log lines for newly spawned contacts.
    """
    global _last_spawn_ts, _next_spawn_delay

    cfg = engine.game_cfg.get("radar", {})
    spawn_min = int(cfg.get("spawn_interval_min", 60))
    spawn_max = int(cfg.get("spawn_interval_max", 180))
    wave_min = int(cfg.get("wave_size_min", 2))
    wave_max = int(cfg.get("wave_size_max", 5))
    max_contacts = int(cfg.get("max_contacts", 10))

    logs: List[str] = []

    # If it's time for a new wave…
    if now - _last_spawn_ts >= _next_spawn_delay:
        # …and we're below the cap, spawn a wave
        if len(engine.pool.contacts) < max_contacts:
            wave_size = random.randint(wave_min, wave_max)
            for _ in range(wave_size):
                c = contacts.spawn_contact(engine.pool)
                if c:
                    sx, sy = engine._ship_xy()
                    rng = round(contacts.dist_nm_xy(c.x, c.y, sx, sy, engine.pool.grid), 1)
                    cell = contacts.format_cell(int(round(c.x)), int(round(c.y)))
                    logs.append(
                        f"NEW CONTACT: {cell} {c.type} ({c.allegiance}) d={rng}nm crs {c.course_deg:.0f}° {c.speed_kts_game:.0f}kts"
                    )

        # Schedule next wave regardless (lull)
        _last_spawn_ts = now
        _next_spawn_delay = random.randint(spawn_min, spawn_max)

    return logs