"""`services.plancheck`: the mechanical checks a plan passes BEFORE anything is filed.

A table of bad plans, each failing exactly ONE check: a plan wrong in five ways has to be
reported as wrong in five ways, and a test that accepted "some finding" would not notice
the day one check quietly stopped running.
"""

from __future__ import annotations

from ddflow.services import plancheck as PC


def _task(needs=(), globs=(), acceptance="it works", **kw) -> dict:
    return {
        "kind": "task",
        "needs": list(needs),
        "globs": list(globs),
        "acceptance": acceptance,
        **kw,
    }


def _kinds(report: PC.Report) -> list[str]:
    return sorted(f.kind for f in report.findings)


def test_a_good_plan_has_no_findings_and_its_waves():
    plan = {
        "a": _task(globs=["src/a.py"]),
        "b": _task(needs=["a"], globs=["src/b.py"]),
        "c": _task(needs=["a"], globs=["src/c.py"]),
    }

    report = PC.check(plan)

    assert report.findings == []
    assert report.waves == {"a": 1, "b": 2, "c": 2}


def test_a_wildcard_in_a_DIRECTORY_position_is_reported():
    """`src/*/cli.py` names a family of files, so no other task can be checked against it."""
    assert _kinds(PC.check({"a": _task(globs=["src/*/cli.py"])})) == ["wide_glob"]

    # A bare `*` is a directory wildcard too: it is the whole of it.
    got = PC.check({"b": _task(globs=["*"])})
    assert [f.ids for f in got.findings] == [("b",)]
    assert "directory position" in got.findings[0].detail


def test_a_wildcard_in_the_FILENAME_is_fine():
    """`src/a*.py` is a set of files the overlap test can still compare."""
    assert PC.check({"a": _task(globs=["src/a*.py", "*.md"])}).findings == []


def test_a_needs_naming_nothing_is_reported():
    plan = {"a": _task(needs=["ghost"], globs=["src/a.py"])}

    (finding,) = PC.check(plan).findings

    assert finding.kind == "unknown_dep" and finding.ids == ("a", "ghost")


def test_a_cycle_is_reported_once_and_gets_no_wave():
    plan = {
        "a": _task(needs=["b"], globs=["src/a.py"]),
        "b": _task(needs=["a"], globs=["src/b.py"]),
    }

    got = PC.check(plan)

    assert _kinds(got) == ["cycle"]
    assert got.waves == {}, "nothing on a cycle can start"


def test_two_tasks_that_can_run_together_may_not_overlap():
    plan = {"a": _task(globs=["docs/**"]), "b": _task(globs=["docs/guide.md"])}

    (finding,) = PC.check(plan).findings

    assert finding.kind == "overlap" and finding.ids == ("a", "b")
    assert "docs/**" in finding.detail and "docs/guide.md" in finding.detail


def test_a_dependency_MAKES_an_overlap_legal():
    """The order is real, not a coincidence to re-order: `b` waits for `a`."""
    plan = {"a": _task(globs=["docs/**"]), "b": _task(needs=["a"], globs=["docs/guide.md"])}

    got = PC.check(plan)

    assert got.findings == []
    assert got.waves == {"a": 1, "b": 2}


def test_a_task_without_acceptance_is_reported_and_a_phase_is_not():
    plan = {"a": _task(acceptance=""), "p": {"kind": "phase"}}

    (finding,) = PC.check(plan).findings

    assert finding.kind == "no_acceptance" and finding.ids == ("a",)


def test_globs_spanning_many_top_level_directories_are_reported():
    four = {"a": _task(globs=["src/a.py", "docs/a.md", "tests/a.py", "scripts/a"])}

    (finding,) = PC.check(four).findings

    assert finding.kind == "wide_span" and "4 top-level" in finding.detail
    three = {"a": _task(globs=["src/a", "docs/a", "tests/a"])}
    assert PC.check(three).findings == [], "three is still one task"


def test_the_injected_checks_are_used_when_given_and_skipped_when_not():
    """`tracked` and `similar` are how this stays pure: no repository, no log, no clock."""
    plan = {"a": _task(globs=["src/a.py"])}
    assert PC.check(plan).findings == []  # neither given: neither check runs

    dup = PC.check(plan, similar=lambda title, body: ["T9"])
    assert [f.kind for f in dup.findings] == ["duplicate"]
    assert dup.findings[0].ids == ("a", "T9")

    wide = PC.check({"a": _task(globs=["src/f*.py"])}, tracked=[f"src/f{i}.py" for i in range(60)])
    assert [f.kind for f in wide.findings] == ["wide_glob"]
    assert "60 tracked file" in wide.findings[0].detail


def test_the_report_orders_the_waves_by_wave_then_id():
    plan = {"b": _task(globs=["src/b.py"]), "a": _task(globs=["src/a.py"])}

    assert list(PC.check(plan).as_dict()["waves"]) == ["a", "b"]
