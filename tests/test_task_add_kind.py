"""`task add --kind doc|research|job` makes an item of that kind, which runs the pipeline its
kind names (`[gates].kind_pipelines`), so docs-only work completes without the code gates
(bug doc-kind-cannot-be-created)."""

from __future__ import annotations

import json

from conftest import run_cli
from helpers import items_of as _items


def _doc(repo, rid="D1", *extra):
    run_cli(repo, "init")
    return run_cli(repo, "task", "add", rid, "--globs", "docs/x.md", "--kind", "doc", *extra)


def test_task_add_kind_doc_makes_a_doc_item(repo):
    code, _o, err = _doc(repo)
    assert code == 0, err
    assert _items(repo)["D1"].kind == "doc"


def test_a_plain_task_stays_a_task(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == 0
    assert _items(repo)["T1"].kind == "task"


def test_phase_and_unknown_kinds_are_refused(repo):
    run_cli(repo, "init")
    for bad in ("phase", "nonsense", "bug"):
        code, _o, err = run_cli(repo, "task", "add", f"X{bad}", "--kind", bad)
        assert code != 0 and "kind" in err, (bad, err)
    assert "Xphase" not in _items(repo)


def test_the_doc_pipeline_has_no_unit_tests_or_critic_gate(repo):
    assert _doc(repo)[0] == 0
    code, out, err = run_cli(repo, "--json", "show", "D1")
    assert code == 0, err
    code, out, err = run_cli(repo, "gate", "status", "D1")
    assert code == 0, err
    assert "implement" in out and "merge" in out
    assert "unit_tests" not in out and "critic" not in out


def test_a_doc_item_is_offered_and_claimable(repo):
    assert _doc(repo)[0] == 0
    code, out, err = run_cli(repo, "next")
    assert code == 0 and "D1" in out, (out, err)
    assert run_cli(repo, "claim", "D1", "--no-worktree")[0] == 0


def test_a_doc_item_completes_without_the_code_gates(repo):
    assert _doc(repo)[0] == 0
    assert run_cli(repo, "claim", "D1", "--no-worktree")[0] == 0
    for g in ("rules", "implement", "standards", "dedupe", "merge"):
        assert (
            run_cli(
                repo, "gate", "record", "D1", g, "--outcome", "passed", "--evidence", "docs only"
            )[0]
            == 0
        )
    code, out, err = run_cli(repo, "complete", "D1")
    assert code == 0, (out, err)
    assert _items(repo)["D1"].state == "done"


def test_work_kinds_are_registered_kinds():
    from ddflow.config_sections._kinds import KINDS
    from ddflow.core.records import WORK_KINDS

    assert set(WORK_KINDS) <= set(KINDS)
