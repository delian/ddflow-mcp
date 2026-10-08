"""The end-to-end scenarios, run by pytest.

These have found most of the real bugs in this project and the unit tests found few of
them: the composed MCP run alone found eight that 213 unit tests and four other
scenarios missed, and `full-lifecycle` found four CLI/MCP divergences before it passed
once. They live in `demos/` and were invoked by hand, which meant **the suite that
matters most was the one automation did not run**.

Marked `slow` and deselected by default, because each spawns real processes, creates
real git worktrees and runs a real `pytest` inside them — two to four minutes for the
set. Run them explicitly:

    pytest tests/test_scenarios.py -m slow        # or: python demos/run_all.py

The marker is the honest trade: putting four minutes of subprocess work into every
`pytest` run is how a suite becomes something people skip, and a skipped suite protects
nothing. What this file buys is that CI *can* run them by name, and that a scenario
which stops importing fails here rather than silently rotting until someone runs the
demos by hand.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "demos"), str(ROOT)]


def _scenarios():
    import run_all

    return run_all.SCENARIOS


def test_every_scenario_still_imports():
    """Cheap, and it is the check that actually rots.

    A scenario is a module that reaches deep into the package — `ddflow_claim` tool
    names, `Plan` fields, event kinds. When the package moves, a scenario breaks at
    IMPORT time, and nobody finds out until the next manual run. This is that check,
    and it costs a second.
    """
    names = [n for n, _fn in _scenarios()]
    assert len(names) >= 6, names
    assert "full-lifecycle" in names and "mcp-orchestration" in names


@pytest.mark.slow
@pytest.mark.scenarios
# The run's --timeout is sized for unit tests (420 s in CI's `tests` job and the pre-push
# hook); a scenario gets CI's scenario budget whatever the command line says.
@pytest.mark.timeout(1800)
@pytest.mark.parametrize("name", [n for n, _ in _scenarios()], ids=lambda n: n)
def test_scenario(name, tmp_path):
    """One scenario, against a freshly invented project in a temp directory."""
    from harness import Fail, Scenario

    fn = dict(_scenarios())[name]
    sc = Scenario(name, tmp_path / name)
    try:
        fn(sc)
    except Fail as exc:
        pytest.fail(f"{name}: {exc}")
    assert sc.checks > 0, f"{name} asserted nothing — a scenario that checks nothing passes"


# -- and something has to actually RUN them ---------------------------------------------

WORKFLOWS = ROOT / ".github" / "workflows"


def test_ci_runs_the_scenarios_not_just_collects_them():
    """The marker made them skippable; nothing made them run.

    `addopts = "-m 'not slow'"` deselects every scenario by default -- correct, they
    take ten minutes -- and for a long time the ONLY workflow was `publish`, on version
    tags, which also inherited that addopts. So the suite that has found most of this
    project's real defects ran nowhere automated: eight that 213 unit tests and four
    other scenarios missed, and four CLI/MCP divergences before `full-lifecycle` passed
    once.

    A test file that exists and a marker that selects it are not the feature. Being run
    is the feature, and this is the only thing that can tell the difference.
    """
    assert WORKFLOWS.is_dir(), "no workflows at all"

    def runs_them(text: str) -> bool:
        # Comments stripped, and both halves required on ONE line. Checking the file as
        # a whole passed on a COMMENT that merely explained `-m slow` -- a check about
        # code satisfied by prose about code, which is the vacuous-pass class this
        # project keeps a ratchet against and which this ratchet had itself.
        for raw in text.splitlines():
            line = raw.split("#", 1)[0]
            if "-m slow" in line and "test_scenarios.py" in line:
                return True
        return False

    running = [w.name for w in sorted(WORKFLOWS.glob("*.yml")) if runs_them(w.read_text())]
    assert running, (
        "no workflow runs `pytest tests/test_scenarios.py -m slow`. The scenarios are "
        "deselected by `addopts` everywhere else, so without an explicit job they are "
        "collected by nobody."
    )


def test_ci_checks_every_push_not_only_a_release_tag():
    """`publish` is the wrong and only place to find out.

    Tests that run on a tag tell you a release is broken. Tests that run on a push tell
    you a commit is.
    """
    on_push = [
        w.name
        for w in sorted(WORKFLOWS.glob("*.yml"))
        if "pull_request" in w.read_text() or "branches:" in w.read_text()
    ]
    assert on_push, "every workflow is tag-triggered; nothing checks ordinary commits"


#: How late each live-window check starts in the load regression below: longer than the 8 s
#: lease the scenario once ran under, which is what a slow process start did to it at load
#: average 100-300 (B11e64b1e8c, Bb070dd642d, Bfb0e454b49, B06a0a99c0e). Each earlier fix
#: widened a margin or scaled to a measured latency; the scenario now keeps the lease live
#: by grace instead of racing the clock, so no latency is long enough to fail it.
LATE_CHECK_S = 10


@pytest.mark.slow
@pytest.mark.scenarios
@pytest.mark.timeout(1800)
def test_crash_recovery_does_not_race_the_clock_for_its_live_window_checks(tmp_path):
    """B06a0a99c0e: every live-window check (the refused claim, `recover`, `doctor` in the
    new holder's lease) starts `LATE_CHECK_S` late (every time it is run), longer than the lease once was. The
    scenario must still pass: it may not depend on how fast the machine starts a process.
    Against the scenario that held an 8 s lease and retried, this failed every time."""
    import scenario_crash_recovery as S
    from harness import Fail, Scenario

    class Late(Scenario):
        late: frozenset = frozenset()

        def ddflow(self, *argv, **kw):
            if (
                argv == ("recover",)
                or (argv[:1] == ("claim",) and kw.get("expect") is None)
                or argv[:1] == ("doctor",)
            ):
                self.late = self.late | {argv[0]}
                time.sleep(LATE_CHECK_S)
            return super().ddflow(*argv, **kw)

    sc = Late("crash-recovery-late", tmp_path / "late")
    try:
        S.run(sc)
    except Fail as exc:
        pytest.fail(f"crash-recovery with late live-window checks: {exc}")
    assert sc.late == {"recover", "claim", "doctor"}, f"no delay was injected: {sc.late}"
