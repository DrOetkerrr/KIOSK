#!/usr/bin/env python3
import re, subprocess

def extract_routes(rev: str):
    try:
        text = subprocess.check_output([
            'git','show', f'{rev}:projects/FalklandV2/webdash.py'
        ], text=True)
    except Exception:
        return []
    out = []
    pat = re.compile(r'@app\.(route|get|post)\(\s*([\'\"])([^\'\"]+)\2([^)]*)\)')
    for m in pat.finditer(text):
        kind = m.group(1)
        path = m.group(3)
        extra = m.group(4)
        methods = None
        mm = re.search(r'methods\s*=\s*\[([^\]]*)\]', extra)
        if mm:
            methods = ','.join(sorted([s.strip().strip("'\"") for s in mm.group(1).split(',') if s.strip()]))
        else:
            methods = 'POST' if kind=='post' else 'GET'
        out.append((path, methods))
    return sorted(set(out))

base = 'FV2_BASELINE_20250908_115257'
head = 'main'
br = extract_routes(base)
hr = extract_routes(head)
bs, hs = set(br), set(hr)
added = sorted(hs - bs)
removed = sorted(bs - hs)
print(f'Base routes: {len(br)}')
print(f'Head routes: {len(hr)}')
print(f'Added: {len(added)}')
for p in added[:40]:
    print('+', p)
print(f'Removed: {len(removed)}')
for p in removed[:40]:
    print('-', p)

