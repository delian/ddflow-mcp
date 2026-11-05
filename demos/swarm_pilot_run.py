#!/usr/bin/env python3
"""The swarm pilot's runner and report (SW-pilot-toy, D-swarm-pilot-2026-11-05).

    python3 demos/swarm_pilot_run.py setup DIR
    python3 demos/swarm_pilot_run.py run DIR --lanes 1 --work-s 2    # the baseline
    python3 demos/swarm_pilot_run.py run DIR --lanes 3 --work-s 2    # a second DIR: the fleet
    python3 demos/swarm_pilot_run.py report BEFORE AFTER

A lane is a thread pulling work the way a real lane does: `next`, `claim`, work in the item's
worktree, gates, `merge`, `complete`, through the real CLI. The baseline is the same runner with
one lane, so the pair differs in nothing but lanes. The only worker here is `StubWorker`: it
sleeps `work_s` and writes a file. Nothing runs a live model, so a stub report proves the harness
and measures ddflow's own overhead; it is not a pilot result. SW-pilot-local brings the real
lanes into a sandbox from `setup` and reads them with `report`.

The report gives agent-hours, not dollars (D-swarm-pilot-2026-11-05): the 1.3x cost target of
D-swarm-answers Q4 is stated in dollars, so it is reported as not measured.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Protocol

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import swarm_pilot_toy as T  # noqa: E402

from ddflow.services import swarm_metrics as S  # noqa: E402

POLL_S = 0.2
MAX_S = 1800.0
#: Named on `complete`; independence is off in the sandbox, so it is a label and nothing else.
AUTHOR_MODEL = "stub-no-model"
NOT_A_RESULT = "stub workers, no live model: this proves the harness, it is not a pilot result"
TEST_BODY = """import unittest


class Toy(unittest.TestCase):
    def test_item(self):
        self.assertEqual({got}, 0)
"""


class RunFailed(Exception):
    """A lane stopped: nothing is forced past it, and the sandbox stays for inspection."""


class Worker(Protocol):
    label: str
    work_s: float

    def work(self, tree: Path, item: T.ToyItem, attempt: int) -> None:
        """Change the item's files in ``tree``; attempt 0 is the first try."""


class StubWorker:
    """No model: sleep for the work and write it, with a failing test while the item is rework."""

    label = "stub"

    def __init__(self, work_s: float) -> None:
        self.work_s = work_s

    def work(self, tree: Path, item: T.ToyItem, attempt: int) -> None:
        time.sleep(self.work_s)
        for name, text in (
            (item.doc, f"# {item.title}\n"),
            (item.test, TEST_BODY.format(got=int(attempt < item.rework))),
        ):
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text(text)


class Board:
    """What the lanes share: which items are taken and done, the deadline, the first failure.

    `taken` covers the gap between `merge` and `complete`: `next` still offers a merged item and
    a claim would rework it, wiping its gates (B-merged-uncompleted-claimable). Real lanes share
    no board, so SW-pilot-local needs that bug fixed."""

    def __init__(self, items: list[str], max_s: float) -> None:
        self.left = set(items)
        self.taken: set[str] = set()
        self.deadline = time.monotonic() + max_s
        self.failure = ""
        self.lock = threading.Lock()

    def take(self, item: str) -> bool:
        """True for the one lane that may try ``item``."""
        with self.lock:
            free = item not in self.taken
            self.taken.add(item)
            return free

    def release(self, item: str) -> None:
        with self.lock:
            self.taken.discard(item)

    def finish(self, item: str) -> None:
        with self.lock:
            self.left.discard(item)

    def fail(self, why: str) -> None:
        with self.lock:
            self.failure = self.failure or why

    def over(self) -> bool:
        """True when a lane should stop; running out of time is a failure of the run."""
        with self.lock:
            if self.left and not self.failure and time.monotonic() >= self.deadline:
                self.failure = f"deadline: {len(self.left)} item(s) left: {sorted(self.left)}"
            return bool(self.failure) or not self.left


def _do_item(sb: T.Sandbox, lane: str, worker: Worker, item: T.ToyItem, tree: Path) -> None:
    """One claimed item through its pipeline, one attempt per trip back from the gate."""
    for attempt in range(item.rework + 1):
        worker.work(tree, item, attempt)
        sb.git("add", "-A", cwd=tree)
        sb.git("commit", "-qm", f"{item.id}: attempt {attempt}", cwd=tree)
        sb.must("gate", "record", item.id, "implement", "--outcome", "passed",
                "--evidence", f"{worker.label} attempt {attempt}", agent=lane)  # fmt: skip
        code, out, err = sb.ddflow("gate", "run", item.id, "unit_tests", agent=lane)
        if code == 0:
            break
        if code != 1 or attempt == item.rework:  # 2 is "could not run", never a failed attempt
            raise RuntimeError(f"{item.id}: unit_tests exit {code}: {err or out}")
    sb.must("merge", item.id, agent=lane)
    sb.must("complete", item.id, "--model", AUTHOR_MODEL, agent=lane)


def _take(sb: T.Sandbox, lane: str, ready: list[str], board: Board) -> tuple[str, Path] | None:
    """Claim the first ready item no other lane has; exit 3 is a refusal, so try the next."""
    for item in ready:
        if not board.take(item):
            continue
        code, out, err = sb.ddflow("claim", item, agent=lane, as_json=True)
        if code == 0:
            return item, Path(json.loads(out)["worktree"])
        board.release(item)
        if code != 3:
            raise RuntimeError(f"claim {item}: exit {code}: {err or out}")
    return None


def _ready(sb: T.Sandbox, lane: str) -> list[str]:
    code, out, err = sb.ddflow("next", "--phase", T.PHASE, agent=lane, as_json=True)
    if code not in (0, 2):  # 2: nothing ready this moment
        raise RuntimeError(f"next: exit {code}: {err or out}")
    return [r["id"] for r in json.loads(out)["ready"]] if out.strip() else []


def _lane(sb: T.Sandbox, lane: str, worker: Worker, board: Board) -> None:
    by_id = {i.id: i for i in T.ITEMS}
    try:
        while not board.over():
            got = _take(sb, lane, _ready(sb, lane), board)
            if got is None:
                time.sleep(POLL_S)
                continue
            _do_item(sb, lane, worker, by_id[got[0]], got[1])
            board.finish(got[0])
    except Exception as exc:  # the lane's thread: whatever stopped it is the run's failure
        board.fail(f"{lane}: {type(exc).__name__}: {exc}")


def run(sb: T.Sandbox, lanes: int, worker: Worker, *, max_s: float = MAX_S) -> None:
    """Work the sandbox's toy phase with ``lanes`` lanes. Once per sandbox; `RunFailed` on a stop."""
    sb.check()
    if lanes < 1:
        raise T.Refused(f"--lanes {lanes}: a run needs at least one lane")
    manifest = sb.manifest()
    if "lanes" in manifest:
        raise T.Refused(f"{sb.repo} already ran: one run per sandbox, so one log is one figure")
    sb.write_manifest(
        manifest
        | {"lanes": lanes, "worker": worker.label, "work_s": worker.work_s, "live_model": False}
    )
    board = Board(manifest["items"], max_s)
    threads = [
        threading.Thread(target=_lane, args=(sb, f"{T.AGENT_PREFIX}lane-{n}", worker, board))
        for n in range(1, lanes + 1)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if board.failure:
        raise RunFailed(board.failure)


def _isolated(sb: T.Sandbox) -> bool:
    """The sandbox's head is unknown to the host, and the sandbox still has no remote."""
    head = sb.git("rev-parse", "main^{commit}").strip()
    known = subprocess.run(
        ["git", "-C", str(T.ROOT), "cat-file", "-e", head],
        capture_output=True,
        text=True,
        check=False,
        env=sb.env,
    )
    if known.returncode not in (0, 1):  # 1 is "no such object"; anything else answered nothing
        raise RuntimeError(f"cannot ask the host about {head}: {known.stderr}")
    return known.returncode == 1 and not sb.git("remote").strip()


def side(sb: T.Sandbox) -> dict:
    """One run's figures from its own log, with what the comparison must know about it."""
    sb.check()
    code, out, err = sb.ddflow("metrics", "swarm", "--phase", T.PHASE, as_json=True)
    if code not in (0, 2):  # 2: too few merged items, and the figures say n/a
        raise RuntimeError(f"metrics swarm: exit {code}: {err or out}")
    manifest = sb.manifest()
    overrides = json.loads(sb.must_json("gate", "list", "--overrides"))["overrides"]
    return {
        **json.loads(out)["metrics"],
        "lanes": manifest.get("lanes"),
        "worker": manifest.get("worker"),
        "live_model": manifest.get("live_model"),
        "work_s": manifest.get("work_s"),
        "items": len(manifest["items"]),
        "overrides": len(overrides),
        "isolated": _isolated(sb),
    }


def _why_not(before: dict, after: dict) -> list[str]:
    why = [
        f"{k} differs: {before[k]} against {after[k]}"
        for k in ("worker", "work_s", "items")
        if before[k] != after[k]
    ]
    lanes = before["lanes"], after["lanes"]
    if not all(type(n) is int and n >= 1 for n in lanes) or lanes[0] >= lanes[1]:
        why.append(f"lanes: {lanes[0]} before and {lanes[1]} after, want fewer before")
    for name, s in (("before", before), ("after", after)):
        if s["merged"] != s["items"]:
            why.append(f"{name}: {s['merged']} of {s['items']} items merged")
        if s["overrides"]:
            why.append(f"{name}: {s['overrides']} override(s): the figures are skewed")
        if not s["isolated"]:
            why.append(f"{name}: not isolated from the host")
    return why


def judge(before: dict, after: dict) -> dict:
    """The pair against the targets; a pair that differs in more than lanes is not judged, so
    its ratios stand but every target verdict is None, in the JSON as in the text."""
    why = _why_not(before, after)
    compared = S.compare(before, after)
    if why:
        compared["targets"] = dict.fromkeys(compared["targets"])
    return {
        "before": before,
        "after": after,
        "compare": compared,
        "comparable": not why,
        "why_not": why,
    }


def _num(value: float | None, unit: str = "", digits: int = 2) -> str:
    return S.UNAVAILABLE if value is None else f"{value:.{digits}f}{unit}"


def _verdict(ok: bool | None) -> str:
    return S.UNAVAILABLE if ok is None else "met" if ok else "NOT MET"


def render(rep: dict) -> str:
    b, a, c = rep["before"], rep["after"], rep["compare"]
    rate = [None if s["rework"]["rate"] is None else s["rework"]["rate"] * 100 for s in (b, a)]
    judged = rep["comparable"]
    thr = _verdict(c["targets"]["throughput"]) if judged else "-"
    rew = _verdict(c["targets"]["rework"]) if judged else "-"
    out = [f"swarm pilot: {b['lanes']} lane(s) against {a['lanes']} lane(s)"]
    if {b["worker"], a["worker"]} == {"stub"}:
        out.append(f"  {NOT_A_RESULT}")
    if not judged:
        out += ["  NOT COMPARABLE, so no target is judged:", *[f"    {w}" for w in rep["why_not"]]]
    head = S.TARGET_THROUGHPUT_X, S.TARGET_REWORK_POINTS
    out += [
        "",
        f"  merged items per hour  {_num(b['throughput']['per_hour'])} -> "
        f"{_num(a['throughput']['per_hour'])}  ({_num(c['throughput_x'], 'x')}, "
        f"target at least {head[0]}x: {thr})",
        f"  rework rate            {_num(rate[0], '%')} -> {_num(rate[1], '%')}  "
        f"({_num(c['rework_points'], ' points')}, target within {head[1]} points: {rew})",
        f"  agent-hours per item   {_num(b['cost']['agent_hours_per_item'], digits=4)} -> "
        f"{_num(a['cost']['agent_hours_per_item'], digits=4)}  "
        f"({_num(c['cost_x'], 'x')} agent-hours)",
    ]
    if "cost" in c["not_measured"]:
        out.append(f"  cost target 1.3x: NOT MEASURED: {c['not_measured']['cost']}")
    else:
        cost = _verdict(c["targets"]["cost"]) if judged else "-"
        out.append(f"  cost target {S.TARGET_COST_X}x per merged item in dollars: {cost}")
    return "\n".join(out)


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").add_argument("dir", type=Path)
    go = sub.add_parser("run")
    go.add_argument("dir", type=Path)
    go.add_argument("--lanes", type=int, default=1)
    go.add_argument("--work-s", type=float, default=2.0, help="the stub's seconds per attempt")
    go.add_argument("--max-s", type=float, default=MAX_S, help="stop the run after this long")
    rep = sub.add_parser("report")
    rep.add_argument("before", type=Path)
    rep.add_argument("after", type=Path)
    rep.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "setup":
            print(f"sandbox ready: {T.setup(a.dir).repo}")
        elif a.cmd == "run":
            run(T.Sandbox.open(a.dir), a.lanes, StubWorker(a.work_s), max_s=a.max_s)
            print(f"ran {a.lanes} lane(s) in {a.dir}")
        else:
            got = judge(side(T.Sandbox.open(a.before)), side(T.Sandbox.open(a.after)))
            print(json.dumps(got, indent=2) if a.json else render(got))
    except T.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except RunFailed as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
