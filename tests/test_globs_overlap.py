"""`overlap` never says two patterns are disjoint when a file matches both (Bebc6fe14a1).

A class table (one row per kind of wildcard, in either position, both argument orders) and a
soundness check against a small universe of real paths: `overlap` may be generous (it decides
whether to re-order claims) but a path that BOTH patterns match must make it True.
"""

from __future__ import annotations

import itertools

import pytest

from ddflow.core import globs

#: (a, b, a path both match): every row must overlap, in either order
SHARED = [
    ("src/[ab]x.py", "src/ax*", "src/ax.py"),
    ("src/[!c]x.py", "src/ax*", "src/ax.py"),
    ("src/[^c]x.py", "src/b*", "src/bx.py"),
    ("src/[a-c]x.py", "src/c?.py", "src/cx.py"),
    ("src/[]]x.py", "src/]*", "src/]x.py"),
    ("src/?x.py", "src/ax*", "src/ax.py"),
    ("src/*x.py", "src/ax*", "src/ax.py"),
    ("src/a[0-9].py", "src/a1*", "src/a1.py"),
    ("[ab]/x.py", "a/*", "a/x.py"),
    ("docs/[ab].md", "docs/*.md", "docs/a.md"),
    ("src/**/*.py", "src/a/b.py", "src/a/b.py"),
    ("src/a[bc]*", "src/ab?", "src/abz"),
]

#: patterns whose literal parts differ before any wildcard: disjoint (`overlap` is otherwise
#: generous by design, so `src/[ab]x.py` and `src/cx*` still read as overlapping)
DISJOINT = [
    ("src/a[bc]x.py", "lib/a*"),
    ("docs/[ab].md", "src/*.md"),
    ("docs/*.md", "src/*.md"),
    ("src/a?.py", "lib/a?.py"),
]


@pytest.mark.parametrize(("a", "b", "path"), SHARED)
def test_patterns_that_share_a_file_overlap_in_either_order(a, b, path):
    assert globs.match(path, a) and globs.match(path, b), "the row's own path must match both"
    assert globs.overlap(a, b)
    assert globs.overlap(b, a)


@pytest.mark.parametrize(("a", "b"), DISJOINT)
def test_patterns_that_share_nothing_are_disjoint_in_either_order(a, b):
    assert not globs.overlap(a, b)
    assert not globs.overlap(b, a)


_PATTERNS = sorted({p for row in SHARED for p in row[:2]} | {p for row in DISJOINT for p in row})
_PATHS = [
    "src/ax.py", "src/bx.py", "src/cx.py", "src/c1.py", "src/a1.py", "src/abz", "src/acz",
    "src/]x.py", "src/a/b.py", "docs/a.md", "docs/b.md", "src/a.md", "lib/a1.py", "a/x.py",
    "b/x.py", "src/b.py",
]  # fmt: skip


def test_overlap_is_never_false_when_a_real_path_matches_both():
    missed = [
        (a, b, p)
        for a, b in itertools.product(_PATTERNS, repeat=2)  # both argument orders
        for p in _PATHS
        if globs.match(p, a) and globs.match(p, b) and not globs.overlap(a, b)
    ]
    assert not missed, missed
