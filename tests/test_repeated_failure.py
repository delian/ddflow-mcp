"""The same gate failing N times with the same output digest (task B-repeated-failure-detector).

`gate_flapping` needs the verdict to FLIP; an agent re-applying the same failing patch
never flips it. The log already holds each run's output digest, so N consecutive
failures of one gate with one digest is a loop the log can show. It warns -- in doctor,
`ddflow loops` and the item's brief -- and blocks only with `[loops] on_detect = "block"`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_progress_loops import detect, ev

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.views import markdown as MD

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _fail(n, gate="unit_tests", digest="aaaa", tree="t1"):
    return ev(
        n,
        "gate.failed",
        "T",
        {"gate": gate, "reason": "red", "evidence": {"output_digest": digest, "tree_sha": tree}},
    )


def _pass(n, gate="unit_tests"):
    return ev(n, "gate.passed", "T", {"gate": gate, "evidence": {"output_digest": "ok"}})


def _repeats(evs, cfg=None):
    return [f for f in detect(evs, cfg) if f.kind == "repeated_failure"]


BASE = [ev(1, "task.added", "T", {"parent": "P"})]


def test_three_identical_failures_warn_with_the_evidence():
    found = _repeats([*BASE, _fail(2), _fail(3), _fail(4)])
    assert len(found) == 1
    f = found[0]
    assert (f.item, f.gate, f.count, f.threshold, f.severity) == ("T", "unit_tests", 3, 3, "warn")
    assert "aaaa" in f.detail and "unit_tests" in f.detail


def test_two_identical_failures_are_under_the_default():
    assert _repeats([*BASE, _fail(2), _fail(3)]) == []


def test_different_digests_do_not_warn():
    evs = [*BASE, _fail(2, digest="a"), _fail(3, digest="b"), _fail(4, digest="c")]
    assert _repeats(evs) == []


def test_only_the_trailing_run_of_one_digest_counts():
    evs = [
        *BASE,
        *(_fail(n, digest="a") for n in (2, 3, 4)),
        _fail(5, digest="b"),
        _fail(6, digest="b"),
    ]
    assert _repeats(evs) == []


def test_a_pass_in_between_resets_the_count():
    evs = [*BASE, _fail(2), _fail(3), _pass(4), _fail(5), _fail(6)]
    assert _repeats(evs) == []


def test_a_failure_with_no_digest_breaks_the_streak():
    evs = [*BASE, _fail(2), _fail(3), _fail(4, digest=""), _fail(5)]
    assert _repeats(evs) == []


def test_reviewer_gates_are_ignored():
    """D-failed-critic-not-blocking: a failed review is not a failure of the work."""
    evs = list(BASE)
    for g in ("critic", "rubber_duck"):
        evs += [_fail(10, gate=g), _fail(11, gate=g), _fail(12, gate=g), _fail(13, gate=g)]
    assert _repeats(evs) == []


def test_the_threshold_is_a_loops_knob_and_zero_disables_it():
    evs = [*BASE, _fail(2), _fail(3)]
    cfg = Config()
    cfg.loops.max_repeated_failures = 2
    assert len(_repeats(evs, cfg)) == 1
    cfg.loops.max_repeated_failures = 0
    assert _repeats([*evs, _fail(4), _fail(5)], cfg) == []


def test_on_detect_block_makes_it_blocking_and_warn_does_not():
    evs = [*BASE, _fail(2), _fail(3), _fail(4)]
    cfg = Config()
    cfg.loops.on_detect = "block"
    assert _repeats(evs, cfg)[0].severity == "block"
    assert _repeats(evs)[0].severity == "warn"


def test_the_items_brief_names_the_repeat():
    evs = [*BASE, _fail(2), _fail(3), _fail(4)]
    st = fold(evs, strict=False)
    cfg = Config()
    from ddflow.core.schedule import plan

    out = MD.brief(st, cfg, plan(st, cfg), item="T", loops=detect(evs))
    assert "repeated_failure" in out and "unit_tests" in out
    quiet = MD.brief(st, cfg, plan(st, cfg), item="T", loops=[])
    assert "repeated_failure" not in quiet


# -- through the CLI --------------------------------------------------------------------


def _setup(repo: Path, extra: str = "") -> None:
    run_cli(repo, "init")
    cmd = f'{sys.executable} -c "import sys; print(chr(98)*3); sys.exit(1)"'
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = {json.dumps(cmd)}\ncwd = "repo"\n'
    )
    if extra:
        (repo / ".ddflow" / "config.toml").write_text(extra)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")


def test_three_real_identical_failures_reach_loops_and_doctor(repo):
    _setup(repo)
    for _ in range(3):
        assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == FAIL
    code, out, _ = run_cli(repo, "loops")
    assert code == FAIL and "repeated_failure" in out
    code, out, _ = run_cli(repo, "doctor")
    assert "repeated_failure" in out
    assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == FAIL  # warn only


def test_block_refuses_a_gate_rerun_on_an_unchanged_tree_with_the_evidence(repo):
    _setup(repo, "[loops]\non_detect = 'block'\n")
    for _ in range(3):
        assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == FAIL
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    assert code == REFUSED, out + err
    assert "same output" in out + err or "repeated_failure" in out + err
    # changing the tree is the way out: the next run is allowed
    (repo / "a.py").write_text("print('changed')\n")
    assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == FAIL


def test_loops_reports_repeated_failures_among_what_it_checked(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "loops", "--json")
    assert code == NOTHING and "repeated failures" in out
