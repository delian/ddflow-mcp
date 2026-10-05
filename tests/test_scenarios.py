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


@pytest.mark.slow
@pytest.mark.scenarios
@pytest.mark.timeout(1800)
def test_crash_recovery_survives_a_check_slower_than_the_lease(tmp_path):
    """B11e64b1e8c: crash-recovery asserted "recover reports nothing yet" inside the 8 s
    lease measured from a heartbeat, so at load average 100-190 the check itself outlived
    the lease and the scenario failed. Simulated here by one `recover` that starts later
    than the TTL -- exactly what a slow process start under load does. The scenario must
    tell an observation made after the lease ran out from a wrong answer, not fail."""
    import scenario_crash_recovery as S
    from harness import Fail, Scenario

    class SlowOnce(Scenario):
        delayed = False

        def ddflow(self, *argv, **kw):
            if argv == ("recover",) and not self.delayed:
                self.delayed = True
                time.sleep(S.TTL_S + 1)
            return super().ddflow(*argv, **kw)

    sc = SlowOnce("crash-recovery-slow", tmp_path / "slow")
    try:
        S.run(sc)
    except Fail as exc:
        pytest.fail(f"crash-recovery under a slow check: {exc}")
    assert sc.delayed, "the slow check was never injected: the scenario changed shape"


@pytest.mark.slow
@pytest.mark.scenarios
@pytest.mark.timeout(1800)
def test_crash_recovery_under_load_where_only_each_check_alone_fits_the_lease(tmp_path):
    """B11e64b1e8c, sustained load: every `claim` and every `recover` is slow enough that
    the two together outlive the lease but each alone does not. Observing both in one
    window failed every attempt; each check gets its own window."""
    import scenario_crash_recovery as S
    from harness import Scenario

    slow = S.TTL_S / 2 + 0.5  # two of these outlive the lease; one leaves 3.5 s

    class Loaded(Scenario):
        slowed: frozenset = frozenset()

        def ddflow(self, *argv, **kw):
            # The two live-window checks: `recover`, and the claim whose answer is read.
            if argv[:1] == ("recover",) or (argv[:1] == ("claim",) and kw.get("expect") is None):
                self.slowed = self.slowed | {argv[0]}
                time.sleep(slow)
            return super().ddflow(*argv, **kw)

    sc = Loaded("crash-recovery-loaded", tmp_path / "loaded")
    try:
        S.run(sc)
    except AssertionError as exc:
        pytest.fail(f"crash-recovery under sustained load: {exc}")
    assert sc.slowed == {"recover", "claim"}, f"load was not injected: {sc.slowed}"
