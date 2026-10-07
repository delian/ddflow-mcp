"""Hypothesis strategies for the property tests: event logs over a small id universe.

Small on purpose. A handful of phases, tasks, bugs and agents makes events COLLIDE --
the same item claimed by two agents, a renewal folding after a release, a completion of a
removed task -- which is where a fold or a scheduler goes wrong. A wide universe would
mostly generate events about unrelated ids that never meet.

Every payload uses the keys the real writers use (`api/` and `services/`), so a property
that fails here fails on a shape the log can actually hold.
"""

from __future__ import annotations

import dataclasses

from hypothesis import strategies as st

from ddflow.core.events import Event

PHASES = ["P1", "P2"]
TASKS = ["T1", "T2", "T3", "T4", "T5"]
ITEMS = PHASES + TASKS
BUGS = ["B1", "B2"]
AGENTS = ["a1", "a2", "a3"]
GLOBS = ["src/a.py", "src/b.py", "src/**", "docs/", "README.md", "CHANGELOG.md", "tests/t_*.py"]
GATES = ["research", "implement", "critic", "unit_tests", "merge"]

#: A lease time is seconds from an arbitrary epoch; the scheduler tests set `now` beside it.
EPOCH = 1_000_000.0

_text = st.text(alphabet="abcxyz ", min_size=0, max_size=8)


def _ts(lamport: int) -> str:
    """A timestamp that grows with the Lamport clock, as the real writers' do."""
    return f"2026-01-01T{lamport // 3600 % 24:02d}:{lamport // 60 % 60:02d}:{lamport % 60:02d}Z"


def _sub(pool: list[str], max_size: int = 3):
    return st.lists(st.sampled_from(pool), max_size=max_size, unique=True)


_task = st.sampled_from(TASKS)
_parent = st.sampled_from(["", *PHASES])


@st.composite
def _definitions(draw) -> tuple[str, str, dict]:
    kind = draw(st.sampled_from(["phase.added", "task.added", "task.added", "task.updated"]))
    if kind == "phase.added":
        data = {"title": draw(_text), "needs": draw(_sub(ITEMS, 1)), "globs": []}
        return kind, draw(st.sampled_from(PHASES)), data
    values = {
        "title": _text,
        "parent": _parent,
        "needs": _sub(ITEMS),
        "globs": _sub(GLOBS),
        "priority": st.integers(0, 200),
    }
    if kind == "task.updated":
        keys = draw(_sub(list(values), 3))
        return kind, draw(_task), {k: draw(values[k]) for k in keys}
    data = {k: draw(v) for k, v in values.items()}
    if draw(st.booleans()):
        data["tags"] = draw(_sub(["no-worktree", "bug", "x"], 2))
    return kind, draw(_task), data


@st.composite
def _leases(draw) -> tuple[str, str, dict]:
    kind = draw(
        st.sampled_from(["lease.acquired", "lease.acquired", "lease.renewed", "lease.released"])
        | st.just("lease.expired")
    )
    data: dict = {"holder": draw(st.sampled_from(AGENTS)), "kind": "task"}
    if kind in ("lease.acquired", "lease.renewed") or draw(st.booleans()):
        data["at"] = EPOCH + draw(st.integers(0, 6000))
    if kind == "lease.acquired":
        data["ttl_s"] = draw(st.sampled_from([600, 1800]))
        data["globs"] = draw(_sub(GLOBS))
        data["worktree"] = draw(st.sampled_from(["", "/w/tree"]))
    return kind, draw(_task), data


@st.composite
def _transitions(draw) -> tuple[str, str, dict]:
    kind = draw(
        st.sampled_from(
            ["started", "completed", "blocked", "unblocked", "abandoned", "reopened", "removed"]
        )
    )
    if kind == "removed":
        return "task.removed", draw(_task), {}
    data = {"kind": "task"}
    if kind in ("blocked", "abandoned", "reopened"):
        data["reason"] = draw(_text)
    if kind == "completed":
        data["sha"] = draw(st.sampled_from(["", "abc1234"]))
    return f"item.{kind}", draw(_task), data


@st.composite
def _records(draw) -> tuple[str, str, dict]:
    kind = draw(st.sampled_from(["gate", "bug.found", "bug.fixed", "bug.invalid", "lesson"]))
    if kind == "gate":
        outcome = draw(st.sampled_from(["started", "passed", "failed", "unavailable", "skipped"]))
        return f"gate.{outcome}", draw(_task), {"gate": draw(st.sampled_from(GATES))}
    bug = draw(st.sampled_from(BUGS))
    if kind == "bug.found":
        return kind, bug, {"summary": draw(_text), "item": draw(_task)}
    if kind == "bug.fixed":
        return kind, bug, {"regression_test": "tests/test_x.py::test_y"}
    if kind == "bug.invalid":
        return kind, bug, {"reason": draw(_text)}
    data = {"title": draw(_text), "rule": draw(_text)}
    return "lesson.recorded", draw(st.sampled_from(["L1", "L2"])), data


def payloads() -> st.SearchStrategy[tuple[str, str, dict]]:
    """One (kind, subject, data) a real writer could have appended."""
    return st.one_of(_definitions(), _leases(), _transitions(), _records())


def event(kind: str, subject: str, data: dict, agent: str, lamport: int) -> Event:
    """An event exactly as `EventLog.append` would have stored it: id filled in."""
    ev = Event(kind=kind, subject=subject, data=data, agent=agent, lamport=lamport, ts=_ts(lamport))
    return dataclasses.replace(ev, id=ev.compute_id())


@st.composite
def event_logs(draw, max_size: int = 40) -> list[Event]:
    """A merged log in the order `read_all` returns it: several agents' shards, each with
    its own Lamport clock (clones that did not see each other can reuse a value), sorted by
    (lamport, agent, id) and de-duplicated."""
    n = draw(st.integers(0, max_size))
    clocks = dict.fromkeys(AGENTS, 0)
    out: list[Event] = []
    for _ in range(n):
        agent = draw(st.sampled_from(AGENTS))
        # Mostly ahead of every clock (one clone), sometimes only ahead of its own (a
        # clone that had not pulled): both shapes reach the merged log.
        floor = max(clocks.values()) if draw(st.booleans()) else clocks[agent]
        clocks[agent] = floor + draw(st.integers(1, 3))
        kind, subject, data = draw(payloads())
        out.append(event(kind, subject, data, agent, clocks[agent]))
    return sorted(out, key=Event.sort_key)
