"""Test-polluter bisect (B26): which earlier test file makes another test fail?

A test that passes alone and fails only in a full-suite run is being polluted by something
that ran before it: a leaked environment variable, a module-level cache, a file left in a
shared directory. Finding WHICH earlier file is a search problem, and it is the same one
every time. This is that search, driven by the project's own test command.

**What this is, and the precondition it does not meet.** The backlog entry wanted this "once
ddflow owns test execution", i.e. ddflow running and selecting tests itself. It does not,
and building that is a large design decision (ddflow would have to understand every test
framework). The generic version needs only one thing from the project: a command that runs
a given list of tests and exits non-zero on failure, with a `{tests}` placeholder where the
list goes (``pytest -q {tests}``, ``go test {tests}``, ``npm test -- {tests}``). The search
never looks inside a test: it only asks "does this command, given this list, fail?".

The algorithm is Zeller's delta debugging (ddmin) over the ordered candidate files, with the
victim always last: it returns a 1-minimal set -- removing any single file from it makes the
victim pass -- which is usually one file, and two when the pollution needs an interaction.

Three-valued, like every probe here: a run that could not be made (timeout, spawn failure)
is ``None``, and one of those ends the search as ``unavailable`` rather than being counted
as a pass or a fail.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import proc as P

#: Outcome of one probe: True = the command passed, False = it failed, None = could not run.
Probe = Callable[[list[str]], "bool | None"]

PLACEHOLDER = "{tests}"

#: ddmin starts by halving, and a complement is only worth trying once there are more than
#: two chunks (with two, the complement of one chunk IS the other chunk).
_HALVES = 2


@dataclass
class Run:
    tests: list[str]
    outcome: bool | None
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Bisection:
    """What the search found. ``state`` is one of:

    * ``found``            -- ``polluters`` is a 1-minimal set that makes the victim fail.
    * ``victim_fails_alone`` -- the victim fails by itself: not an ordering problem.
    * ``not_reproducible`` -- the victim passes even after every candidate: nothing to find
      (flaky, or the pollution needs something this command does not do).
    * ``budget_exhausted`` -- ``max_runs`` ran out; ``polluters`` is the smallest set so far.
    * ``unavailable``      -- a probe could not run; the search stopped there.
    """

    state: str
    victim: str
    polluters: list[str] = field(default_factory=list)
    candidates: int = 0
    runs: list[Run] = field(default_factory=list)
    reason: str = ""


class _Stop(Exception):
    """Unwinds the search: a probe could not run, or the budget is spent."""

    def __init__(self, state: str, reason: str) -> None:
        self.state, self.reason = state, reason


def command_probe(
    template: str,
    cwd: Path,
    *,
    timeout_s: float = 600,
    repeat: int = 1,
    runs: list[Run] | None = None,
) -> Probe:
    """A probe that runs `template` (with ``{tests}`` expanded) in `cwd`.

    ``repeat`` > 1 guards a flaky command: a list FAILS if any of the runs fails (a pollution
    that shows 1 time in 3 is still pollution), PASSES only if all pass, and is unknown if
    none failed and one could not run.
    """
    argv_t = shlex.split(template)
    if not any(PLACEHOLDER in a for a in argv_t):
        raise ValueError(f"the command must contain {PLACEHOLDER} where the test list goes")

    def argv_for(tests: list[str]) -> list[str]:
        out: list[str] = []
        for a in argv_t:
            if a == PLACEHOLDER:
                out.extend(tests)
            else:
                out.append(a.replace(PLACEHOLDER, " ".join(tests)))
        return out

    def probe(tests: list[str]) -> bool | None:
        verdicts: list[bool | None] = []
        t0 = time.monotonic()
        detail = ""
        for _ in range(max(1, repeat)):
            try:
                p = P.capture(
                    argv_for(tests),
                    cwd=cwd,
                    encoding="utf-8",
                    errors="replace",  # a stray byte in a test's output is not a failed run
                    timeout=timeout_s,
                )
            except UnicodeError as exc:  # an argv that cannot be encoded
                verdicts.append(None)
                detail = f"could not run: {exc}"
                continue
            if p.timed_out:
                verdicts.append(None)
                detail = f"timed out after {timeout_s:g}s"
                continue
            if p.error is not None:
                verdicts.append(None)
                detail = f"could not run: {p.error}"
                continue
            verdicts.append(p.returncode == 0)
            if p.returncode != 0:
                tail = (p.stdout or p.stderr or "").strip().splitlines()[-1:]
                detail = f"exit {p.returncode}: {tail[0][:160] if tail else ''}"
                break  # one failure is already a FAIL
        if False in verdicts:
            outcome: bool | None = False
        elif None in verdicts:
            outcome = None
        else:
            outcome = True
        if runs is not None:
            runs.append(Run(list(tests), outcome, detail, round(time.monotonic() - t0, 2)))
        return outcome

    return probe


def _partitions(items: list[str], n: int) -> list[list[str]]:
    """`items` cut into `n` contiguous, near-equal, non-empty chunks (order kept)."""
    size, extra = divmod(len(items), n)
    out, i = [], 0
    for k in range(n):
        j = i + size + (1 if k < extra else 0)
        out.append(items[i:j])
        i = j
    return [p for p in out if p]


def ddmin(candidates: list[str], fails: Callable[[list[str]], bool]) -> list[str]:
    """A 1-minimal subsequence of `candidates` for which `fails` still holds.

    `fails(subset)` is True when the victim fails after running exactly `subset`. The
    precondition (`fails(candidates)` is True) is the caller's. Zeller & Hildebrandt's
    algorithm: try each chunk, then each complement, then split finer.
    """
    current = list(candidates)
    n = _HALVES
    while len(current) >= _HALVES:
        chunks = _partitions(current, min(n, len(current)))
        reduced = False
        for chunk in chunks:  # a single chunk is enough
            if fails(chunk):
                current, n, reduced = chunk, _HALVES, True
                break
        if not reduced and len(chunks) > _HALVES:
            start = 0
            for chunk in chunks:  # or everything BUT one chunk (by position, not by value)
                rest = current[:start] + current[start + len(chunk) :]
                start += len(chunk)
                if rest and fails(rest):
                    current, n, reduced = rest, max(n - 1, _HALVES), True
                    break
        if not reduced:
            if n >= len(current):
                break
            n = min(len(current), n * 2)
    return current


def bisect(
    victim: str,
    candidates: list[str],
    probe: Probe,
    *,
    max_runs: int = 200,
    runs: list[Run] | None = None,
) -> Bisection:
    """Find the smallest set of `candidates` that, run before `victim`, makes it fail.

    `probe(tests)` runs `tests` (candidates first, the victim last) and says whether the
    command passed. Candidates keep their given order, which is the suite's order.
    """
    candidates = list(dict.fromkeys(candidates))  # a file listed twice is one file
    res = Bisection(
        "unavailable", victim, candidates=len(candidates), runs=runs if runs is not None else []
    )
    spent = 0

    def run(subset: list[str]) -> bool | None:
        nonlocal spent
        if spent >= max_runs:
            raise _Stop("budget_exhausted", f"{max_runs} runs spent")
        spent += 1
        return probe([*subset, victim])

    def fails(subset: list[str]) -> bool:
        outcome = run(subset)
        if outcome is None:
            raise _Stop("unavailable", "a run could not be made (timeout or spawn failure)")
        return outcome is False

    best: list[str] = []
    try:
        alone = run([])
        if alone is None:
            raise _Stop("unavailable", "the victim could not be run alone")
        if alone is False:
            res.state, res.reason = (
                "victim_fails_alone",
                "the victim fails by itself: not an ordering problem",
            )
            return res
        if not candidates:
            res.state, res.reason = "not_reproducible", "no candidate files precede the victim"
            return res
        if not fails(candidates):
            res.state, res.reason = (
                "not_reproducible",
                "the victim passes after every candidate: nothing to bisect (flaky, or the "
                "pollution is not about earlier files)",
            )
            return res
        # At this point we've verified that all candidates together make the victim fail
        best = list(candidates)

        def tracked(subset: list[str]) -> bool:
            nonlocal best
            hit = fails(subset)
            if hit and len(subset) < len(best):
                best = list(subset)
            return hit

        best = ddmin(list(candidates), tracked)
        res.state, res.polluters = "found", best
        res.reason = f"{len(best)} of {len(candidates)} files make {victim} fail"
    except _Stop as stop:
        res.state, res.reason = stop.state, stop.reason
        if stop.state == "budget_exhausted":
            res.polluters = best
    return res
