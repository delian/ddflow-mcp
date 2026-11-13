"""The four shipped role packs (CO-role-packs): every `rule:<id>` line names a live rule,
a pack obeys the budget, and `brief --role phase-closer` covers the closer's checklist."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core import defs as D
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import brief_roles as BR

ROLES = ("phase-closer", "fixer", "researcher", "reviewer")
PROJECT = Path(__file__).resolve().parents[1]

#: The checklist a coordinator otherwise retypes, each keyword the closer's brief must carry.
_CLOSER_CHECKLIST = (
    "Tests first",
    "ratchet",
    "detached suite",
    "Relay approvals",  # relay approvals
    "docs wave",
    "never push",  # no push
)


def test_every_rule_line_names_a_live_rule():
    # Read the packs directly and resolve every `rule:<id>` against the project's rule
    # records, so a pack line can never name a missing or superseded rule.
    st = fold(EventLog(PROJECT).read_all(), strict=False)
    for role in ROLES:
        for line in BR.packaged(role):
            if line.startswith(BR.RULE_PREFIX):
                key = D.key("rule", line[len(BR.RULE_PREFIX) :].strip())
                rec = st.defs.get(key)
                assert rec is not None and rec.live, f"{role}: {line}"


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
