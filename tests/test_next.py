"""`next --state <state>` lists the items in one report state, machine-readably (NV-next-state-filter).

`ddflow next` prints INTERRUPTED / running / blocked / merged-not-completed COUNTS but had
no flag to LIST items by state; scripts and lanes grep the text ("INTERRUPTED:") to
enumerate them. `--state` returns that state's items as a list (full rows), echoes the
state in `--json`, carries the same `--json`/`--full`/`--cursor` caps, and reaches the MCP
tool too (a `ddflow_next` `state=` property).
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli


def _json_state(repo, state, *argv):
    code, out, err = run_cli(repo, "--json", "next", "--state", state, *argv)
    assert code == 0, f"exit {code}\n{out}\n{err}"
    d = json.loads(out)
    return d, d["items"]


def _next_call(repo, **kw):
    """The `next` command's call in-process (so a clock can be monkeypatched)."""
    from ddflow.surfaces.declared.lifecycle.navigate import COMMANDS

    nxt = next(c for c in COMMANDS if c.path == ("next",))
    return nxt.call(Path(repo), dict(kw), "test-agent")


def test_state_echoed_and_ready_listed(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "a", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--title", "b", "--globs", "b.py")
    d, items = _json_state(repo, "ready")
    assert d["state"] == "ready", d
    assert d["filter"]["state"] == "ready", d
    assert {r["id"] for r in items} == {"A", "B"}, items


def test_state_blocked_lists_blocked_rows(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "a", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--title", "b", "--globs", "b.py")
    run_cli(repo, "block", "B", "--reason", "parked")
    d, items = _json_state(repo, "blocked")
    assert d["state"] == "blocked", d
    blocked = {r["item"] for r in items}
    assert "B" in blocked and "A" not in blocked, blocked
    assert all("item" in r for r in items), items


def test_state_running_lists_live_claims_not_ready(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "a", "--globs", "a.py")
    code, _, err = run_cli(repo, "claim", "A")
    assert code == 0, err
    _d, items = _json_state(repo, "running")
    assert {r["id"] for r in items} == {"A"}, items
    # the default `next` JSON still reports running as ids, unchanged by the state listing.
    d2 = json.loads(run_cli(repo, "--json", "next")[1])
    assert d2["running"] == ["A"], d2["running"]


def test_state_interrupted_lists_expired_running_only(repo, monkeypatch):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "a", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--title", "b", "--globs", "b.py")
    assert run_cli(repo, "claim", "A")[0] == 0
    assert run_cli(repo, "claim", "B")[0] == 0
    # Both are RUNNING for now (live leases): `--state running` lists them, `interrupted`
    # lists none.
    run = {r["id"] for r in _next_call(repo, state="running").data["items"]}
    assert run == {"A", "B"}, run
    assert _next_call(repo, state="interrupted").data["items"] == []
    # Expire both leases: each RUNNING item is now interrupted (no live lease).
    from ddflow.core.schedule import _plan

    monkeypatch.setattr(_plan.time, "time", lambda: 1e12)
    inter = {r["id"] for r in _next_call(repo, state="interrupted").data["items"]}
    assert inter == {"A", "B"}, inter
    run = {r["id"] for r in _next_call(repo, state="running").data["items"]}
    assert run == {"A", "B"}, run


def test_state_running_is_distinct_from_interrupted(repo, monkeypatch):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "a", "--globs", "a.py")
    run_cli(repo, "task", "add", "B", "--title", "b", "--globs", "b.py")
    assert run_cli(repo, "claim", "A")[0] == 0
    assert run_cli(repo, "claim", "B")[0] == 0
    from ddflow.core.schedule import _plan

    monkeypatch.setattr(_plan.time, "time", lambda: 1e12)
    running = {r["id"] for r in _next_call(repo, state="running").data["items"]}
    interrupted = {r["id"] for r in _next_call(repo, state="interrupted").data["items"]}
    assert interrupted == {"A", "B"}, interrupted
    assert running == interrupted  # both leases expired: RUNNING items, no live lease


def test_state_is_paged_and_footer_names_the_state(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "output.max_rows", "5")[0] == 0
    for i in range(12):
        run_cli(repo, "task", "add", f"B{i}", "--title", f"blocked {i}")
        run_cli(repo, "block", f"B{i}", "--reason", "parked")
    d, _items = _json_state(repo, "blocked")
    assert d["state"] == "blocked"
    # The capped list is cut the way `next` cuts its own lists: the total is said, and the
    # footer's next-page call carries the state so the cursor keeps the same listing.
    assert d["count"] == 12, d["count"]
    assert d["truncated"]["lists"]["items"] == 12, d["truncated"]
    assert "state=blocked" in d["next"].get("cursor_mcp", ""), d["next"]


def _state_keys(d):
    """The wire body of a state listing: the state, its items, the count and the filter."""
    return set(d) - {"schema"}


def test_state_leaf_request_maintains_json_schema(repo):
    """`--state` never corrupts the wire body: only the state listing is present."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "A", "--title", "a", "--globs", "a.py")
    d, _ = _json_state(repo, "ready")
    assert _state_keys(d) == {"state", "items", "count", "filter"}, sorted(d)
