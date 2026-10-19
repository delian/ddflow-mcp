"""The arithmetic of the roadmap (B-rm-data, D-roadmap): order, lanes, counts, the cap.

Pure over plain rows. `kind_roadmap` reads the Query and the config and calls these; the
rules here are the ones the document states: phases in dependency order then priority, Now
= work in flight, Next = the top N ready phases, Later = everything else, percent and
counts by status per phase, task lists capped (Now / Next only), internal batches hidden.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

#: Defaults for `[export.roadmap]` `next` and `tasks`.
DEFAULT_NEXT = 3
DEFAULT_TASKS = 8
DEFAULT_HIDE = ("BL-",)
#: The order statuses are listed in.
STATUSES = ("running", "review", "ready", "blocked", "done")


def hidden(pid: str, tags: Iterable[str], hide: Iterable[str]) -> bool:
    """A phase is hidden when its id starts with, or one of its tags is, an entry of ``hide``."""
    tagset = set(tags)
    return any(pid.startswith(h) or h in tagset for h in hide if h)


def percent(done: int, total: int) -> int:
    return 100 if total == 0 else done * 100 // total


def counts(rows: Iterable[Mapping[str, Any]], done: int) -> dict[str, int]:
    """Task counts by status, in `STATUSES` order, zero counts left out."""
    n = dict.fromkeys(STATUSES, 0)
    n["done"] = done
    for r in rows:
        n[r["status"]] += 1
    return {k: v for k, v in n.items() if v}


def order(
    ids: Iterable[str], before: Callable[[str], Iterable[str]], key: Callable[[str], Any]
) -> list[str]:
    """``ids`` with what a phase waits on ahead of it, ties by ``key`` (priority, id).
    Layer by layer, so the result is the same on every run; a cycle's members go last."""
    left = sorted(ids, key=key)
    members = set(left)
    deps = {i: {d for d in before(i) if d in members and d != i} for i in left}
    out: list[str] = []
    while left:
        layer = [i for i in left if not deps[i]] or left  # all cyclic: take them as they are
        out += layer
        left = [i for i in left if i not in layer]
        for i in left:
            deps[i] -= set(layer)
    return out


def lane_of(rows: list[Mapping[str, Any]], own_waits: list[str]) -> str:
    """now (anything in flight) | ready (an unblocked task, or an empty phase) | later."""
    if any(r["status"] in ("running", "review") for r in rows):
        return "now"
    if any(r["status"] == "ready" for r in rows) or (not rows and not own_waits):
        return "ready"
    return "later"


def split_lanes(
    phases: list[dict[str, Any]], next_n: int
) -> tuple[list[dict], list[dict], list[dict]]:
    """``(now, next, later)`` from phases already in order, each with its ``lane``. The first
    ``next_n`` ready phases are Next; the others wait in Later, marked ``ready``."""
    now = [p for p in phases if p["lane"] == "now"]
    ready = [p for p in phases if p["lane"] == "ready"]
    nxt = ready[: max(next_n, 0)]
    later = [{**p, "ready": True} for p in ready[len(nxt) :]]
    later += [p for p in phases if p["lane"] == "later"]
    later.sort(key=lambda p: p["rank"])
    return now, nxt, later


def cap_tasks(rows: list[dict[str, Any]], cap: int) -> tuple[list[dict[str, Any]], int]:
    """At most ``cap`` open tasks (none when 0), in-flight ones first; and how many were left out."""
    cap = max(cap, 0)
    if len(rows) <= cap:
        return rows, 0
    ranked = sorted(rows, key=lambda r: r["status"] not in ("running", "review"))
    return ranked[:cap], len(rows) - cap


def by_line(phases: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Phases grouped by release line, in the order the lines first appear; [] when every
    phase is on one line (or none names one)."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for p in phases:
        groups.setdefault(p["line"], []).append(p)
    if len(groups) <= 1:
        return []
    out = []
    for line, ps in groups.items():
        done, total = sum(p["done"] for p in ps), sum(p["total"] for p in ps)
        out.append(
            {"line": line, "phases": [p["id"] for p in ps], "done": done, "total": total,
             "percent": percent(done, total)}
        )  # fmt: skip
    return out
