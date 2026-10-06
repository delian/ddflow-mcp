"""Bug Bf228d082de: the work log folds a fixed bug into its fix task's line by reading the
task's `(fixes bugs ...)` suffix -- and split that list on commas only, so the id after
"and" was never recognised and kept a line of its own. The changelog reads the same
suffix; both now share one parser."""

from __future__ import annotations

from ddflow.core.events import Event
from ddflow.services.export import Filters, query, registry


def _ev(n: int, kind: str, subject: str, ts: str, **data: object) -> Event:
    return Event(kind, subject, data, agent="agent-a", lamport=n, ts=ts)


def test_every_bug_in_an_and_joined_fix_list_rides_on_the_fix_task_line():
    events = [
        _ev(1, "phase.added", "P1", "2026-09-01T08:00:00Z", title="Phase one"),
        _ev(
            2,
            "task.added",
            "T-fix",
            "2026-09-01T08:01:00Z",
            title="Repair three things (fixes bugs B-a, B-b and B-c)",
            parent="P1",
        ),
        *(
            _ev(3 + i, "bug.found", b, "2026-09-01T08:03:00Z", summary=f"{b} broke")
            for i, b in enumerate(("B-a", "B-b", "B-c"))
        ),
        _ev(10, "worktree.merged", "T-fix", "2026-09-20T15:00:00Z", sha="abc"),
        _ev(11, "item.completed", "T-fix", "2026-09-20T15:01:00Z"),
        *(
            _ev(12 + i, "bug.fixed", b, "2026-09-20T15:02:00Z", regression_test="t.py::t")
            for i, b in enumerate(("B-a", "B-b", "B-c"))
        ),
    ]
    body = registry.render_body("worklog", query.build(events), Filters(since="2026-09-20"))
    day = body.split("## 2026-09-20")[1]
    lines = [ln for ln in day.splitlines() if ln.startswith("- ")]
    assert len(lines) == 1, lines  # the three bugs rode on T-fix's line, none on its own
    assert "`T-fix`" in lines[0] and "bug fixed" in lines[0], lines
