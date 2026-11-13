"""Judge one live lane run from what it left behind (SW-live-smoke, D-swarm-pilot-2026-11-05).

Not a runner, and it reaches no model: a lane is started by the Lane contract (``lanes.launch``,
``conformance.run``), and the planned ``agent_smoke`` of B-ac-smoke stays the one runner. This
is the judgement that task plans, ready for it to adopt: assert on the event log, the tree and
what ddflow answered, never on what the lane says it did; pass, fail or unavailable, unavailable
never a pass; several runs decided by majority.

``read_stream`` reads the ``run --format json`` lines of the opencode family, in the shape read
on a real run (docs/ddflow/research/swarm-smoke.md). It separates what the conformance grade
cannot see: a ddflow refusal arrives as a ``completed`` call, a lease hook refusal as an
``error`` call. Both are well formed calls that were declined, not unreliable ones.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ...core.model import GATE_OUTCOMES
from ..swarm import conformance as cf

PASSED, FAILED, UNAVAILABLE = cf.PASSED, cf.FAILED, cf.UNAVAILABLE
#: What became of one tool call.
OK, REFUSED, GUARDED, BAD = "ok", "refused", "guarded", "bad"
STEPS = ("claim", "edit", "commit", "heartbeat", "gate_record", "stop")
#: The sentences of `lanehooks_check.verdict`, the first one meaning the lease has ended.
LEASE_GONE = "holds no live lease"
GUARD_MARKS = (LEASE_GONE, "outside the item's lease globs", "ddflow lane hook")
#: What `_refusal` says of a heartbeat answering ``renewed: false``.
NOT_RENEWED = "the heartbeat renewed nothing"
#: Harness tools that change a file through a path the lease hook can judge.
WRITE_TOOLS = frozenset({"write", "edit", "multiedit", "patch"})
#: Events that mean a lane went past its brief: finishing the item is not a lane's to do here.
PAST_THE_LEASE = frozenset({"item.completed", "worktree.merged"})
_GATE_KINDS = frozenset(f"gate.{o}" for o in GATE_OUTCOMES)


@dataclass(frozen=True)
class Call:
    name: str  #: the tool, ddflow prefixes removed (`conformance.norm_tool`)
    action: str
    status: str  #: ok | refused | guarded | bad
    detail: str = ""
    step: int = 0  #: the model turn it came in; calls of one turn were sent together


@dataclass(frozen=True)
class Stream:
    calls: tuple[Call, ...]
    tokens: int  #: summed `step_finish` totals
    unreadable: int  #: lines that were not a JSON object


@dataclass(frozen=True)
class Reliability:
    calls: int
    bad: int

    @property
    def rate(self) -> float | None:
        return (self.calls - self.bad) / self.calls if self.calls else None


@dataclass(frozen=True)
class Check:
    step: str
    status: str
    evidence: str


@dataclass(frozen=True)
class Verdict:
    checks: tuple[Check, ...]
    reliability: Reliability

    @property
    def outcome(self) -> str:
        got = {c.status for c in self.checks}
        return FAILED if FAILED in got else UNAVAILABLE if UNAVAILABLE in got else PASSED


def is_guard_refusal(text: str) -> bool:
    return any(mark in text for mark in GUARD_MARKS)


def _refusal(output: Any) -> str:
    """The reason of a ddflow refusal in a tool's output, or "". A heartbeat that renewed nothing
    is one: it exits 2, not 3, but the lane has no lease to go on with. The refusal JSON may
    follow leading prose (a warning line, braces in prose included) or sit behind an earlier,
    non-refusal object; only its own text matters."""
    if not isinstance(output, str):
        return ""
    text = output.lstrip()
    decoder = json.JSONDecoder()
    while True:
        first = text.find("{")
        if first < 0:
            return ""
        try:
            head, end = decoder.raw_decode(text[first:])
        except ValueError:  # a '{' in prose, not JSON: scan past it
            text = text[first + 1 :]
            continue
        if isinstance(head, dict):
            if head.get("renewed") is False:
                return NOT_RENEWED
            got = head.get("refusal")
            if isinstance(got, dict):
                return str(got.get("reason") or "refused")
        # a real object, but not a refusal: scan past it. `raw_decode` always consumes at
        # least one character of a value it decoded; the `max` keeps progress guaranteed
        # even if a pathological decoder ever reported none.
        text = text[first + max(end, 1) :]


def _call(part: dict[str, Any], step: int) -> Call:
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    args = state.get("input") if isinstance(state.get("input"), dict) else {}
    name, action = cf.norm_tool(part.get("tool", "")), str(args.get("action") or "")
    if state.get("status") != "completed":
        why = str(state.get("error") or state.get("status") or "no status")
        return Call(name, action, GUARDED if is_guard_refusal(why) else BAD, why, step)
    if why := _refusal(state.get("output")):
        return Call(name, action, REFUSED, why, step)
    return Call(name, action, OK, "", step)


def _row(line: str) -> dict[str, Any] | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def read_stream(lines: Iterable[str]) -> Stream:
    calls: list[Call] = []
    tokens = unreadable = step = 0
    for line in lines:
        if not line.strip():
            continue
        row = _row(line)
        if row is None:
            unreadable += 1
            continue
        part = row.get("part") if isinstance(row.get("part"), dict) else {}
        if row.get("type") == "step_start":
            step += 1
        elif row.get("type") == "tool_use":
            calls.append(_call(part, step))
        elif row.get("type") == "step_finish":
            spent = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            total = spent.get("total")
            tokens += total if isinstance(total, int) and not isinstance(total, bool) else 0
    return Stream(tuple(calls), tokens, unreadable)


def reliability(calls: Sequence[Call]) -> Reliability:
    """Well formed is every call the harness could run; declined by a guard or by ddflow still is."""
    return Reliability(len(calls), sum(c.status == BAD for c in calls))


def _logged(step: str, hits: list[Any], kinds: str) -> Check:
    if hits:
        return Check(step, PASSED, f"{len(hits)} {kinds} event(s) under the lane's identity")
    return Check(step, FAILED, f"no {kinds} event under the lane's identity")


def _edit(calls: Sequence[Call]) -> Check:
    done = [c for c in calls if c.name in WRITE_TOOLS and c.status == OK]
    if done:
        return Check("edit", PASSED, f"{len(done)} file write(s) went through")
    return Check("edit", FAILED, "no file write went through")


def _heartbeat(calls: Sequence[Call]) -> Check:
    """From the answer ddflow gave, not the log: a renewal soon after the claim is coalesced out
    of it by design (`logcoalesce`), and the claim's own `lease.renewed` would pass for one."""
    done = [c for c in calls if c.name == "heartbeat"]
    if any(c.status == OK for c in done):
        return Check("heartbeat", PASSED, f"{len(done)} heartbeat call(s), the lease renewed")
    return Check("heartbeat", FAILED, f"{len(done)} heartbeat call(s), none renewed the lease")


def _commit(commits: int | None) -> Check:
    if commits is None:
        return Check("commit", UNAVAILABLE, "the branch could not be read")
    if commits < 1:
        return Check("commit", FAILED, "no commit ahead of the base")
    return Check("commit", PASSED, f"{commits} commit(s) ahead of the base")


def _stop(calls: Sequence[Call], past: list[Any], exit_code: int | None) -> Check:
    if past:  # proved by the log, whether or not the exit was seen
        return Check("stop", FAILED, f"the lane went past its brief: {past[0].kind}")
    gone = next((c.step for c in calls if LEASE_GONE in c.detail or c.detail == NOT_RENEWED), None)
    after = [c for c in calls if gone is not None and c.step > gone]
    if after:
        return Check("stop", FAILED, f"{len(after)} call(s) in later turns, after the lease ended")
    if exit_code is None:
        return Check("stop", UNAVAILABLE, "the lane's exit was not observed")
    if exit_code != 0:
        return Check("stop", FAILED, f"the lane exited {exit_code}")
    return Check("stop", PASSED, "exited by itself and left the item to the lead")


def judge(
    stream: Stream,
    events: Iterable[Any] | None,
    *,
    item: str,
    lane: str,
    commits: int | None,
    exit_code: int | None,
) -> Verdict:
    """One run. ``events`` is the event log (``None`` when it could not be read), ``commits`` the
    branch's count ahead of its base (``None`` when unread), ``exit_code`` the lane's (``None``
    when it was not seen to end). An unreadable log grades the claim and gate-record steps
    unavailable, not failed: a torn or missing log never reads as a failed run."""
    own = [e for e in events if e.subject == item and e.agent == lane] if events is not None else []

    def of(*kinds: str) -> list[Any]:
        return [e for e in own if e.kind in kinds]

    def logged(step: str, hits: list[Any], what: str) -> Check:
        if events is None:
            return Check(step, UNAVAILABLE, "the event log could not be read")
        return _logged(step, hits, what)

    return Verdict(
        (
            logged("claim", of("lease.acquired"), "lease.acquired"),
            _edit(stream.calls),
            _commit(commits),
            _heartbeat(stream.calls),
            logged("gate_record", of(*_GATE_KINDS), "gate outcome"),
            _stop(stream.calls, of(*PAST_THE_LEASE), exit_code),
        ),
        reliability(stream.calls),
    )


def majority(outcomes: Sequence[str]) -> str:
    """The result of several runs. Unavailable runs are no vote, but at least half of the runs
    must have been judged, or nothing is decided. Among the judged, more than half passed is a
    pass and anything else, a tie included, a failure."""
    passed, failed = outcomes.count(PASSED), outcomes.count(FAILED)
    votes = passed + failed
    if votes == 0 or votes * 2 < len(outcomes):
        return UNAVAILABLE
    return PASSED if passed * 2 > votes else FAILED
