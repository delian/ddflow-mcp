"""RA-inventory: the gated-command table is complete (a refusal site cannot appear unlisted)."""

from __future__ import annotations

import re
from pathlib import Path

from ddflow.services.approvals import sites as S

ROOT = Path(__file__).resolve().parents[1]
#: a CALL, not a definition or a mention in prose
CALL = re.compile(
    r"(?<![\w.])(?:\w+\.)*(?:agent_marker|is_agent|_is_agent|reviewer_agent_marker)\("
)
DEF = re.compile(r"^\s*def ")


def _callers(base: Path = ROOT) -> set[str]:
    found: set[str] = set()
    for path in (base / "ddflow").rglob("*.py"):
        for line in path.read_text().splitlines():
            code = line.split("#", 1)[0]
            if CALL.search(code) and not DEF.match(code):
                found.add(path.relative_to(base).as_posix())
                break
    return found


def test_every_module_that_checks_for_an_agent_is_named_by_a_row():
    unlisted = _callers() - S.marker_modules() - set(S.INFRASTRUCTURE)
    assert not unlisted, (
        f"{sorted(unlisted)} call agent_marker/is_agent: add a Site row for the verb in "
        "ddflow/services/approvals/sites.py (tier, minimum level, what is digested)"
    )


def test_every_listed_module_really_checks_and_exists():
    callers = _callers()
    for s in S.SITES:
        for m in s.modules:
            assert (ROOT / m).is_file(), f"{s.verb}: {m} does not exist"
            assert m in callers, f"{s.verb}: {m} no longer calls agent_marker/is_agent"
    for m in S.INFRASTRUCTURE:
        assert (ROOT / m).is_file(), m


def test_a_new_refusal_site_would_be_caught(tmp_path):
    pkg = tmp_path / "ddflow" / "services"
    pkg.mkdir(parents=True)
    (pkg / "newgate.py").write_text(
        "def f(a):\n    if why := agent_marker(a):\n        return why\n"
    )
    (pkg / "quiet.py").write_text("def agent_marker(x):\n    return x  # agent_marker( in prose\n")
    found = _callers(tmp_path)
    assert found == {"ddflow/services/newgate.py"}
    assert found - S.marker_modules() - set(S.INFRASTRUCTURE) == found


def test_rows_are_well_formed_and_the_amended_acts_are_listed():
    verbs = [s.verb for s in S.SITES]
    assert len(verbs) == len(set(verbs))
    for s in S.SITES:
        assert s.tier in S.TIERS and s.family in S.FAMILIES, s.verb
        assert s.min_level in (S.L1, S.L2, S.L3, S.L4), s.verb
        assert s.digest and s.command, s.verb
        # D-RA-Q6: T3 is signed only, T2 never needs more than a person channel
        assert (s.tier == S.T3) == (s.min_level == S.L4) or s.tier == S.T1, s.verb
    for required in ("gate.skip.failed_review", "grant.add", "claim.force", "complete.force"):
        assert S.site(required).tier == S.T2
