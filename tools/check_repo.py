#!/usr/bin/env python3
"""
Lightweight repo checks (safe, non-destructive).

Runs:
- Python AST/bytecode compile on project files
- Route count summary for webdash
- File-size guardrails (warn-only)
"""
from __future__ import annotations
import sys, os, py_compile, re
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]

PY_PATHS = [ROOT / 'projects']

SIZE_THRESHOLDS = {
    '.py': 1000,
    '.html': 800,
    '.js': 600,
}

# Per-file overrides: (max_lines, warn_label)
OVERRIDES_BASENAME: Dict[str, Tuple[int, str]] = {
    # Allow webdash to stay large (warn separately) while we refactor incrementally
    'webdash.py': (6000, 'webdash-large'),
}

def list_files() -> List[Path]:
    out: List[Path] = []
    for base in PY_PATHS:
        for p in base.rglob('*'):
            if p.is_dir():
                # skip virtual envs and git
                if p.name in {'.venv', '.git', '__pycache__'}:
                    # prune
                    if p.is_dir():
                        pass
                continue
            out.append(p)
    return out

def py_compile_all(files: List[Path]) -> List[Tuple[Path, str]]:
    errs: List[Tuple[Path, str]] = []
    for p in files:
        if p.suffix != '.py':
            continue
        try:
            py_compile.compile(str(p), doraise=True)
        except Exception as e:
            errs.append((p, f'{type(e).__name__}: {e}'))
    return errs

def summarize_routes() -> None:
    wd = ROOT / 'projects' / 'FalklandV2' / 'webdash.py'
    if not wd.exists():
        print('routes: webdash.py not found')
        return
    text = wd.read_text(encoding='utf-8', errors='ignore')
    n = len(re.findall(r'@app\.(?:route|get|post)\(', text))
    print(f'routes: webdash.py contains {n} route decorators')

def size_guard(files: List[Path]) -> int:
    warns = 0
    for p in files:
        suf = p.suffix.lower()
        if suf not in SIZE_THRESHOLDS:
            continue
        try:
            lines = sum(1 for _ in p.open('r', encoding='utf-8', errors='ignore'))
        except Exception:
            continue
        max_allowed = SIZE_THRESHOLDS[suf]
        if p.name in OVERRIDES_BASENAME:
            max_allowed, label = OVERRIDES_BASENAME[p.name]
            if lines > SIZE_THRESHOLDS[suf]:
                print(f'WARN: {label}: {p} has {lines} lines (> {SIZE_THRESHOLDS[suf]}). Refactor advisable.')
        if lines > max_allowed:
            print(f'WARN: large-file: {p} has {lines} lines (> {max_allowed})')
            warns += 1
    return warns

def main() -> int:
    files = list_files()
    print(f'files: scanned {len(files)} under projects/')
    errs = py_compile_all(files)
    if errs:
        print('compile: FAIL')
        for p, msg in errs:
            print(f'  {p}: {msg}')
        return 1
    print('compile: OK')
    summarize_routes()
    w = size_guard(files)
    print(f'size-guard: {w} warning(s)')
    return 0

if __name__ == '__main__':
    sys.exit(main())
