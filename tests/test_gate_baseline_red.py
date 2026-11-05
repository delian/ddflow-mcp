"""A ci / unit_tests failure that the base branch shows too is baseline-red (DX-gate-baseline-red).

The failing ids are run again, alone, on the base in a scratch worktree. All of them red there:
the gate is recorded `partial` with `baseline-red: <ids>, bug <id>`, a bug is filed, and
`complete` accepts the coverage gap. One of them green there (the branch's own fault): the
gate stays FAILED.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

ID = "tests/test_x.py::test_a"


def _project(repo: Path, *, base_healthy: bool = False) -> None:
    """A fake pytest that fails ``test_a`` unless the file `healthy` exists. With
    ``base_healthy`` the committed base has it."""
    run_cli(repo, "init")
    if base_healthy:
        (repo / "healthy").write_text("")
    (repo / "bin").mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_a():\n    pass\n")
    script = repo / "bin" / "pytest"
    script.write_text(
        "#!/bin/sh\n"
        'if [ -e "$(dirname "$0")/../healthy" ]; then echo "1 passed in 0.1s"; exit 0; fi\n'
        "printf '____ test_a ____\\nE assert 1 == 2\\n=== short test summary info ===\\n"
        f"FAILED {ID} - assert 1 == 2\\n1 failed in 0.1s\\n'; exit 1\n"
    )
    script.chmod(0o755)
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "./bin/pytest -q tests/"\ncwd = "repo"\n'
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "project"], check=True)
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")


def test_a_failure_the_base_shows_too_is_recorded_partial_baseline_red(repo) -> None:
    _project(repo)  # the base fails test_a: so does the item's tree
    code, out, _e = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    body = json.loads(out)
    assert body["outcome"] == "partial", body
    assert code != 1
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["unit_tests"]
    assert rec.outcome == "partial"
    assert rec.reason.startswith(f"baseline-red: {ID}, bug B"), rec.reason
    assert rec.evidence["baseline"]["red"] and rec.evidence["baseline"]["ids"] == [ID]
    bug = rec.reason.rsplit("bug ", 1)[1]
    assert bug in fold(EventLog(repo).read_all(), strict=False).bugs


def test_a_failure_the_base_does_not_show_stays_failed(repo) -> None:
    _project(repo, base_healthy=True)
    (repo / "healthy").unlink()  # the item's tree breaks what the base has
    code, out, _e = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    body = json.loads(out)
    assert code == 1 and body["outcome"] == "failed", body
    assert body["evidence"]["baseline"]["red"] is False


def test_unattributable_failures_stay_failed(repo) -> None:
    from ddflow.services.gates import baseline_red as BR

    ev = {"failed_tests": [ID], "failed_tests_more": 3}
    assert BR.candidate_ids("unit_tests", "pytest", ev, False) == []
    assert BR.candidate_ids("unit_tests", "pytest -x", {"failed_tests": [ID]}, True) == []
    assert BR.candidate_ids("critic", "pytest", {"failed_tests": [ID]}, False) == []
    assert BR.candidate_ids("ci", "pytest", {"failed_tests": [ID]}, False) == [ID]


def test_no_ids_is_never_baseline_red(repo) -> None:
    from ddflow.services.gates import baseline_red as BR

    assert BR.on_base(repo, "main", None, [], lambda g, c: ("failed", {}))["red"] is False


def _capped_project(repo: Path, n: int) -> None:
    """A fake pytest that fails ``n`` distinct tests (``n > MAX_FAILED_TESTS`` caps the list the
    gate evidence keeps). The base fails them too: a full-suite failure the base shows is
    baseline-red (B69d1c0ff00)."""
    run_cli(repo, "init")
    (repo / "bin").mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text(
        "".join(f"def test_a{i}():\n    pass\n" for i in range(n))
    )
    lines = ["#!/bin/sh"]
    # Fail exactly the tests named on the command line (the base measurement runs a fixed
    # list); with none named (the item's full run) fail all `n` under tests/test_x.py.
    lines.append("named=0")
    lines.append('for a in "$@"; do')
    lines.append(
        '  case "$a" in tests/test_x.py::*) echo "FAILED $a - assert 1 == 2"; named=1;; esac'
    )
    lines.append("done")
    lines.append("if [ $named -eq 0 ]; then")
    lines.append("  i=0")
    lines.append(
        f'  while [ $i -lt {n} ]; do echo "FAILED tests/test_x.py::test_a$i - assert 1 == 2"; i=$((i+1)); done'
    )
    lines.append("fi")
    lines.append('echo "=== short test summary info ==="')
    lines.append(f'echo "{n} failed in 0.1s"')
    lines.append("exit 1")
    script = repo / "bin" / "pytest"
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "./bin/pytest -q tests/"\ncwd = "repo"\n'
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "project"], check=True)
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")


def test_a_cut_full_suite_failure_is_still_measured_on_the_base(repo) -> None:
    """B69d1c0ff00: a full-suite unit_tests failure CAPS its failing ids (checked in
    `test_unattributable_failures_stay_failed`), so the whole list must be kept aside and run on
    the base -- otherwise the gate stays failed with no baseline and `baseline_red_auto` never
    fires."""
    _capped_project(repo, 55)  # 55 > MAX_FAILED_TESTS (50): the evidence list is cut
    _code, out, _e = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    body = json.loads(out)
    assert body["outcome"] == "partial", body
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["unit_tests"]
    assert rec.outcome == "partial"
    assert rec.evidence["baseline"]["red"] is True
    assert len(rec.evidence["baseline"]["ids"]) == 55


# ---- B-baseline-branch-only-test-no-tests-ran: a probe that ran nothing measured nothing ----

NEW = "tests/test_x.py::test_only_on_the_branch"


def _branch_only_project(repo: Path) -> None:
    """`_project` whose fake pytest refuses a path the tree lacks, as pytest does, and whose gate
    command names a test file only the branch has (the unit_tests scope narrows it to that)."""
    _project(repo)
    script = repo / "bin" / "pytest"
    guard = (
        '#!/bin/sh\nfor a in "$@"; do case "$a" in *.py) if [ ! -e "$a" ]; then\n'
        '  echo "ERROR: file or directory not found: $a"; echo "no tests ran in 0.1s"; exit 4\n'
        "fi;; esac; done\n"
    )
    script.write_text(script.read_text().replace("#!/bin/sh\n", guard, 1))
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "./bin/pytest -q tests/test_x.py tests/test_new.py"\n'
        'cwd = "repo"\n'
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "narrowed"], check=True)
    (repo / "tests" / "test_new.py").write_text("def test_b():\n    pass\n")


def test_a_test_file_only_the_branch_has_does_not_abort_the_probe(repo) -> None:
    """The bug itself: the command names `tests/test_new.py`, the base has no such file, pytest
    aborted having run nothing, and the failure the base shows too read as new."""
    _branch_only_project(repo)
    _code, out, _e = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    body = json.loads(out)
    assert body["outcome"] == "partial", body
    base = body["evidence"]["baseline"]
    assert base["red"] is True and base["still_failing"] == [ID]
    assert "tests/test_new.py" not in base["command"]


def test_run_command_drops_a_test_path_the_tree_lacks_but_keeps_an_option_value(repo) -> None:
    from ddflow.services import testselect as TS

    got = TS.run_command("pytest -q --ignore tests/test_gone.py tests/test_gone.py", [ID], repo)
    assert got.split().count("tests/test_gone.py") == 1
    assert "--ignore tests/test_gone.py" in got


def _measure(repo: Path, ids: list[str], run, ttl_s: int = 0) -> tuple[str, str, dict]:
    """`gate_baseline.apply` over a failed unit_tests run listing ``ids``, ``run`` on the base."""
    from ddflow.api import gate_baseline as GB
    from ddflow.services.gates.defs import GateDef

    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_cache_ttl_s=ttl_s, baseline_red_auto="on"))
    ev: dict = {"failed_tests": list(ids)}
    gdef = GateDef(id="unit_tests", command="pytest -q tests/")
    it = SimpleNamespace(id="T1", base="main")
    outcome, why = GB.apply(repo, cfg, "worker", it, "unit_tests", gdef, "failed", ev, run, False)
    return outcome, why, ev


@pytest.mark.parametrize(
    "result",
    [
        ("failed", {"exit": 5, "summary": ["no tests ran in 0.1s"]}),
        ("failed", {"exit": 4, "summary": ["no tests ran in 0.1s"]}),
        ("passed", {"exit": 0, "summary": ["no tests ran in 0.1s"]}),
        ("passed", {"exit": 0, "summary": ["2 deselected in 0.1s"]}),
        ("unavailable", {"reason": "the suite lock stayed taken"}),
    ],
    ids=["exit5", "exit4", "exit0-no-tests", "exit0-deselected", "unavailable"],
)
def test_a_probe_that_ran_nothing_is_unavailable_never_new_never_passed(repo, result) -> None:
    _project(repo)
    outcome, why, ev = _measure(repo, [ID], lambda g, where=None: result, ttl_s=3600)
    assert outcome == "unavailable", why
    assert "new failure" not in why and ID in why
    found = ev["baseline"]
    assert found["red"] is False and found["unmeasured"] == [ID]
    assert "new_failures" not in found and found["still_failing"] == []
    from ddflow.services.gates import baseline as BL

    assert BL.cache_load(repo, found["base_sha"], 3600, None, "pytest -q tests/") == {}


def test_a_base_that_cannot_be_checked_out_is_unavailable_too(repo, monkeypatch) -> None:
    from ddflow.services.gates import baseline_red as BR

    _project(repo)
    monkeypatch.setattr(BR.W, "rev", lambda *a: "")
    outcome, why, ev = _measure(repo, [ID], lambda g, where=None: ("failed", {}))
    assert outcome == "unavailable" and "new failure" not in why
    assert ev["baseline"]["unmeasured"] == [ID]


def test_a_failing_test_in_a_file_the_base_lacks_is_new_without_running_it(repo) -> None:
    """Not unmeasured: a test whose file the base lacks cannot fail there, so this stays FAILED
    (and the ids beside it are still measured)."""
    _project(repo)
    (repo / "tests" / "test_new.py").write_text("def test_b():\n    pass\n")
    only = "tests/test_new.py::test_b"
    calls: list[str] = []

    def run(g, where=None):
        calls.append(g.command)
        return "failed", {"exit": 1, "failed_tests": [ID], "summary": ["1 failed in 0.1s"]}

    outcome, why, ev = _measure(repo, [ID, only], run)
    assert outcome == "failed" and only in why and ID not in why
    assert ev["baseline"]["new_failures"] == [only] and ev["baseline"]["still_failing"] == [ID]
    assert not any("test_new.py" in c for c in calls)
    calls.clear()
    outcome, why, ev = _measure(repo, [only], run)  # nothing to run, and still measured
    assert outcome == "failed" and ev["baseline"]["new_failures"] == [only] and not calls


def test_a_file_at_neither_root_is_left_to_pytest_to_name(repo) -> None:
    """`cd app && pytest`: ids are relative to `app/`, at neither root, so absence proves nothing
    there; pytest saying it cannot find one does."""
    _project(repo)
    far = "tests/test_far.py::test_c"
    calls: list[str] = []

    def run(g, where=None):
        calls.append(g.command)
        if far in g.command:
            return "failed", {"exit": 4, "tail": f"ERROR: file or directory not found: {far}\n"}
        return "failed", {"exit": 1, "failed_tests": [ID], "summary": ["1 failed in 0.1s"]}

    outcome, _why, ev = _measure(repo, [ID, far], run)
    assert far in calls[0] and outcome == "failed"
    assert ev["baseline"]["new_failures"] == [far] and ev["baseline"]["still_failing"] == [ID]


def _xdist_base(repo: Path, missing: list[str], *, says_which: bool = True, red: str = ID):
    """A base run the way pytest-xdist answers an unknown node id: exit 5 and no word of which
    one, while a serial run (`-n 0`) names it and runs nothing. ``missing`` are `(id, the name
    pytest prints)` pairs; without them ``red`` fails."""
    calls: list[str] = []

    def run(g, where=None):
        calls.append(g.command)
        if not any(i in g.command for i, _n in missing):
            return "failed", {"exit": 1, "failed_tests": [red], "summary": ["1 failed in 0.1s"]}
        if g.command.endswith("-n 0") and says_which:
            lines = [f"ERROR: not found: {repo}/{n}\n(no match in any)" for _i, n in missing]
            return "failed", {"exit": 4, "tail": "\n".join(lines)}
        return "failed", {"exit": 5, "summary": ["no tests ran in 0.1s"]}

    return run, calls


def test_a_test_only_the_branch_has_in_a_file_the_base_has_is_new_not_unavailable(repo) -> None:
    """One unknown node id aborts the whole chunk, and under xdist nothing says which: the serial
    run names it, and the rest are measured. A real new failure must stay FAILED."""
    _project(repo)
    run, calls = _xdist_base(repo, [(NEW, NEW)])
    outcome, why, ev = _measure(repo, [ID, NEW], run)
    assert outcome == "failed" and NEW in why and ID not in why, why
    found = ev["baseline"]
    assert found["new_failures"] == [NEW] and found["still_failing"] == [ID]
    assert not found["unmeasured"] and len(calls) == 3


def test_a_lone_test_only_the_branch_has_is_new(repo) -> None:
    _project(repo)
    run, _calls = _xdist_base(repo, [(NEW, NEW)])
    outcome, why, _ev = _measure(repo, [NEW], run)
    assert outcome == "failed" and NEW in why


def test_an_abort_that_names_no_test_leaves_every_id_unmeasured(repo) -> None:
    """Exit 4 is also a broken conftest: only pytest naming the id says it is missing."""
    _project(repo)
    run, _calls = _xdist_base(repo, [(NEW, NEW)], says_which=False)
    outcome, _why, ev = _measure(repo, [ID, NEW], run)
    assert outcome == "unavailable"
    assert sorted(ev["baseline"]["unmeasured"]) == sorted([ID, NEW])


def test_params_pytest_cannot_tell_apart_are_asked_about_one_by_one(repo) -> None:
    """pytest prints `test_p` for `test_p[9]`: with two params of it failing either may be the one
    the base has, so each runs alone. Neither is called new on that line's word."""
    _project(repo)
    one, two = "tests/test_x.py::test_p[1]", "tests/test_x.py::test_p[9]"
    run, _calls = _xdist_base(repo, [(two, "tests/test_x.py::test_p")], red=one)
    outcome, why, ev = _measure(repo, [one, two], run)
    assert outcome == "failed" and two in why and one not in why, why
    found = ev["baseline"]
    assert found["new_failures"] == [two] and found["still_failing"] == [one]


def test_a_param_is_stripped_from_the_test_name_and_never_from_the_path() -> None:
    from ddflow.services.gates import baseline_red as BR

    assert BR._PARAM.sub(r"\1", "tests[1]/t.py::C::test_x[a[0]]") == "tests[1]/t.py::C::test_x"
    assert BR._PARAM.sub(r"\1", "tests[1]/t.py::test_x") == "tests[1]/t.py::test_x"


def test_some_measured_new_with_some_unmeasured_stays_failed_and_says_both(repo) -> None:
    from ddflow.services.gates import baseline_red as BR

    _project(repo)
    ids = [f"tests/test_x.py::test_{i}" for i in range(BR.CHUNK + 1)]

    def run(g, where=None):
        if ids[-1] in g.command:
            return "failed", {"exit": 5, "summary": ["no tests ran in 0.1s"]}
        return "passed", {"exit": 0, "summary": [f"{BR.CHUNK} passed in 0.1s"]}

    outcome, why, ev = _measure(repo, ids, run)
    assert outcome == "failed" and "1 more could not be measured" in why
    assert ev["baseline"]["unmeasured"] == [ids[-1]]
    assert ev["baseline"]["new_failures"] == ids[: BR.SHOWN * 4] and ids[0] in why


def test_a_run_cut_short_answers_only_when_it_reached_every_id(repo) -> None:
    """Exit 2 is a collection error and an interrupt alike: a pass it counted for the one id asked
    is a measurement, and one it did not reach (or a file that never collected) is not."""
    _project(repo)
    two = "tests/test_x.py::test_b"

    def cut(summary):
        return lambda g, where=None: ("failed", {"exit": 2, "summary": [summary]})

    outcome, _why, ev = _measure(repo, [ID], cut("1 passed in 0.1s"))
    assert outcome == "failed" and ev["baseline"]["new_failures"] == [ID]
    outcome, _why, ev = _measure(repo, [ID, two], cut("1 passed in 0.1s"))
    assert outcome == "unavailable" and ev["baseline"]["unmeasured"] == [ID, two]
    outcome, _why, ev = _measure(repo, [ID], cut("1 error in 0.1s"))
    assert outcome == "unavailable" and ev["baseline"]["unmeasured"] == [ID]


@pytest.mark.parametrize(
    "result",
    [
        ("passed", {"exit": 0, "summary": ["1 passed, 1 deselected in 0.1s"]}),
        (
            "failed",
            {"exit": 1, "failed_tests": [ID], "summary": ["1 failed, 1 deselected in 0.1s"]},
        ),
    ],
    ids=["pass", "fail"],
)
def test_a_run_that_reached_fewer_ids_than_asked_says_nothing_of_the_rest(repo, result) -> None:
    """A kept `-k` deselects: the id it never ran is not one that passes on the base."""
    _project(repo)
    two = "tests/test_x.py::test_b"
    outcome, why, ev = _measure(repo, [ID, two], lambda g, where=None: result)
    assert outcome == "unavailable" and "new failure" not in why
    assert ev["baseline"]["unmeasured"] == [ID, two]


def test_a_command_that_is_no_pytest_stays_failed_and_claims_nothing_new(repo) -> None:
    """No probe exists for it, so there is no measurement to lose: the failure stands as it was."""
    from ddflow.api import gate_baseline as GB
    from ddflow.services.gates.defs import GateDef

    _project(repo)
    cfg = SimpleNamespace(gates=SimpleNamespace(baseline_cache_ttl_s=0, baseline_red_auto="on"))
    ev: dict = {"failed_tests": [ID]}
    gdef = GateDef(id="unit_tests", command="make check")
    it = SimpleNamespace(id="T1", base="main")
    outcome, why = GB.apply(repo, cfg, "w", it, "unit_tests", gdef, "failed", ev, None, False)
    assert outcome == "failed" and "new failure" not in why
    assert "new_failures" not in ev["baseline"] and not ev["baseline"].get("unmeasured")


def _sha(repo: Path, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_the_baseline_is_pinned_to_the_candidates_own_merge_base(repo) -> None:
    """B48c70b6773: the candidate forks from a red main; main then moves on and fixes the
    failing test. The baseline is measured on the candidate's own merge-base (still red), not
    the moved main tip (green) -- otherwise the branch's unchanged failure reads as new."""
    from ddflow.services.gates import baseline_red as BR
    from ddflow.services.gates.defs import GateDef

    _project(repo)  # main fails test_a (no `healthy` file)
    red = _sha(repo)
    subprocess.run(["git", "-C", str(repo), "switch", "-qc", "cand"], check=True)
    subprocess.run(["git", "-C", str(repo), "switch", "-q", "main"], check=True)
    (repo / "healthy").write_text("")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fix test_a on main"], check=True)
    green = _sha(repo)
    subprocess.run(["git", "-C", str(repo), "switch", "-q", "cand"], check=True)
    assert green != red  # main has moved: the move is the whole point of the test

    gdef = GateDef(id="unit_tests", command="./bin/pytest -q tests/")

    def run(g, where=None):
        got = subprocess.run(["sh", "-c", g.command], cwd=where, capture_output=True, text=True)
        failed = got.returncode != 0
        return (
            "failed" if failed else "passed",
            {
                "exit": got.returncode,
                "summary": [],
                "tail": got.stdout,
                "failed_tests": [ID] if failed else [],
            },
        )

    found = BR.on_base(repo, "main", gdef, [ID], run, 0, tree=repo)
    assert found["red"] is True, found
    assert found["base_sha"] == red, found
