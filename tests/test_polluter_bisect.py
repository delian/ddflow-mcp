"""Test-polluter bisect (B26): which earlier test file makes another test fail?

The real thing is exercised against a fixture project with a deliberately polluting test,
through the real command runner and a real `pytest`. The search logic is checked against
brute force.
"""

from __future__ import annotations

import itertools
import random
import sys
import textwrap
from pathlib import Path

import pytest

from ddflow.api import bisect as API
from ddflow.services import bisect as B

# -- the search, against brute force -----------------------------------------------------


def _one_minimal(candidates, fails, result):
    assert fails(result)
    for i in range(len(result)):
        smaller = result[:i] + result[i + 1 :]
        assert not smaller or not fails(smaller), f"{result} is not 1-minimal: {smaller} fails"


@pytest.mark.parametrize("seed", range(40))
def test_ddmin_finds_exactly_the_culprits_for_a_monotonic_failure(seed):
    rng = random.Random(seed)
    files = [f"t{i:02d}.py" for i in range(rng.randint(1, 40))]
    culprits = set(rng.sample(files, rng.randint(1, min(3, len(files)))))

    def fails(subset):
        return culprits <= set(subset)

    out = B.ddmin(files, fails)
    assert set(out) == culprits
    assert out == [f for f in files if f in culprits], "order is kept"


@pytest.mark.parametrize("seed", range(40))
def test_ddmin_result_is_one_minimal_for_a_non_monotonic_failure(seed):
    """Failure when an ODD number of 'bad' files ran: not monotonic, ddmin is still 1-minimal."""
    rng = random.Random(1000 + seed)
    files = [f"t{i:02d}.py" for i in range(rng.randint(2, 24))]
    bad = set(rng.sample(files, rng.randint(1, len(files))))

    def fails(subset):
        return len(bad & set(subset)) % 2 == 1

    if not fails(files):
        return
    out = B.ddmin(files, fails)
    _one_minimal(files, fails, out)


def test_ddmin_pair_interaction_needs_both():
    files = list("abcdefgh")

    def fails(s):
        return {"c", "f"} <= set(s)

    assert B.ddmin(files, fails) == ["c", "f"]


def test_ddmin_probe_count_is_far_below_brute_force():
    files = [f"t{i}" for i in range(64)]
    calls = []

    def fails(s):
        calls.append(1)
        return "t41" in s

    assert B.ddmin(files, fails) == ["t41"]
    assert len(calls) <= 20, len(calls)  # ~2*log2(64), not 64


# -- the states, with a fake probe ----------------------------------------------------------


def _probe(polluters, victim_alone_fails=False):
    def probe(tests):
        assert tests[-1] == "V"
        if victim_alone_fails:
            return False
        return not (set(polluters) <= set(tests[:-1]) and polluters)

    return probe


def test_found():
    r = B.bisect("V", list("abcdef"), _probe({"d"}))
    assert (r.state, r.polluters) == ("found", ["d"])


def test_victim_fails_alone_is_not_an_ordering_problem():
    r = B.bisect("V", list("abc"), _probe(set(), victim_alone_fails=True))
    assert r.state == "victim_fails_alone" and r.polluters == []


def test_not_reproducible_when_nothing_pollutes():
    r = B.bisect("V", list("abc"), _probe(set()))
    assert r.state == "not_reproducible"


def test_no_candidates_is_not_reproducible():
    assert B.bisect("V", [], _probe(set())).state == "not_reproducible"


def test_a_probe_that_could_not_run_is_unavailable_never_a_pass_or_fail():
    calls = []

    def probe(tests):
        calls.append(tests)
        return True if len(calls) == 1 else None

    r = B.bisect("V", list("abc"), probe)
    assert r.state == "unavailable"


def test_budget_exhausted_reports_the_smallest_set_so_far():
    r = B.bisect("V", list("abcdefgh"), _probe({"b", "g"}), max_runs=5)
    assert r.state == "budget_exhausted"
    assert set(r.polluters) >= {"b", "g"}


# -- the command runner, a real pytest, a deliberately polluting project --------------------

POLLUTER = """
import os
def test_sets_a_flag():
    os.environ["POLLUTED_BY"] = os.environ.get("POLLUTED_BY", "") + "{name}"
"""

CLEAN = """
def test_nothing():
    assert True
"""

VICTIM = """
import os
def test_victim():
    assert "c" not in os.environ.get("POLLUTED_BY", ""), os.environ.get("POLLUTED_BY")

def test_pair():
    got = os.environ.get("POLLUTED_BY", "")
    assert not ("b" in got and "d" in got)
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    t = tmp_path / "tests"
    t.mkdir()
    for name in "abcdefg":
        body = POLLUTER.format(name=name) if name in "bcd" else CLEAN
        (t / f"test_{name}.py").write_text(textwrap.dedent(body))
    (t / "test_zz_victim.py").write_text(textwrap.dedent(VICTIM))
    return tmp_path


CMD = f"{sys.executable} -m pytest -q -p no:cacheprovider -p no:randomly {{tests}}"


def test_the_fixture_really_is_order_dependent(project):
    import subprocess

    def run(*files):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-p",
                "no:randomly",
                *files,
            ],
            cwd=project,
            capture_output=True,
        ).returncode

    assert run("tests/test_zz_victim.py::test_victim") == 0
    assert run("tests/test_c.py", "tests/test_zz_victim.py::test_victim") != 0


def test_bisect_finds_the_single_polluting_file_with_real_pytest(project):
    out = API.bisect(project, "tests/test_zz_victim.py::test_victim", CMD)
    assert out.exit == 0, out.data
    assert out.data["state"] == "found"
    assert out.data["polluters"] == ["tests/test_c.py"]
    assert out.data["candidates"] == 7
    assert len(out.data["runs"]) < 7 + 2  # fewer than trying every file alone


def test_bisect_finds_a_pair_that_only_pollutes_together(project):
    out = API.bisect(project, "tests/test_zz_victim.py::test_pair", CMD)
    assert out.exit == 0, out.data
    assert out.data["polluters"] == ["tests/test_b.py", "tests/test_d.py"]


def test_a_victim_that_passes_after_everything_is_not_reproducible(project):
    (project / "tests" / "test_zz_victim.py").write_text("def test_victim():\n    assert True\n")
    out = API.bisect(project, "tests/test_zz_victim.py::test_victim", CMD)
    assert out.exit == 2 and out.data["state"] == "not_reproducible"


def test_a_victim_that_fails_alone_says_so(project):
    (project / "tests" / "test_zz_victim.py").write_text("def test_victim():\n    assert False\n")
    out = API.bisect(project, "tests/test_zz_victim.py::test_victim", CMD)
    assert out.exit == 2 and out.data["state"] == "victim_fails_alone"


def test_only_files_that_run_before_the_victim_are_candidates(project):
    (project / "tests" / "test_zzz_after.py").write_text(textwrap.dedent(POLLUTER.format(name="c")))
    cands = API.candidates_before(project, "tests/test_zz_victim.py::test_victim", [], "")
    assert "tests/test_zzz_after.py" not in cands and "tests/test_zz_victim.py" not in cands
    assert cands == [f"tests/test_{n}.py" for n in "abcdefg"]


def test_named_candidates_are_taken_in_the_given_order(project):
    out = API.bisect(
        project,
        "tests/test_zz_victim.py::test_victim",
        CMD,
        candidates="tests/test_g.py,tests/test_c.py,tests/test_a.py",
    )
    assert out.data["polluters"] == ["tests/test_c.py"] and out.data["candidates"] == 3


def test_a_command_without_the_placeholder_is_refused(project):
    out = API.bisect(project, "tests/test_zz_victim.py::test_victim", "pytest -q")
    assert out.exit == 1 and "{tests}" in out.reason


def test_a_missing_victim_is_refused(project):
    assert API.bisect(project, " ", CMD).exit == 1


def test_a_command_that_cannot_be_spawned_is_unavailable_not_a_pass(project):
    out = API.bisect(project, "tests/test_zz_victim.py::test_victim", "no-such-runner-xyz {tests}")
    assert out.exit == 2 and out.data["state"] == "unavailable"
    assert out.data["runs"][0]["outcome"] == "could_not_run"


def test_a_timeout_is_unavailable(project):
    out = API.bisect(
        project,
        "tests/test_zz_victim.py::test_victim",
        f"{sys.executable} -c 'import time; time.sleep(30)' {{tests}}",
        timeout_s=1,
    )
    assert out.data["state"] == "unavailable" and out.data["runs"][0]["detail"].startswith(
        "timed out"
    )


def test_repeat_catches_a_flaky_pollution(project, tmp_path):
    """A list FAILS if any of its runs fails: pollution that shows one run in two is still
    pollution."""
    counter = tmp_path / "n"
    counter.write_text("0")
    script = tmp_path / "flaky.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            n = int(open({str(counter)!r}).read()); open({str(counter)!r}, "w").write(str(n + 1))
            polluted = any("test_c" in a for a in sys.argv[1:])
            sys.exit(1 if polluted and n % 2 == 0 else 0)
            """
        )
    )
    cmd = f"{sys.executable} {script} {{tests}}"
    one = B.command_probe(cmd, project, repeat=1)
    two = B.command_probe(cmd, project, repeat=2)
    counter.write_text("1")  # the next single run passes, the one after fails
    assert one(["tests/test_c.py", "v"]) is True
    counter.write_text("1")
    assert two(["tests/test_c.py", "v"]) is False


def test_all_subsets_agree_with_brute_force_on_a_small_suite():
    files = list("abcde")
    for r in range(1, 4):
        for culprit_set in itertools.combinations(files, r):
            res = B.bisect("V", files, _probe(set(culprit_set)))
            assert res.state == "found" and set(res.polluters) == set(culprit_set)


@pytest.mark.parametrize(
    "spelling", ["./tests/test_zz_victim.py", "tests/test_zz_victim.py", "ABS"]
)
def test_the_victim_may_be_spelled_any_way_a_shell_would(project, spelling):
    path = str(project / "tests" / "test_zz_victim.py") if spelling == "ABS" else spelling
    cands = API.candidates_before(project, f"{path}::test_victim", [], "")
    assert cands == [f"tests/test_{n}.py" for n in "abcdefg"], cands


def test_named_candidates_are_normalised_and_the_victims_file_dropped(project):
    cands = API.candidates_before(
        project,
        "./tests/test_zz_victim.py::test_victim",
        ["./tests/test_a.py", "tests/test_zz_victim.py"],
        "",
    )
    assert cands == ["tests/test_a.py"]


def test_undecodable_output_is_a_result_not_a_crash(project):
    """A byte that is not valid text (a test printing latin-1 under LC_ALL=C) must not
    escape as an exception: the run's verdict is its exit code."""
    cmd = f"{sys.executable} -c 'import sys; sys.stdout.buffer.write(bytes([0xff, 0xfe])); sys.exit(1)' {{tests}}"
    probe = B.command_probe(cmd, project)
    assert probe(["x"]) is False


def test_dotdot_spellings_and_a_glob_that_misses_the_victims_file(project):
    cands = API.candidates_before(project, "tests/../tests/test_zz_victim.py::test_victim", [], "")
    assert cands == [f"tests/test_{n}.py" for n in "abcdefg"]
    (project / "tests" / "test_zzz_after.py").write_text(CLEAN)
    # A glob that does not include the victim's file must still never offer a file that
    # sorts after it.
    cands = API.candidates_before(
        project, "tests/test_zz_victim.py::test_victim", [], "tests/test_[a-z].py"
    )
    assert cands == [f"tests/test_{n}.py" for n in "abcdefg"]
    cands = API.candidates_before(
        project, "tests/test_c.py::test_sets_a_flag", [], "tests/test_[a-z].py"
    )
    assert cands == ["tests/test_a.py", "tests/test_b.py"]


def test_ddmin_complement_is_by_position_so_duplicates_stay_one_minimal():
    files = ["a", "a", "b"]

    def fails(s):
        return "a" in s and "b" in s

    out = B.ddmin(files, fails)
    assert sorted(out) == ["a", "b"], out


def test_bisect_treats_a_duplicated_candidate_as_one_file():
    r = B.bisect("V", ["a", "b", "a", "c"], _probe({"c"}))
    assert r.state == "found" and r.polluters == ["c"] and r.candidates == 3


def test_budget_exhaustion_before_the_full_set_is_shown_to_fail_reports_no_polluters():
    """Bug Beca0feba54: with the budget gone before the whole candidate set was seen to
    make the victim fail, the candidates are unverified and must not come back as polluters."""
    candidates = list("abcdef")

    needs_c = _probe({"c"})  # the victim fails once c has run before it

    # one run: only the victim alone was run; the full set was never tried
    r = B.bisect("V", candidates, needs_c, max_runs=1)
    assert r.state == "budget_exhausted"
    assert r.polluters == [], f"unverified candidates returned: {r.polluters}"
    # two runs: the full set is shown to fail, so it is a (not minimal) verified set
    r = B.bisect("V", candidates, needs_c, max_runs=2)
    assert r.state == "budget_exhausted" and set(r.polluters) >= {"c"}


def _collected_files(project: Path) -> list[str]:
    """The files in the order pytest itself collects them."""
    import subprocess

    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
        ],
        cwd=project,
        capture_output=True,
        text=True,
    ).stdout
    files = [ln.split("::", 1)[0] for ln in out.splitlines() if "::" in ln]
    return list(dict.fromkeys(files))


def test_candidate_order_follows_pytests_real_collection_order(tmp_path):
    """Bug Beca0feba54: a directory sorts before a same-stem file ('test_a' < 'test_a.py'),
    which a whole-path string sort gets backwards ('.' < '/')."""
    t = tmp_path / "tests"
    (t / "test_a").mkdir(parents=True)
    for rel in ("test_a.py", "test_a/test_x.py", "test_a/test_y.py", "test_b.py", "test_a_b.py"):
        (t / rel).write_text("def test_t():\n    pass\n")
    order = _collected_files(tmp_path)
    assert order[:2] == ["tests/test_a/test_x.py", "tests/test_a/test_y.py"], order
    assert order.index("tests/test_a/test_x.py") < order.index("tests/test_a.py")
    for victim in order:
        want = order[: order.index(victim)]
        assert API.candidates_before(tmp_path, victim + "::test_t", [], "") == want, victim


# -- the real CLI and the MCP tool ----------------------------------------------------------


def _cli(repo, *argv):
    from tests.conftest import run_cli

    return run_cli(repo, *argv)


def test_cli_bisect_end_to_end_with_real_pytest(project):
    import json

    code, out, err = _cli(
        project, "--json", "bisect", "tests/test_zz_victim.py::test_victim", "--cmd", CMD
    )
    assert code == 0, out + err
    body = json.loads(out)
    assert body["state"] == "found" and body["polluters"] == ["tests/test_c.py"]
    code, out, err = _cli(project, "bisect", "tests/test_zz_victim.py::test_pair", "--cmd", CMD)
    assert code == 0, out + err
    assert "tests/test_b.py" in out and "tests/test_d.py" in out and "make it fail" in out


def test_cli_bisect_exit_codes(project):
    victim = "tests/test_zz_victim.py::test_victim"
    assert _cli(project, "bisect", victim, "--cmd", "pytest -q")[0] == 1  # no {tests}
    code, out, _ = _cli(project, "bisect", victim, "--cmd", "no-such-runner-xyz {tests}")
    assert code == 2 and "unavailable" in out
    (project / "tests" / "test_zz_victim.py").write_text("def test_victim():\n    assert False\n")
    code, out, _ = _cli(project, "bisect", victim, "--cmd", CMD)
    assert code == 2 and "victim_fails_alone" in out


def test_mcp_tool_bisects(project):
    import json

    from ddflow.surfaces.mcp import Server

    reply = Server(project).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_bisect",
                "arguments": {"victim": "tests/test_zz_victim.py::test_victim", "cmd": CMD},
            },
        }
    )
    body = json.loads(reply["result"]["content"][0]["text"])
    assert body["state"] == "found" and body["polluters"] == ["tests/test_c.py"], body
