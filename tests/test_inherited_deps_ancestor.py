"""An umbrella's need on its own child must not bind that child's sub-tasks (B43447abfc8).

`inherited_deps` dropped an ancestor's need that points into the item's OWN subtree, but
not one that points at an ancestor between the item and the need's owner. A needs C, C
is A's child, D is C's child: D inherited "C is open (from A)", while C, an umbrella,
cannot finish before D does -- a permanent hang.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.core.schedule import inherited_deps
from ddflow.infra.log import EventLog


def _plan(repo) -> dict:
    code, out, err = run_cli(repo, "--json", "next")
    assert code in (0, 2), f"exit {code}\n{out}\n{err}"
    return json.loads(out)


def _tree(repo) -> None:
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "task", "add", "A", "--phase", "P1", "--globs", "core/a.py")
    run_cli(repo, "task", "add", "C", "--parent", "A", "--globs", "core/c.py")
    run_cli(repo, "task", "add", "D", "--parent", "C", "--globs", "core/d.py")
    run_cli(repo, "update", "A", "--needs", "C")


def test_a_grandchild_does_not_inherit_a_need_on_its_own_parent(repo):
    _tree(repo)
    plan = _plan(repo)
    ready = [r["id"] for r in plan["ready"]]
    assert "D" in ready, f"D waits on C, which waits on D: {plan['blocked']}"


def test_the_need_is_still_dropped_only_on_the_path_between(repo):
    """A's need on C still binds work OUTSIDE C's subtree (A's other child E)."""
    _tree(repo)
    run_cli(repo, "task", "add", "E", "--parent", "A", "--globs", "core/e.py")
    st = fold(EventLog(repo, "t").read_all(), strict=False)
    assert ("A", "C") not in inherited_deps(st, st.items["D"])
    assert ("A", "C") in inherited_deps(st, st.items["E"])
    blocked = {b["item"]: b for b in _plan(repo)["blocked"]}
    assert "E" in blocked and "C" in json.dumps(blocked["E"]), blocked
