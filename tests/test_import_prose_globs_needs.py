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
    "- [ ] **OPIK.11** Needs the shutdown drain (OPIK.3) first.\n"
    "- [ ] **OPIK.12** Depends on OPIK.10 for the schema.\n"
    "- [ ] **OPIK.13** Reworks the importer. Requires no new config. Related: see OPIK.3.\n"
    "- [ ] **OPIK.14** Run `python ddflow/cli.py import` then read `ddflow/services/importer.py:1219,1580`"
    " and `docs/a.md:3-9`, which cut every title at a hundred and twenty characters in the middle of `a path`"
    " so the queue could not be read from its own titles.\n"
    "- [ ] **OPIK.15** Depends on: OPIK.10 for the schema. Needs OPIK.11.\n"
    "- [ ] **OPIK.16** This no longer requires OPIK.3 to land.\n"
    "- [ ] **OPIK.17** Requires no new config, unlike OPIK.3.\n"
    "- [ ] **OPIK.18** Blocked by OPIK.10 until the schema lands.\n"
    "  **Needs:** OPIK.3\n"
    "- [ ] **OPIK.19** The parser is not finished, needs OPIK.3 for the schema; `and/or` too.\n"
    "- [ ] **OPIK.20** Needs not only OPIK.3 but OPIK.10; it doesn't require OPIK.9.\n"
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


def test_each_dependency_phrase_is_read_on_its_own(repo):
    t = _found(repo)
    assert t["OPIK.11"].needs == ["OPIK.3"]
    assert t["OPIK.12"].needs == ["OPIK.10"]


def test_an_id_in_a_later_sentence_is_not_a_need(repo):
    assert _found(repo)["OPIK.13"].needs == []


def test_line_cited_paths_glob_the_file_and_commands_are_skipped(repo):
    t = _found(repo)["OPIK.14"]
    assert t.globs == ["ddflow/services/importer.py", "docs/a.md"]


def test_a_long_bullet_keeps_a_whole_title_and_its_full_text(repo):
    t = _found(repo)["OPIK.14"]
    assert t.title.endswith("\u2026") and len(t.title) <= 120
    assert t.title.count("`") % 2 == 0
    assert "read from its own titles." in t.body


def test_colon_form_and_two_phrases_on_one_line(repo):
    assert _found(repo)["OPIK.15"].needs == ["OPIK.10", "OPIK.11"]


def test_negated_prose_is_not_a_need(repo):
    t = _found(repo)
    assert t["OPIK.16"].needs == [] and t["OPIK.17"].needs == []


def test_blocked_by_and_a_declared_needs_line_are_unioned(repo):
    assert _found(repo)["OPIK.18"].needs == ["OPIK.3", "OPIK.10"]


def test_an_unrelated_negation_does_not_hide_a_need_and_prose_slashes_are_no_glob(repo):
    t = _found(repo)
    assert t["OPIK.19"].needs == ["OPIK.3"] and t["OPIK.19"].globs == []
    assert t["OPIK.20"].needs == ["OPIK.3", "OPIK.10"]
