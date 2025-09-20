from __future__ import annotations

import random

from projects.falklandV2.grid.coords import (
    col_to_index,
    index_to_col,
    format_coord,
    parse_coord,
    center_subboard,
    center_subboard_labels,
)


def test_col_roundtrip_first_80():
    samples = [
        (0, 'AA'), (1, 'AB'), (25, 'AZ'), (26, 'BA'), (51, 'BZ'), (52, 'CA'),
    ]
    for idx, label in samples:
        assert index_to_col(idx) == label
        assert col_to_index(label) == idx
    for i in range(80):
        lbl = index_to_col(i)
        assert col_to_index(lbl) == i


def test_parse_format_roundtrip_corners_and_random():
    corners = [(0,0), (39,0), (0,39), (39,39)]
    for c,r in corners:
        s = format_coord(c,r)
        c2,r2 = parse_coord(s)
        assert (c,r) == (c2,r2)
    random.seed(0)
    for _ in range(100):
        c = random.randint(0,39)
        r = random.randint(0,39)
        s = format_coord(c,r)
        c2,r2 = parse_coord(s)
        assert (c,r) == (c2,r2)


def test_strict_parse_rejects():
    bad = ['aA00','AA 00','A00','AAA00','AA0','AA-1']
    for s in bad:
        try:
            parse_coord(s)
        except Exception:
            pass
        else:
            raise AssertionError(f"should reject: {s}")


def test_center_subboard_30_in_40():
    (tlc, tlr), (brc, brr) = center_subboard(40,40,30,30)
    assert (tlc,tlr)==(5,5)
    assert (brc,brr)==(34,34)
    tl, br = center_subboard_labels(40,40,30,30)
    assert tl == 'AF05'
    assert br == 'BI34'

