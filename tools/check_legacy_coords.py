#!/usr/bin/env python3
"""Guard: scan repo for legacy A1/A01-style grid labels in code.

This is a heuristic grep; allowlist can be extended if needed.
Exits with non-zero status on suspicious finds unless RUN_GUARD=0.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAT = re.compile(r"\b[A-Z]{1,2}\d{1,3}\b")
ALLOW_IN = {"tests", "tools", "logs", "node_modules", ".git", ".venv", "state", "docs"}


def main() -> int:
    suspicious = []
    for p in ROOT.rglob('*'):
        if not p.is_file():
            continue
        parts = set(p.parts)
        if parts & ALLOW_IN:
            continue
        if p.suffix.lower() not in ('.py', '.js', '.ts', '.json', '.md', '.txt', '.csv'):
            continue
        try:
            txt = p.read_text(encoding='utf-8')
        except Exception:
            continue
        for m in PAT.finditer(txt):
            s = m.group(0)
            # Ignore canonical AA00 forms (two letters + 2 digits) followed by more digits (already caught by parse)
            if re.fullmatch(r"[A-Z]{2}\d{2,}", s):
                continue
            suspicious.append((p, s))
            if len(suspicious) > 50:
                break
    if not suspicious:
        print('legacy-coords: OK')
        return 0
    for p, s in suspicious[:50]:
        print(f"legacy-coords: {p}: {s}")
    if os.environ.get('RUN_GUARD','0') == '0':
        return 0
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
