"""Import reads what a task's prose already says: backticked repo paths become globs and
'Requires/Depends on/Needs <id>' becomes a proposed need (imp-titles-truncated-no-text)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import importer as IM

TODO = (
    "## OPIK follow-ups\n\n"
    "- [ ] **OPIK.3** Add the retrieval-miss stratum in `src/opik/strata.py` and `tests/opik/`.\n"
    "- [ ] **OPIK.9** Wire the exporter into `ddflow/services/export.py`. Requires OPIK.3 and\n"
    "  Needs the stratum (OPIK.3); Depends on NOPE.7 which nobody wrote. Run `pytest -q`.\n"
    "- [ ] **OPIK.10** Declared wins. `src/ignored.py`\n"
    "  **Globs:** src/declared/**\n"
)


def _found(repo: Path) -> dict:
    (repo / "docs").mkdir()
    (repo / "docs" / "todo.md").write_text(TODO)
    plan = IM.plan_import(repo, fold(EventLog(repo).read_all()))
    return {f.ident: f for f in plan.found if f.kind == "task"}


def test_backticked_repo_paths_become_globs_commands_do_not(repo):
    t = _found(repo)
    assert t["OPIK.3"].globs == ["src/opik/strata.py", "tests/opik/"]
    assert t["OPIK.9"].globs == ["ddflow/services/export.py"]


def test_a_declared_globs_line_overrides_prose_paths(repo):
    assert _found(repo)["OPIK.10"].globs == ["src/declared/**"]


def test_prose_requires_becomes_a_need_only_for_a_known_id(repo):
    t = _found(repo)
    assert t["OPIK.9"].needs == ["OPIK.3"]
    assert t["OPIK.3"].needs == []
