"""The ci gate says a stale pin is a stale pin (B305db5608b).

A branch whose pinned outputs were current fails the gate after main moved: the merge result
has both sides' lines and a count neither side computed. The whole suite then ran for
minutes and ended in a test failure that did not name the pin. The run now checks the pins
in the merge result first; and a gate that could not run says why in `gate status`."""

from __future__ import annotations

import json

import pytest
from conftest import run_cli
from helpers import commit as _commit
from helpers import git as _git
from test_ci_gate import _branch, _project, fake_precommit  # noqa: F401

from ddflow.config import Config
from ddflow.services import ci as CI

GEN = """\
import sys
from pathlib import Path

knobs = Path("knobs.txt").read_text().split()
want = {"count.txt": f"{len(knobs)}\\n", "list.txt": "".join(f"- {k}\\n" for k in sorted(knobs))}
if "--check" in sys.argv:
    ok = all(Path(f).exists() and Path(f).read_text() == t for f, t in want.items())
    sys.exit(0 if ok else 1)
for f, t in want.items():
    Path(f).write_text(t)
"""

RECIPES = """\
[[pin]]
name = "knobs"
files = ["count.txt", "list.txt"]
check = ["{python}", "gen.py", "--check"]
regen = ["{python}", "gen.py"]
"""


def _pinned_project(repo):
    _project(repo)
    default = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    (repo / ".ddflow").mkdir(exist_ok=True)
    _commit(
        repo,
        {
            "gen.py": GEN,
            "knobs.txt": "a\nb\n",
            ".ddflow/pins.toml": RECIPES,
        },
        "pins",
    )
    from ddflow.services import pins

    pins.regenerate(repo, Config.load(repo))
    _commit(
        repo,
        {
            "count.txt": (repo / "count.txt").read_text(),
            "list.txt": (repo / "list.txt").read_text(),
        },
        "pinned",
    )
    return default


def _drift(repo, default):
    """The branch adds knob z, main adds knob 0: each side's pins are current alone, and the
    merge result holds count.txt = 3 (both sides said 3) for four knobs."""
    _branch(repo)
    _commit(
        repo,
        {"knobs.txt": "a\nb\nz\n", "count.txt": "3\n", "list.txt": "- a\n- b\n- z\n"},
        "branch",
    )
    _git(repo, "switch", "-q", default)
    _commit(
        repo, {"knobs.txt": "0\na\nb\n", "count.txt": "3\n", "list.txt": "- 0\n- a\n- b\n"}, "main"
    )
    _git(repo, "switch", "-q", "work")


@pytest.mark.usefixtures("fake_precommit")
def test_a_pin_made_stale_by_main_is_named_before_the_suite_runs(repo, monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    default = _pinned_project(repo)
    _drift(repo, default)
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "failed", res.reason
    assert "pins stale after merging" in res.reason and "knobs" in res.reason, res.reason
    assert "ddflow pins regenerate" in res.reason
    assert not res.checks, "the suite must not have run"


@pytest.mark.usefixtures("fake_precommit")
def test_current_pins_in_the_merge_result_let_the_run_go_on(repo, monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    default = _pinned_project(repo)
    _branch(repo)
    _commit(repo, {"unrelated.txt": "x\n"}, "work")
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.ok, res.reason


@pytest.mark.usefixtures("fake_precommit")
def test_the_precheck_can_be_turned_off(repo, monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    default = _pinned_project(repo)
    _drift(repo, default)
    cfg = Config.load(repo)
    cfg.ci.pins_precheck = False
    assert CI.run(repo, cfg, base=default).ok


@pytest.mark.parametrize("outcome", ["unavailable", "partial"])
def test_gate_status_says_why_a_gate_could_not_run(repo, outcome):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        '[gates]\ntask_pipeline = ["implement", "ci", "merge"]\n'
    )
    run_cli(repo, "phase", "add", "P1", "--globs", "src/**")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--globs", "src/x.py")
    run_cli(repo, "claim", "T1")
    code, _out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "ci",
        "--outcome",
        outcome,
        "--reason",
        "wrapper killed by SIGTERM",
    )
    assert code == 0, err
    _code, out, _ = run_cli(repo, "gate", "status", "T1")
    row = next(x for x in out.splitlines() if "] ci" in x)
    assert "wrapper killed by SIGTERM" in row, out
    _code, out, _ = run_cli(repo, "--json", "gate", "status", "T1")
    assert json.loads(out)["why"]["ci"] == "wrapper killed by SIGTERM"


@pytest.mark.usefixtures("fake_precommit")
def test_one_pin_that_cannot_be_checked_does_not_hide_a_stale_one(repo, monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    default = _pinned_project(repo)
    recipes = (
        '[[pin]]\nname = "broken"\nfiles = ["x.txt"]\n'
        'check = ["/no/such/checker"]\nregen = ["/no/such/checker"]\n\n' + RECIPES
    )
    _commit(repo, {".ddflow/pins.toml": recipes}, "a pin whose checker is gone")
    _drift(repo, default)
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "failed" and "knobs" in res.reason, res.reason


def test_a_whitespace_only_reason_still_reads_as_no_reason():
    from ddflow.services.gates.outcomes import unavailable_reasons

    from types import SimpleNamespace as NS

    it = NS(gates={"ci": NS(reason="  \n ")})
    assert unavailable_reasons(it, [("ci", "unavailable")]) == {"ci": "no reason was recorded"}
