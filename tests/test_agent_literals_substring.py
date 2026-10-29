"""Report-only counter of string constants that CONTAIN an agent name or directory.

`test_no_agent_literals` is an exact-match ratchet: it sees `"claude"` and not
`"run prompt --harness claude"`. This module counts the second kind (research
driver-architecture 2.4 counted 163 incl. model names; this counter's id-only count is 149). It does not fail on growth, it reports, and a
ceiling (`SUBSTRING_CEILING`) only stops a silent jump; lower it as sites move into descriptors.
The inventory (docs/ddflow/research/driver-inventory.md) is checked against the exact ratchet.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ddflow.services import harnessreg as HR
from tests import test_no_agent_literals as NAL

DOC = Path(NAL.PKG).parent / "docs" / "ddflow" / "research" / "driver-inventory.md"

#: ids shorter than this are not counted as substrings (`pi`, `amp` hit ordinary words)
MIN_ID_LEN = 4
#: the substring total may not jump past this (report-only below it); lower it as sites move
SUBSTRING_CEILING = 160


def substring_sites(source: str, ids: set[str], roots: set[str]) -> list[int]:
    """Lines of string constants that contain an id (any case), or a root, but are not exact sites."""
    tree = ast.parse(source)
    skip = NAL._docstrings(tree)
    words = [i.lower() for i in ids if len(i) >= MIN_ID_LEN]
    found = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)) or id(n) in skip:
            continue
        v = n.value
        if NAL.sites(f"x = {v!r}\n", ids, roots):
            continue  # already counted by the exact ratchet
        if any(w in v.lower() for w in words) or any(r in v for r in roots):
            found.append(n.lineno)
    return found


def counts() -> dict[str, int]:
    ids = {h.id for h in HR.load()}
    roots = NAL._roots()
    out = {}
    for f in sorted(NAL.PKG.rglob("*.py")):
        if f.relative_to(NAL.PKG).parts[:1] == ("harnesses",):
            continue
        n = len(substring_sites(f.read_text("utf-8"), ids, roots))
        if n:
            out[f.relative_to(NAL.PKG.parent).as_posix()] = n
    return out


def test_the_substring_detector_can_fail():
    ids, roots = {"claude"}, {".claude/"}
    assert substring_sites('x = "run prompt --harness claude"\n', ids, roots) == [1]
    assert substring_sites('x = "see ~/.claude/x"\n', ids, roots) == []  # the ratchet's (path)
    assert substring_sites('x = "see .claude/ here"\n', ids, roots) == [1]
    assert substring_sites('x = "claude"\n', ids, roots) == []  # exact: the ratchet's
    assert substring_sites('x = "Claude Code"\n', ids, roots) == [1]  # case-insensitive
    assert substring_sites('"""run claude"""\nx = 1\n', ids, roots) == []  # docstring


def test_the_substring_total_is_reported_and_bounded():
    c = counts()
    total = sum(c.values())
    print(f"substring agent-name sites: {total} in {len(c)} files")
    assert 0 < total <= SUBSTRING_CEILING, total


def _rows() -> dict[str, tuple[int, str, str]]:
    """file -> (sites, class, descriptor field) from the inventory's first table."""
    rows = {}
    for line in DOC.read_text("utf-8").splitlines():
        m = re.match(
            r"\|\s*`(ddflow/[^`]+)`\s*\|\s*(\d+)\s*\|\s*([\w-]+)\s*\|\s*([^|]+?)\s*\|", line
        )
        if m:
            rows[m[1]] = (int(m[2]), m[3], m[4])
    return rows


def test_the_inventory_matches_the_ratchet_baseline():
    rows = _rows()
    assert set(rows) == set(NAL.BASELINE), set(rows) ^ set(NAL.BASELINE)
    wrong = {f: (rows[f][0], n) for f, (n, _w) in NAL.BASELINE.items() if rows[f][0] != n}
    assert not wrong, f"inventory count != BASELINE (file: doc, baseline): {wrong}"


def test_every_row_has_a_class_and_a_replacing_field():
    classes = {"data", "compat", "model-axis", "driver"}
    bad = {f: r for f, r in _rows().items() if r[1] not in classes or len(r[2]) < 5}
    assert not bad, bad
    # every data-in-code site names the descriptor field that replaces it
    assert all(r[2] != "-" for f, r in _rows().items() if r[1] == "data")
