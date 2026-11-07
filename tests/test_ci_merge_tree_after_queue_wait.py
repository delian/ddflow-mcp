"""B-ci-merge-tree-stale-after-queue-wait: `ci run` built the merge tree BEFORE it waited for
the host-wide suite slot, so a base that moved while it queued left the tree testing the OLD
base. The tree the checks actually run in must be merged under the held slot."""

from __future__ import annotations

from types import SimpleNamespace as NS

from ddflow.config import Config
from ddflow.services.ci import runner


def test_the_fresh_merge_happens_inside_the_suite_slot(monkeypatch):
    """Fails on the unfixed code: `merge_tree` was entered, then the slot held, so nothing was
    merged while the slot was held -- a base that moved during the wait left the tree stale."""
    events: list[str] = []

    monkeypatch.setattr(runner, "resolve_command", lambda *a: ("hooks", ""))
    monkeypatch.setattr(runner, "tool_missing", lambda c: "")
    monkeypatch.setattr(runner.W, "rev", lambda *a: "abc")
    monkeypatch.setattr(runner, "_merge_base", lambda *a: ("main", None))
    monkeypatch.setattr(runner, "_needs_suite_slot", lambda *a: True)  # a full-suite run
    monkeypatch.setattr(runner, "_stale_pins", lambda *a, **k: "")

    monkeypatch.setattr(
        runner,
        "_execute",
        lambda cfg, tree, cmd, scope, budget, tick, **kw: (
            NS(kind="", ran=True, code=0, output=""),
            "",
        ),
    )

    class Hold:
        def __enter__(self):
            events.append("hold_enter")
            return self

        def __exit__(self, *a):
            events.append("hold_exit")
            return False

    monkeypatch.setattr(runner.SL, "hold", lambda *a, **k: Hold())

    class Tree:
        def __enter__(self):
            events.append("merge_enter")
            return "/tree", None

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(runner, "merge_tree", lambda *a: Tree())

    runner.run("/repo", Config(), command="", base="main")

    hold = events.index("hold_enter")
    fresh_merge_under_the_slot = [e for e in events[hold:] if e == "merge_enter"]
    assert fresh_merge_under_the_slot, f"no merge happened while the suite slot was held: {events}"
