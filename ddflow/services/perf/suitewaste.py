"""``ddflow metrics suitewaste``: why whole-suite runs happen, and which share was avoidable.

Folds the event log once (like the swarm baseline, it needs evidence the shared facts do not
carry: the command, the baseline verdict, the tree) and classes every whole-suite run -- a
``ci`` verdict, or a ``unit_tests`` verdict whose command runs the whole ``tests/`` directory:

* ``first_on_tree``: no earlier run covered the same CODE tree;
* ``rerun_same_tree_same_item`` / ``rerun_same_tree_other_item``: an earlier run covered a tree
  whose code paths are equal (``.ddflow/`` state and ``[gates].doc_globs`` documents ignored), by
  the same item (a rework rerun) or by another (a second lane testing the same merged tree);
  both are AVOIDABLE by a result cache (RC-tree-result-cache);
* ``after_baseline_red``: a first run on its tree that follows a verdict the item recorded as
  baseline-red (``partial`` with a ``baseline`` block);
* ``unplaced``: the evidence names no clean commit, or git cannot read it: never counted as
  avoidable, and said so.

Suite minutes are the recorded ``elapsed_s``, queue minutes the recorded ``waited_s`` (host
suite-lock wait). A figure the log cannot give is ``None`` with a reason in ``unavailable``,
never a zero. Falsifier (R-suite-lock-principles-2026-11-12): under 25 percent avoidable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from ...adapters.digest import hasher
from ...config import Config
from ...core import bookkeeping
from ...core import progress as PR
from ...core.events import Event
from ...core.globs import inside

RAN = ("passed", "failed", "partial", "unavailable")
GATES = ("ci", "unit_tests")
#: The classes in report order; the two ``rerun_`` classes are the avoidable ones.
CLASSES = (
    "first_on_tree",
    "rerun_same_tree_same_item",
    "rerun_same_tree_other_item",
    "after_baseline_red",
    "unplaced",
)
#: The verdicts a result cache stores (an unavailable or partial run is never reused).
CACHEABLE = ("passed", "failed")
AVOIDABLE = ("rerun_same_tree_same_item", "rerun_same_tree_other_item")
#: The research's bound: avoidable runs under this share refute the cache-first order.
FALSIFIER_SHARE = 0.25
_WHOLE = re.compile(r"(?:^|\s)tests/?(?:\s|$)")
_SHA = re.compile(r"^([0-9a-f]{7,40})\+clean$")


class TreeProbe(Protocol):
    def code_tree(self, sha: str) -> str | None:
        """A key equal for two commits with equal code paths; None when git cannot say."""


def tree_key(entries: Sequence[tuple], doc_globs: Sequence[str]) -> str:
    """The key of a commit's tree entries ``(mode, blob, path, size)``: state under ``.ddflow/``
    and documents (``[gates].doc_globs``) left out, so an events-only or docs-only commit leaves
    it unchanged."""
    rows = sorted(
        f"{e[0]} {e[1]} {e[2]}"
        for e in entries
        if not bookkeeping.is_state(e[2]) and not any(inside(e[2], g) for g in doc_globs)
    )
    return hasher("\n".join(rows).encode()).hexdigest()


def _num(v: Any) -> float | None:
    ok = isinstance(v, int | float) and not isinstance(v, bool) and v >= 0
    return float(v) if ok else None


def _is_suite(gate: str, ev: dict) -> bool:
    command = ev.get("command")
    if not isinstance(command, str) or not command:
        return False
    return gate == "ci" or bool(_WHOLE.search(command))


def runs(events: Sequence[Event], floor: float | None, now: float) -> list[dict]:
    """The whole-suite runs of the log in time order: item, verdict, sha, minutes, baseline-red."""
    out = []
    for e in events:
        d = e.data if isinstance(e.data, dict) else {}
        verdict = e.kind.partition(".")[2]
        gate = d.get("gate")
        if not e.kind.startswith("gate.") or verdict not in RAN or gate not in GATES:
            continue
        ev = d.get("evidence") if isinstance(d.get("evidence"), dict) else {}
        at = PR.epoch(e.ts)
        if not _is_suite(gate, ev) or at <= 0 or at > now or (floor is not None and at < floor):
            continue
        m = _SHA.match(str(ev.get("tree_sha") or ""))
        out.append(
            {
                "item": e.subject,
                "gate": gate,
                "kind": verdict,
                "at": at,
                "lamport": e.lamport,
                "sha": m.group(1) if m else "",
                "suite_s": _num(ev.get("elapsed_s")),
                "queue_s": _num(ev.get("waited_s")),
                "baseline_red": verdict == "partial" and bool(ev.get("baseline")),
            }
        )
    return sorted(out, key=lambda r: (r["at"], r["lamport"]))


def classify(rows: list[dict], probe: TreeProbe) -> list[dict]:
    """Each run with its ``class``: the first to cover a code tree is first, a later one a rerun."""
    seen: dict[str, set[str]] = {}  # code tree key -> the items whose run covered it
    after_red: set[str] = set()  # items whose last run was baseline-red
    keys: dict[str, str | None] = {}
    out = []
    for r in rows:
        key = None
        if r["sha"]:
            if r["sha"] not in keys:
                keys[r["sha"]] = probe.code_tree(r["sha"])
            key = keys[r["sha"]]
        if key is None:
            cls = "unplaced"
        elif key in seen:
            same = r["item"] in seen[key]
            cls = "rerun_same_tree_same_item" if same else "rerun_same_tree_other_item"
            if r["kind"] in CACHEABLE:
                seen[key].add(r["item"])
        else:
            if r["kind"] in CACHEABLE:  # only a passed/failed result is one a cache could serve
                seen[key] = {r["item"]}
            cls = "after_baseline_red" if r["item"] in after_red else "first_on_tree"
        (after_red.add if r["baseline_red"] else after_red.discard)(r["item"])
        out.append({**r, "class": cls})
    return out


def _minutes(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(sum(known) / 60.0, 2) if known else None


def build(
    events: Sequence[Event],
    cfg: Config,
    now: float,
    probe: TreeProbe,
    *,
    since: float | None = None,
    min_samples: int | None = None,
    in_scope: Callable[[str], bool] | None = None,
) -> dict:
    """The report as plain data."""
    minimum = max(int(cfg.metrics.min_samples if min_samples is None else min_samples), 1)
    scoped = [r for r in runs(events, since, now) if in_scope is None or in_scope(r["item"])]
    rows = classify(scoped, probe)
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["class"]].append(r)
    classes = {
        c: {
            "runs": len(by[c]),
            "suite_minutes": _minutes([r["suite_s"] for r in by[c]]),
            "queue_minutes": _minutes([r["queue_s"] for r in by[c]]),
        }
        for c in CLASSES
    }
    n = len(rows)
    avoidable = sum(classes[c]["runs"] for c in AVOIDABLE)
    share = avoidable / n if n >= minimum else None
    placed = n - classes["unplaced"]["runs"]
    #: The unplaced runs are unknown: they may have re-run a tree, so the share over the placed
    #: runs is the upper bound and the one the falsifier is judged on.
    upper = avoidable / placed if placed >= minimum else None
    unavailable: dict[str, str] = {
        "baseline_measure": "a baseline measurement of a base leaves no gate event in the log",
        "queue_integrator": "the merge queue's runs are recorded as the item's own gate runs",
    }
    if share is None:
        unavailable["avoidable_share"] = f"{n} whole-suite runs, need {minimum}"
    if classes["unplaced"]["runs"]:
        unavailable["unplaced"] = (
            f"{classes['unplaced']['runs']} runs name no clean commit git can read: "
            "not counted as avoidable"
        )
    return {
        "runs": n,
        "avoidable": avoidable,
        "avoidable_share": share,
        "avoidable_share_of_placed": upper,
        "avoidable_suite_minutes": _minutes([r["suite_s"] for c in AVOIDABLE for r in by[c]]),
        "classes": classes,
        "falsifier": {
            "threshold": FALSIFIER_SHARE,
            "holds": None if upper is None else upper >= FALSIFIER_SHARE,
            "claim": (
                "under 25 percent avoidable, even counting only the runs whose tree is known, "
                "refutes the cache-first order"
            ),
        },
        "unavailable": unavailable,
    }


def has_figures(data: dict) -> bool:
    return data["runs"] > 0 and data["avoidable_share"] is not None


def text(data: dict) -> str:
    share = data["avoidable_share"]
    head = "n/a" if share is None else f"{share * 100:.0f}%"
    upper = data["avoidable_share_of_placed"]
    placed = "n/a" if upper is None else f"{upper * 100:.0f}%"
    lines = [
        f"whole-suite runs {data['runs']}, avoidable {data['avoidable']} ({head}; "
        f"{placed} of the runs whose tree is known)"
    ]
    for c, v in data["classes"].items():
        lines.append(
            f"  {c:<28} runs {v['runs']:>4}  suite {_fmt(v['suite_minutes'])}  "
            f"queue {_fmt(v['queue_minutes'])}"
        )
    f = data["falsifier"]
    if f["holds"] is not None:
        verdict = "not refuted" if f["holds"] else "REFUTED"
        lines.append(f"falsifier (avoidable under {f['threshold'] * 100:.0f}%): {verdict}")
    if data["unavailable"]:
        lines.append("unavailable (not zero):")
        lines += [f"  {k}: {why}" for k, why in sorted(data["unavailable"].items())]
    return "\n".join(lines)


def _fmt(minutes: float | None) -> str:
    return "n/a" if minutes is None else f"{minutes:g}m"
