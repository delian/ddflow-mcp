"""`services.planlint`: the mechanical checks `plan lint` runs on the LIVE QUEUE.

The rules the task (DX-queue-lint) states, as regressions:

* an ordering edge is proposed only where a rule decides its direction (fix/DX never
  waits on a feature; otherwise higher priority number waits for lower), the ends pass
  the autoorder ordering guard (settled, decide task, no-auto-order), and adding it
  could not close a needs cycle;
* a `needs` naming a settled or gone item is a drop-able stale edge;
* a DX/fix task at the generic default priority names its band;
* a body mention with no ordering edge is a reference nothing enforces;
* `--fix` applies only the high-confidence ones.
"""

from __future__ import annotations

from conftest import finish, run_cli

from ddflow.services import planlint as PL


def _it(state="open", needs=(), globs=(), tags=(), priority=100, body="", title="t", **kw) -> dict:
    return {
        "id": kw.pop("id", ""),
        "kind": kw.pop("kind", "task"),
        "state": "done" if kw.pop("terminal", False) else state,
        "title": title,
        "body": body,
        "needs": list(needs),
        "globs": list(globs),
        "tags": list(tags),
        "priority": priority,
        "terminal": kw.pop("terminal", state == "done"),
        **kw,
    }


def _b(item_ids) -> dict:
    """The snapshot `_it` makes: the `id` field mirroring what the api passes."""
    return {iid: {**it, "id": iid} for iid, it in item_ids.items()}


def _band(iid: str, tags) -> str:
    if iid.startswith("DX-") or "dx" in set(tags):
        return "dx"
    if any(t in ("bugfix", "hotfix") for t in tags):
        return "bug"
    return ""


def _band_prio(iid: str, tags) -> int:  # noqa: ARG001
    return {"dx": 1, "bug": 5}.get(_band(iid, tags), 100)


def lint(items, scope=None, similar=None):
    snap = _b(items)
    return PL.lint(
        snap,
        scope=scope or [i for i, it in snap.items() if not it["terminal"]],
        band=_band,
        band_priority=_band_prio,
        similar=similar,
    )


def _of(report: PL.Report, kind: str) -> list[PL.Finding]:
    return [f for f in report.findings if f.kind == kind]


def test_a_clean_queue_has_no_findings():
    items = {
        "a": _it(globs=["src/a.py"]),
        "b": _it(needs=["a"], globs=["src/b.py"]),
        "done1": _it(state="done", globs=["src/d.py"]),
    }
    assert lint(items).findings == []


def test_an_overlapping_fix_waits_for_nothing_that_is_a_feature():
    """a bug-fix and a feature touching one file: the FEATURE waits (a fix never needs a
    feature), and that edge is what --fix applies."""
    items = {
        "fix1": _it(tags=["bugfix"], globs=["docs/a.md"], priority=100),
        "feat": _it(globs=["docs/a.md"], priority=50),
    }

    (f,) = _of(lint(items), "overlap")

    assert f.ids == ("feat", "fix1")
    assert f.fix == {"needs_add": ["feat", "fix1"]}


def test_a_feature_pair_ordered_by_priority_is_reported_not_applied():
    """Priority numbers order two FEATURES' proposal, but no fix: the operator orders
    with --priority -- a number nobody typed as a dependency is not one."""
    items = {
        "hi": _it(globs=["docs/a.md"], priority=60),
        "lo": _it(globs=["docs/a.md"], priority=40),
    }

    (f,) = _of(lint(items), "overlap")

    assert f.fix is None
    assert "hi needs lo" in f.detail


def test_equal_priorities_propose_no_direction():
    items = {"a": _it(globs=["docs/a.md"], priority=50), "b": _it(globs=["docs/a.md"], priority=50)}

    (f,) = _of(lint(items), "overlap")

    assert "no proposed edge" in f.detail
    assert f.fix is None


def test_a_decide_task_end_proposes_no_edge_even_for_a_fixed_pair():
    """L-needs-on-decide-tasks: never need a (settled or operating) decide task in an
    ordering edge, so a decide/fix overlap gets reported with no proposed direction."""
    items = {
        "fix1": _it(tags=["bugfix"], globs=["docs/a.md"]),
        "pick": _it(tags=["decide"], globs=["docs/a.md"], priority=50),
    }

    (f,) = _of(lint(items), "overlap")

    assert f.fix is None
    assert "no legal direction" in f.detail


def test_a_no_auto_order_side_keeps_its_pair_report_only():
    items = {
        "fix1": _it(tags=["bugfix", "no-auto-order"], globs=["docs/a.md"]),
        "feat": _it(globs=["docs/a.md"], priority=50),
    }
    assert all(f.fix is None for f in _of(lint(items), "overlap"))


def test_an_order_THROUGH_a_settled_item_is_an_order():
    """A needs X, X is DONE, X needs B: (A, B) overlap but A waits on B through X, so
    there is no finding -- reachability walks THROUGH settled items."""
    items = {
        "a": _it(needs=["x"], globs=["docs/a.md"]),
        "b": _it(globs=["docs/a.md"], priority=10),
        "x": _it(state="done", needs=["b"], globs=["docs/x.md"]),
    }

    assert _of(lint(items, scope=["a", "b"]), "overlap") == []


def test_an_overlapping_pair_with_one_side_real_edge_is_not_reported():
    items = {
        "a": _it(globs=["docs/**"]),
        "b": _it(needs=["a"], globs=["docs/a.md"]),
    }
    assert _of(lint(items), "overlap") == []


def test_a_task_with_no_globs_is_reported_and_not_doubled_as_a_pair():
    items = {
        "n": _it(globs=[]),
        "o": _it(globs=["docs/a.md"], priority=50),
    }

    report = lint(items)

    assert [f.kind for f in report.findings] == ["no_globs"]


def test_a_needs_naming_a_settled_item_is_stale_and_fixable():
    items = {
        "a": _it(needs=["d"], globs=["src/a.py"]),
        "d": _it(state="done", globs=["src/d.py"]),
    }

    (f,) = _of(lint(items), "stale_needs")

    assert f.fix == {"needs_remove": ["d"]}


def test_a_needs_naming_nothing_is_stale_too():
    items = {"a": _it(needs=["ghost"], globs=["src/a.py"])}

    (f,) = _of(lint(items), "stale_needs")

    assert "gone" in f.detail and f.fix == {"needs_remove": ["ghost"]}


def test_needs_of_settled_items_are_not_linted_but_still_walkable():
    """A DONE item holding a stale needs is out of scope; the stale edge of an OPEN one
    still reads through it."""
    items = {
        "a": _it(globs=["src/a.py"]),
        "d": _it(state="done", needs=["ghost"], globs=["src/d.py"]),
    }
    assert _of(lint(items), "stale_needs") == []


def test_a_dx_task_at_the_generic_priority_names_its_band():
    items = {
        "DX-a": _it(globs=["src/a.py"]),
    }

    (f,) = _of(lint(items), "dx_priority")

    assert f.fix == {"priority": 1}
    # A feature at 100 is not a finding: 100 is ITS band.
    assert _of(lint({"f": _it(globs=["src/f.py"])}), "dx_priority") == []


def test_a_body_naming_an_open_unordered_item_is_a_report_only_reference():
    items = {
        "a": _it(globs=["src/a.py"], body="waits on the DWIM of fix-node, then ships"),
        "fix-node": _it(tags=["bugfix"], globs=["src/n.py"], priority=100),
    }

    (f,) = _of(lint(items), "body_ref")

    assert f.ids == ("a", "fix-node")
    assert f.fix is None
    # The ordering edge suppresses the finding: the mention IS the dependency.
    items["a"]["needs"] = ["fix-node"]
    assert _of(lint(items), "body_ref") == []


def test_a_body_still_naming_a_settled_item_is_stale():
    items = {
        "a": _it(globs=["src/a.py"], body="reported before: B-old (then it merged)"),
        "B-old": _it(state="done", globs=["src/b.py"]),
    }
    assert [f.ids for f in _of(lint(items), "stale_ref")] == [("a", "B-old")]


def test_an_ordering_edge_satisfies_a_stale_body_mention_is_no_stale_read():
    """a needs done B-old AND mentions it: mention + needs? the MENTION kind fires only
    for settled items regardless of edges -- a text reference to finished work."""
    items = {
        "a": _it(needs=["B-old"], globs=["src/a.py"], body="done by B-old"),
        "B-old": _it(state="done", globs=["src/b.py"]),
    }
    assert [f.ids for f in _of(lint(items), "stale_ref")] == [("a", "B-old")]


def test_the_add_time_hunt_reports_duplicates_when_provided():
    items = {
        "a": _it(globs=["src/a.py"], title="ship it"),
        "b": _it(globs=["src/b.py"], title="ship it"),
    }

    dup = lint(items, similar=lambda title, body: ["b"] if "ship it" in title else [])

    assert [f.ids for f in _of(dup, "duplicate")] == [("a", "b")]
    assert _of(lint(items), "duplicate") == [], "no hunt provided: no duplicate findings"


def test_plan_lint_on_an_empty_queue_is_nothing_to_do(repo):
    """exit 2, not exit 0: there is no live work to lint, and '0' would claim a clean
    queue for a lint that never looked at one."""
    code, out, _err = run_cli(repo, "plan", "lint")

    assert code == 2


def test_plan_lint_reports_a_stale_needs_and_fix_drops_it(repo):
    """One end to end: a done item still in an open task's needs is a finding (exit 1),
    --fix drops the edge, and the second run is clean (exit 0)."""
    run_cli(repo, "task", "add", "D1", "--title", "done", "--globs", "d.py")
    finish(repo, "D1")
    run_cli(repo, "task", "add", "A1", "--title", "a", "--needs", "D1", "--globs", "a.py")

    code, out, _err = run_cli(repo, "plan", "lint")

    assert code == 1
    assert "stale_needs" in out and "A1 D1" in out

    code, out, _ = run_cli(repo, "plan", "lint", "--fix")

    assert code == 1, out  # the finding existed; the run reports it AND its fixes
    assert "applied" in out and "needs" in out

    code, out, _ = run_cli(repo, "plan", "lint")

    assert code == 0, out
