"""`next --phase <id>` returns the phase's ready upstream dependencies, labelled.

`next --phase P-tail` walked only P-tail's own tasks, so a phase at the end of a
dependency chain showed '0 ready / N blocked' with no way to see, from the same command,
which upstream dependency tasks were themselves ready to
claim (B-next-phase-hides-blocking-deps).
By default those ready blocking-dependency items are now included and labelled, and
``--phase-only`` restores the in-phase-only slice.
"""

from __future__ import annotations

import json

from conftest import run_cli


def _chained(repo):
    """P-down needs P-up, so P-down's only task is blocked while P-up's task is ready."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P-up", "--title", "upstream", "--globs", "up/**")
    run_cli(
        repo,
        "phase",
        "add",
        "P-down",
        "--title",
        "downstream",
        "--needs",
        "P-up",
        "--globs",
        "down/**",
    )
    run_cli(
        repo,
        "task",
        "add",
        "P-up.T1",
        "--title",
        "up task",
        "--phase",
        "P-up",
        "--globs",
        "up/a.py",
    )
    run_cli(
        repo,
        "task",
        "add",
        "P-down.T1",
        "--title",
        "down task",
        "--phase",
        "P-down",
        "--globs",
        "down/a.py",
    )


def _plan(repo, *argv):
    code, out, err = run_cli(repo, "--json", "next", *argv)
    assert code in (0, 2), f"exit {code}\n{out}\n{err}"
    return json.loads(out)


def test_blocking_dependency_is_included_and_labelled_by_default(repo):
    _chained(repo)
    plan = _plan(repo, "--phase", "P-down")
    ready = [r["id"] for r in plan["ready"]]
    assert "P-down.T1" not in ready, "the in-phase task is still blocked on P-up"
    deps = plan["blocking_deps"]
    dep_ids = [r["id"] for r in deps]
    assert "P-up.T1" in dep_ids, f"the ready upstream task must be surfaced: {plan!r}"
    assert all(r["blocking_dependency"] is True for r in deps), deps
    # Distinct from in-phase items: the upstream task is not in the in-phase `ready` list.
    assert "P-up.T1" not in ready


def test_phase_only_hides_the_blocking_dependency(repo):
    _chained(repo)
    plan = _plan(repo, "--phase", "P-down", "--phase-only")
    assert plan["ready"] == [], plan
    assert plan["blocking_deps"] == [], plan


def test_a_blocking_dependency_alone_is_actionable_exit_zero(repo):
    _chained(repo)
    code, _out, _err = run_cli(repo, "next", "--phase", "P-down")
    assert code == 0, "a phase with only ready blockers is still actionable (start the blockers)"


def test_human_text_labels_the_blocking_dependency(repo):
    _chained(repo)
    code, out, _err = run_cli(repo, "next", "--phase", "P-down")
    assert code == 0, out
    assert "blocking-dependency" in out, out


def test_a_phase_with_no_dependencies_has_no_blocking_deps(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "Solo", "--title", "solo")
    run_cli(
        repo,
        "task",
        "add",
        "Solo.T1",
        "--title",
        "solo task",
        "--phase",
        "Solo",
        "--globs",
        "solo.py",
    )
    plan = _plan(repo, "--phase", "Solo")
    assert [r["id"] for r in plan["ready"]] == ["Solo.T1"], plan
    assert plan["blocking_deps"] == [], plan
