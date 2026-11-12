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
import shlex
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
#: The pytest options a whole-suite command may carry: they change how the run executes, never
#: which tests it selects. A flag outside these makes the command unrecognised (not counted),
#: never guessed whole.
_PLAIN_FLAGS = ("-q", "-qq", "-v", "-vv", "-x", "--no-header", "-ra", "-rA", "--strict-markers")
#: Options that take a value (next argument, or ``=value``; ``-n24`` for ``-n``).
_VALUE_FLAGS = ("-n", "-p", "--timeout", "--tb", "--maxfail", "--durations", "--dist")
_ROOT = re.compile(r"^(\./)?tests/?$")
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


def _whole_command(command: str) -> bool:
    """Does this pytest command run the whole ``tests/`` directory? True only for the root path
    (``tests``, ``tests/``, ``./tests``) with nothing but the known execution flags; any other
    option or path leaves the command unrecognised, so it is not counted (never guessed)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    cut = next((i for i, t in enumerate(tokens) if t.rsplit("/", 1)[-1] == "pytest"), None)
    if cut is None:
        return False
    root, skip = False, False
    for a in tokens[cut + 1 :]:
        if skip:
            skip = False
        elif a in _VALUE_FLAGS:
            skip = True
        elif a in _PLAIN_FLAGS or a.split("=", 1)[0] in _VALUE_FLAGS:
            continue
        elif re.fullmatch(r"-n\S+", a):
            continue
        elif _ROOT.match(a):
            root = True
        else:
            return False
    return root


def _is_suite(gate: str, ev: dict) -> bool:
    """A ``ci`` verdict is a whole-suite run; a ``unit_tests`` verdict only when its recorded
    command runs the whole directory (a scoped or selected run is not)."""
    if gate == "ci":
        return True
    command = ev.get("command")
    return isinstance(command, str) and _whole_command(command)


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


def _minutes(values: list[float | None], *, whole: bool = False) -> float | None:
    """Minutes of the runs that recorded a time; None when none did, and with ``whole`` also None
    when any run did not (a partial sum is never the total). The count of runs without a time is
    reported beside it (``untimed``)."""
    known = [v for v in values if v is not None]
    if whole and len(known) < len(values):
        return None
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
    # Classify the whole log: a run by another item is still the earlier run that makes an
    # in-scope run a rerun; the scope only picks the rows that are reported.
    rows = [
        r
        for r in classify(runs(events, since, now), probe)
        if in_scope is None or in_scope(r["item"])
    ]
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["class"]].append(r)
    classes = {
        c: {
            "runs": len(by[c]),
            #: None when any run of the class recorded no time: a partial sum is not the total.
            "suite_minutes": _minutes([r["suite_s"] for r in by[c]], whole=True),
            "suite_minutes_timed": _minutes([r["suite_s"] for r in by[c]]),
            "queue_minutes": _minutes([r["queue_s"] for r in by[c]], whole=True),
            "untimed": sum(r["suite_s"] is None for r in by[c]),
        }
        for c in CLASSES
    }
    n = len(rows)
    avoidable = sum(classes[c]["runs"] for c in AVOIDABLE)
    share = avoidable / n if n >= minimum else None
    #: The unplaced runs are unknown: any of them may have re-run a tree. ``share`` counts
    #: none of them avoidable (the lower bound), ``upper`` counts all of them (the upper).
    upper = (avoidable + classes["unplaced"]["runs"]) / n if n >= minimum else None
    unavailable: dict[str, str] = {
        "baseline_measure": "a baseline measurement of a base leaves no gate event in the log",
        "queue_integrator": "the merge queue's runs are recorded as the item's own gate runs",
    }
    if share is None:
        unavailable["avoidable_share"] = f"{n} whole-suite runs, need {minimum}"
    if classes["unplaced"]["runs"]:
        unavailable["unplaced"] = (
            f"{classes['unplaced']['runs']} runs name no clean commit git can read: "
            "none counted in the share, all counted in the upper bound"
        )
    return {
        "runs": n,
        "avoidable": avoidable,
        "avoidable_share": share,
        "avoidable_share_max": upper,
        "avoidable_suite_minutes": _minutes(
            [r["suite_s"] for c in AVOIDABLE for r in by[c]], whole=True
        ),
        "classes": classes,
        "falsifier": _falsifier(share, upper),
        "unavailable": unavailable,
    }


def _falsifier(lower: float | None, upper: float | None) -> dict:
    """R-suite-lock-principles: the cache-first order is refuted when even the upper bound is
    under the threshold, stands when the lower bound reaches it, else undetermined."""
    if lower is None or upper is None:
        holds = None
    elif upper < FALSIFIER_SHARE:
        holds = False
    elif lower >= FALSIFIER_SHARE:
        holds = True
    else:
        holds = None
    return {
        "threshold": FALSIFIER_SHARE,
        "holds": holds,
        "verdict": {True: "stands", False: "refuted", None: "undetermined"}[holds],
        "claim": "avoidable share under 25 percent (upper bound) refutes the cache-first order",
    }


def has_figures(data: dict) -> bool:
    return data["runs"] > 0 and data["avoidable_share"] is not None


def text(data: dict) -> str:
    share = data["avoidable_share"]
    head = "n/a" if share is None else f"{share * 100:.0f}%"
    upper = data["avoidable_share_max"]
    top = "n/a" if upper is None else f"{upper * 100:.0f}%"
    lines = [
        f"whole-suite runs {data['runs']}, avoidable {data['avoidable']} "
        f"({head}, at most {top} if every unplaced run re-ran a tree)"
    ]
    for c, v in data["classes"].items():
        lines.append(
            f"  {c:<28} runs {v['runs']:>4}  suite {_fmt(v['suite_minutes'])}  "
            f"queue {_fmt(v['queue_minutes'])}"
            + (
                f"  ({v['untimed']} untimed; the timed runs alone {_fmt(v['suite_minutes_timed'])})"
                if v["untimed"]
                else ""
            )
        )
    f = data["falsifier"]
    lines.append(f"falsifier (avoidable under {f['threshold'] * 100:.0f}%): {f['verdict']}")
    if data["unavailable"]:
        lines.append("unavailable (not zero):")
        lines += [f"  {k}: {why}" for k, why in sorted(data["unavailable"].items())]
    return "\n".join(lines)


def _fmt(minutes: float | None) -> str:
    return "n/a" if minutes is None else f"{minutes:g}m"
