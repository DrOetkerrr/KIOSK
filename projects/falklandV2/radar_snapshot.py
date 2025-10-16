from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
import re


Cell = Dict[str, Any]
Row = Dict[str, Any]
Contact = Dict[str, Any]
ViewContext = Dict[str, Any]


def _col_label(idx: int) -> str:
    hi, lo = divmod(idx, 26)
    return chr(65 + hi) + chr(65 + lo)


def _parse_cell_label(cell: str, *, max_rows: int, max_cols: int) -> Optional[Tuple[int, int]]:
    if not cell:
        return None
    match = re.match(r"^([A-Z]{2})(\d{1,3})$", str(cell).strip().upper())
    if not match:
        return None
    col = match.group(1)
    try:
        row_idx = int(match.group(2))
    except ValueError:
        return None
    col_hi = ord(col[0]) - 65
    col_lo = ord(col[1]) - 65
    if not (0 <= col_hi < 26 and 0 <= col_lo < 26):
        return None
    col_idx = col_hi * 26 + col_lo
    if not (0 <= col_idx < max_cols and 0 <= row_idx < max_rows):
        return None
    return row_idx, col_idx


def _sys_radar_cell_contacts(contacts: List[Contact], *, rows: int, cols: int) -> List[List[List[Contact]]]:
    grid: List[List[List[Contact]]] = [[[] for _ in range(cols)] for _ in range(rows)]
    for contact in contacts:
        cell = str(contact.get("cell") or contact.get("grid") or "").strip().upper()
        pos = _parse_cell_label(cell, max_rows=rows, max_cols=cols)
        if pos is None:
            continue
        row_idx, col_idx = pos
        grid[row_idx][col_idx].append(contact)
    return grid


def _sys_radar_cell_summary(cell_contacts: Iterable[Contact]) -> Cell:
    cell_list = list(cell_contacts)

    def _is_hostile(c: Contact) -> bool:
        return str(c.get("allegiance") or c.get("type") or "").lower() == "hostile"

    def _is_friendly(c: Contact) -> bool:
        return str(c.get("allegiance") or c.get("type") or "").lower() == "friendly"

    def _is_resupply(c: Contact) -> bool:
        hull = str(c.get("hull") or "").strip().lower()
        if hull == "resupply":
            return True
        meta = c.get("meta") if isinstance(c, dict) else {}
        if isinstance(meta, dict) and (meta.get("resupply") or str(meta.get("hull") or "").strip().lower() == "resupply"):
            return True
        return False

    def _is_sheffield(c: Contact) -> bool:
        cid = str(c.get("id") or "").lower()
        if cid in ("fleet:own", "fleet:sheffield"):
            return True
        meta = c.get("meta") if isinstance(c, dict) else {}
        if isinstance(meta, dict) and meta.get("own_ship"):
            return True
        name = str(c.get("name") or "").lower()
        return "sheffield" in name

    def _is_hermes(c: Contact) -> bool:
        cid = str(c.get("id") or "").lower()
        if cid == "fleet:hermes":
            return True
        name = str(c.get("name") or "").lower()
        return "hms hermes" in name

    def _is_harrier(c: Contact) -> bool:
        if c.get("cap_flight") or c.get("cap_callsign"):
            return True
        name = str(c.get("name") or "").lower()
        if "harrier" in name:
            return True
        pennant = str(c.get("pennant") or "").lower()
        return pennant.startswith("cap-")

    hostiles = [c for c in cell_list if _is_hostile(c)]
    sheffield = [c for c in cell_list if _is_sheffield(c)]
    hermes = [c for c in cell_list if _is_hermes(c)]
    harriers = [c for c in cell_list if _is_harrier(c) and not _is_hermes(c)]
    resupply = [c for c in cell_list if _is_resupply(c)]
    friendlies = [c for c in cell_list if _is_friendly(c) and c not in harriers and not _is_hermes(c) and not _is_sheffield(c)]

    harrier_calls = [
        str(c.get("cap_callsign") or "").strip()
        for c in harriers
        if str(c.get("cap_callsign") or "").strip()
    ]

    def _harrier_label() -> str:
        if not harriers:
            return ""
        if len(harriers) == 1 and len(harrier_calls) == 1:
            return harrier_calls[0]
        if len(harrier_calls) >= 2:
            joined = "".join(harrier_calls[:2])
            return joined if len(joined) <= 3 else f"{harrier_calls[0]}+"
        if len(harrier_calls) == 1:
            return harrier_calls[0]
        if len(harriers) > 1:
            return f"S{min(len(harriers), 9)}"
        return "S1"

    friendly_count = len(friendlies) + len(harriers) + len(hermes) + len(sheffield) + len(resupply)

    classes = ["sys-cell"]
    label = ""
    if hostiles and friendly_count:
        classes.append("sys-cell--mixed")
        label = "X"
    elif hostiles:
        classes.append("sys-cell--hostile")
        label = str(min(len(hostiles), 9)) if len(hostiles) > 1 else "E"
    elif resupply:
        classes.extend(["sys-cell--friendly", "sys-cell--resupply"])
        label = "K"
    elif sheffield:
        classes.append("sys-cell--sheffield")
        label = "*"
    elif hermes and harriers:
        classes.extend(["sys-cell--hermes", "sys-cell--harrier"])
        label = _harrier_label() or "S"
    elif hermes:
        classes.append("sys-cell--hermes")
        label = "="
    elif harriers:
        classes.append("sys-cell--harrier")
        label = _harrier_label() or "S"
    elif friendlies:
        classes.append("sys-cell--friendly")
        label = str(min(len(friendlies), 9)) if len(friendlies) > 1 else "F"
    else:
        label = ""

    tooltip_entries = []
    for contact in cell_list:
        allegiance = str(contact.get("allegiance") or contact.get("type") or "").upper()
        base_name = contact.get("name") or "Contact"
        callsign = str(contact.get("cap_callsign") or "").strip()
        display = f"{base_name} ({callsign})" if callsign else base_name
        tooltip_entries.append(f"{allegiance}: {display}")

    tooltip = "\n".join(tooltip_entries)
    return {
        "classes": " ".join(classes),
        "label": label,
        "tooltip": tooltip,
    }


def _format_contact(contact: Contact) -> Contact:
    name = contact.get("name") or contact.get("display_name") or "Contact"
    cell = contact.get("cell") or contact.get("grid") or "—"
    allegiance = str(contact.get("allegiance") or contact.get("type") or "Unknown").title()
    rng = contact.get("range_nm") or contact.get("Range") or ""
    try:
        rng = f"{float(rng):.1f}"
    except Exception:
        rng = str(rng) if rng not in (None, "") else ""
    speed = contact.get("speed") or contact.get("SPD") or ""
    course = contact.get("course") or contact.get("CRS") or ""
    return {
        "cell": cell,
        "allegiance": allegiance,
        "name": name,
        "range_nm": rng,
        "speed": speed,
        "course": course,
    }


def build_radar_view(payload: Dict[str, Any]) -> ViewContext:
    contacts_raw = payload.get("contacts") or []
    contacts = contacts_raw if isinstance(contacts_raw, list) else []
    grid_meta = payload.get("grid") or {}
    total_cols = int(grid_meta.get("cols") or grid_meta.get("world_n") or 40)
    total_rows = int(grid_meta.get("rows") or grid_meta.get("world_n") or 40)

    total_cols = max(1, total_cols)
    total_rows = max(1, total_rows)

    columns = [_col_label(i) for i in range(total_cols)]
    grid_cells = _sys_radar_cell_contacts(contacts, rows=total_rows, cols=total_cols)
    row_width = max(1, len(str(total_rows - 1)))

    friendly_total = sum(1 for c in contacts if str(c.get("allegiance") or c.get("type") or "").lower() == "friendly")
    hostile_total = sum(1 for c in contacts if str(c.get("allegiance") or c.get("type") or "").lower() == "hostile")

    rows: List[Row] = []
    for row_idx, row_cells in enumerate(grid_cells):
        cells: List[Cell] = []
        for cell_contacts in row_cells:
            summary = _sys_radar_cell_summary(cell_contacts)
            cells.append(summary)
        rows.append(
            {
                "label": str(row_idx).zfill(row_width),
                "cells": cells,
            }
        )

    contact_rows = [_format_contact(c) for c in contacts]
    contact_rows.sort(key=lambda row: (row["cell"], row["allegiance"] != "Hostile", row["name"]))

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return {
        "columns": columns,
        "rows": rows,
        "friendly_total": friendly_total,
        "hostile_total": hostile_total,
        "contact_rows": contact_rows,
        "generated": generated,
    }


__all__ = ["build_radar_view"]
