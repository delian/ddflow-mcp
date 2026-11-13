"""The four shipped role packs (CO-role-packs): every `rule:<id>` line names a live rule,
a pack obeys the budget, and `brief --role phase-closer` covers the closer's checklist."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

ROLES = ("phase-closer", "fixer", "researcher", "reviewer")

#: The checklist a coordinator otherwise retypes, each keyword the closer's brief must carry.
_CLOSER_CHECKLIST = (
    "Test first",
    "ratchet offender",
    "detached suite",
    "Relay approvals",  # relay approvals
    "docs wave",
    "operator says so",  # no push
)


def test_every_rule_line_names_a_live_rule(repo):
    for role in ROLES:
        code, out, err = run_cli(repo, "brief", "--role", role)
        assert code == 0, f"{role}: {err}"
        assert "[no such rule" not in out, f"{role}: {out}"


def test_a_pack_stays_under_the_budget(repo):
    for role in ROLES:
        code, out, err = run_cli(repo, "brief", "--role", role)
        assert code == 0, f"{role}: {err}"
        assert "(more rules cut to the budget)" not in out, f"{role}: {out}"


def test_brief_role_phase_closer_covers_the_checklist(repo):
    code, out, err = run_cli(repo, "brief", "--role", "phase-closer")
    assert code == 0, err
    for keyword in _CLOSER_CHECKLIST:
        assert keyword in out, f"phase-closer brief misses {keyword!r}:\n{out}"


def test_every_role_has_a_package(repo):
    for role in ROLES:
        code, out, err = run_cli(repo, "brief", "--role", role)
        assert code == 0, err
        assert out.startswith(f"# Role: {role}"), (role, out)
