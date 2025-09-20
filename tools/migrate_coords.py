#!/usr/bin/env python3
from __future__ import annotations

"""Migrate legacy grid labels to AA00 format.

Legacy accepted forms:
- 1–2 letters + 1–3 digits, case-insensitive (e.g., K13, A01, Z9, AA12)
Assumptions:
- Single-letter columns use A..Z with A=0..Z=25 → becomes 'A' + letter (e.g., K → 'AK')
- Rows in legacy are 1-based; convert to zero-based with zero-padding.

Usage:
  tools/migrate_coords.py --path projects/falklandV2/state --dry-run
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Tuple

# Ensure 'projects' is importable when run as a script
HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from projects.falklandV2.grid.coords import format_coord, index_to_col  # type: ignore
from projects.falklandV2.grid.config import ROW_WIDTH  # type: ignore


LEGACY_RE = re.compile(r"\b([A-Za-z]{1,2})(\d{1,3})\b")


def legacy_to_indices(label: str) -> Tuple[int, int]:
    m = LEGACY_RE.fullmatch(label)
    if not m:
        raise ValueError(f"not a legacy label: {label}")
    col_raw = m.group(1).upper()
    row_raw = m.group(2)
    # Column: 1 or 2 letters
    if len(col_raw) == 1:
        col_index = ord(col_raw) - ord('A')
        if not (0 <= col_index < 26):
            raise ValueError(f"bad col: {label}")
        # Promote to two-letter by prefixing 'A' band
        col2 = 'A' + col_raw
        col_index = (ord('A') - ord('A')) * 26 + (ord(col_raw) - ord('A'))
    else:
        col_index = (ord(col_raw[0]) - ord('A')) * 26 + (ord(col_raw[1]) - ord('A'))
    # Row: assume 1-based legacy
    row_index = int(row_raw) - 1
    if row_index < 0:
        row_index = 0
    return col_index, row_index


def convert_text(text: str) -> Tuple[str, int]:
    count = 0
    def _repl(m: re.Match[str]) -> str:
        nonlocal count
        legacy = m.group(0)
        try:
            c, r = legacy_to_indices(legacy)
            new = format_coord(c, r)
            count += 1
            return new
        except Exception:
            return legacy
    return LEGACY_RE.sub(_repl, text), count


def process_path(path: Path, *, dry_run: bool) -> Tuple[int, int]:
    files = 0
    replacements = 0
    for p in path.rglob('*'):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ('.json', '.csv', '.txt', '.log'):
            continue
        try:
            txt = p.read_text(encoding='utf-8')
        except Exception:
            continue
        new_txt, n = convert_text(txt)
        if n > 0:
            files += 1
            replacements += n
            if not dry_run:
                bak = p.with_suffix(p.suffix + '.bak')
                try:
                    if not bak.exists():
                        bak.write_text(txt, encoding='utf-8')
                except Exception:
                    pass
                p.write_text(new_txt, encoding='utf-8')
    return files, replacements


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--path', type=str, default='.')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"ERR: path not found: {root}", file=sys.stderr)
        return 2
    files, repl = process_path(root, dry_run=bool(args.dry_run))
    mode = 'DRY-RUN' if args.dry_run else 'WROTE'
    print(f"{mode}: updated_files={files} replacements={repl} row_width={ROW_WIDTH}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
