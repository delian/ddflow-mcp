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


def test_an_acceptance_written_in_the_BODY_counts():
    """The tasks filed in this queue state theirs as an `ACCEPTANCE:` line in the body, so a
    plan read back from the queue must not be told it says nothing about being done."""
    assert PC.says_acceptance({"body": "ACCEPTANCE: the 11 tasks pass"})
    assert PC.says_acceptance({"body": "work\n\nAcceptance: it renders"})
    assert PC.says_acceptance({"acceptance": "a field"})
    assert not PC.says_acceptance({"body": "no word for it here"})
    assert not PC.says_acceptance({"acceptance": "  ", "body": "still nothing"})

    plan = {"a": {"kind": "task", "needs": [], "globs": ["src/a.py"], "body": "ACCEPTANCE: ok"}}
    assert PC.check(plan).findings == []


# -- the command that runs them (B-gap-plan-lint's surface) -----------------------------


def _plan_file(repo, tmp_path, body: str):
    """The command, run in a real repository (the CLI bootstraps before the command runs;
    the plan itself lives in `tmp_path`, which is all `plan check` reads)."""
    from conftest import run_cli

    path = tmp_path / "plan.toml"
    path.write_text(body, "utf-8")
    return run_cli(repo, "plan", "check", str(path))


def test_the_command_reports_a_finding_and_fails(repo, tmp_path):
    """Findings ARE the point, so they are a failure -- and the human line names the kind."""
    code, out, err = _plan_file(repo, tmp_path, '[a]\nglobs = ["src/*/x.py"]\nacceptance = "ok"\n')

    assert code == 1, (out, err)
    assert "wide_glob" in out and "a" in out


def test_the_command_is_clean_and_shows_the_waves(repo, tmp_path):
    code, out, err = _plan_file(
        repo,
        tmp_path,
        '[a]\nglobs = ["src/a.py"]\nbody = "ACCEPTANCE: ok"\n'
        '[b]\nglobs = ["src/b.py"]\nneeds = ["a"]\nbody = "acceptance: ok"\n',
    )

    assert code == 0, (out, err)
    assert "no findings" in out and "wave 2: b" in out


def test_a_document_that_is_not_a_plan_is_refused_not_called_clean(repo, tmp_path):
    """ "Could not tell" is never a clean plan: unparseable and item-less are both refusals."""
    code, out, err = _plan_file(repo, tmp_path, 'title = "a document that is not a plan"\n')
    assert code == 3, (out, err)
    assert "no items" in (out + err)

    code, out, err = _plan_file(repo, tmp_path, "this is not TOML or JSON at all\n")
    assert code == 3, (out, err)
    assert "neither TOML nor JSON" in (out + err)


def test_a_needs_naming_a_queued_item_is_not_unknown_when_the_queue_is_given():
    """A plan filed into a queue depends on what is already there (`known`)."""
    plan = {"a": _task(needs=["queued"], globs=["src/a.py"])}

    assert _kinds(PC.check(plan)) == ["unknown_dep"]
    assert PC.check(plan, known=["queued"]).findings == []
