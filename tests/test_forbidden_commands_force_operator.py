"""A refused `ddflow ... --force` names how a person approves THAT call (B-forbidden-force-blocks-operator-authorised-complete).

The guard keeps agents from forcing past ddflow's own refusals, and the operator does
sometimes authorise exactly one (completing a stale docs-only research item). The refusal said
"no force flags: fix what the tool refused", which leaves an authorised override with no route
but editing config. It now names the per-call one: the operator runs the exact line from their
own terminal, which the hook does not guard.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.services import forbidden as FB


def _check(cmd: str):
    return FB.check(cmd, Config(), env={}, cwd="/nonexistent")


def test_a_refused_ddflow_force_names_the_operators_own_terminal():
    r = _check('ddflow complete RM-x --force --reason "operator authorised"')
    assert r is not None and r.entry == "--force"
    assert "own terminal" in r.instead and "DDFLOW_HOOKS_ALLOW" in r.instead, r.instead


def test_a_launcher_does_not_change_the_route():
    r = _check("timeout 100 python -m ddflow --agent a complete RM-x --force")
    assert r is not None and "own terminal" in r.instead, r


def test_a_non_ddflow_force_keeps_the_plain_advice():
    r = _check("git branch -D x --force")
    assert r is not None and "own terminal" not in r.instead, r.instead
