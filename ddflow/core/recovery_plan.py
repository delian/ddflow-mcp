"""The pure triage behind `ddflow recover --all --plan` (DX-recovery-plan).

One engine, many triggers (orphan-recovery.md research section 3): a single pure
function maps the MEASURED facts -- the lease sweep's rows, the health findings, orphaned
gate-run markers, unended jobs and merge-queue entries a dead integrator left running --
to one `Action` per item. Each action is one of three classes, and the class is the
whole safety contract:

* SAFE_AUTO -- idempotent, loses nothing, provable from the facts alone. `services.
  recovery` may apply it; `recover --apply` does.
* PROPOSE   -- needs a new agent or an operator decision; the plan shows the exact
  command, nothing runs it.
* OPERATOR  -- only a person decides; never done by a machine.

No state, no clock of its own, no I/O: `now` and every fact come in with the call, the
same style as `core.health`, so the plan can be unit-tested without a repository.

The attempt counter is a fold the CALLER makes (today the `lease.expired` count per
item; `DX-recovery-guards` upgrades it to `recovery.resumed` and the `[recover]` knobs);
the cap comes in as `Limits` with the same default the research fixed (2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SAFE_AUTO, PROPOSE, OPERATOR = "safe_auto", "propose", "operator"
CLASSES = (SAFE_AUTO, PROPOSE, OPERATOR)
_PRIORITY = {SAFE_AUTO: 0, PROPOSE: 1, OPERATOR: 2}

#: The default cap, until `DX-recovery-guards` wires `[recover].max_attempts`
#: (orphan-recovery.md research, Guards against loops).
MAX_ATTEMPTS_DEFAULT = 2


@dataclass(frozen=True)
class Limits:
    """The guards the triage reads. Shipped values are the research defaults."""

    max_attempts: int = MAX_ATTEMPTS_DEFAULT


@dataclass(frozen=True)
class Facts:
    """Everything measured, already shaped as plain rows by `services.recovery`.

    * ``recoveries``  one row per `leases.scan` entry: keys item, kind, worktree,
      salvageable (True/False/None), adopted, unmerged_commits, holder, advice, age_s.
    * ``dead``        one row per orphaned gate-run marker: keys item, gate, pid, at, sha.
    * ``jobs``        one row per unended job worth judging: keys id, item, state,
      exit_code (state drawn from `services.jobs.status`).
    * ``queue``       the ids of queue entries left `running` (the integrator died).
    * ``attempts``    item -> recovery attempts recorded so far (expired-count today).
    * ``kill_switch`` the recovery kill switch file exists (the apply path refuses).
    """

    recoveries: tuple[dict[str, Any], ...] = ()
    dead: tuple[dict[str, Any], ...] = ()
    jobs: tuple[dict[str, Any], ...] = ()
    queue: tuple[str, ...] = ()
    attempts: dict[str, int] = field(default_factory=dict)
    kill_switch: bool = False


@dataclass(frozen=True)
class Action:
    """One item, one class, one exact command, the evidence, the attempt count."""

    item: str
    cls: str
    command: str
    reason: str
    attempts: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "cls": self.cls,
            "command": self.command,
            "reason": self.reason,
            "attempts": self.attempts,
            "evidence": self.evidence,
        }


def _expiry(r: dict[str, Any], attempts: int) -> Action:
    what = "its tree was measured clean" if r.get("salvageable") is False else "nothing to lose"
    return Action(
        r["item"],
        SAFE_AUTO,
        f"record `lease.expired` for {r['holder']}'s lease (the tree holds no work; "
        "only the claim is freed)",
        f"lease lapsed {r.get('age_s', 0.0):.0f}s ago and {what}; the worktree exists "
        f"nowhere needed — expiry lets the next `ddflow claim {r['item']}` take over",
        attempts,
        {"effect": "expire", "holder": r.get("holder"), "worktree": r.get("worktree", "")},
    )


def _capped(item: str, attempts: int, limits: Limits) -> Action:
    """The one OPERATOR action for the cap (roborev: it lived twice and drifted)."""
    return Action(
        item,
        OPERATOR,
        f"ddflow block {item} --reason 'recovery attempt cap "
        f"({limits.max_attempts}) reached'",
        f"{attempts} recovery attempt(s), the cap is {limits.max_attempts}; "
        "only the operator settles it. <operator> settle, re-plan, or abandon",
        attempts,
        {"effect": "block", "cap": limits.max_attempts},
    )


def _resume(r: dict[str, Any], attempts: int) -> Action:
    force = ""
    if r.get("kind") == "expired_lease" and r.get("unmerged_commits", 0):
        force = " --force"
    return Action(
        r["item"],
        PROPOSE,
        f"ddflow claim {r['item']}{force}",
        (
            f"{r.get('unmerged_commits', 0)} unmerged commit(s) and the worktree are kept"
            if r.get("salvageable")
            else "the tree could not be measured; nothing is touched automatically"
        )
        + f"; {r.get('advice') or 'the next agent claims it'}",
        attempts,
        {"effect": "resume", "holder": r.get("holder", ""), "worktree": r.get("worktree", "")},
    )


def _recovery_actions(facts: Facts, limits: Limits) -> list[Action]:
    out: list[Action] = []
    # The cap gates RESUME- and EXPIRY-shaped recoveries. A clean orphan is never
    # capped: its cleanup loses nothing whatever the expire history, and describing it
    # as a spent attempt would mislabel a machine-cleanable tree (roborev 9b01ab5).
    # Actions emitted THIS pass count too, so repeated rows for one item cannot
    # advertise a second expiry in a single run.
    spent: dict[str, int] = {}
    capped: set[str] = set()  # one capped line per item; later rows for it say nothing
    for r in facts.recoveries:
        if r["item"] in capped:
            continue
        attempts = facts.attempts.get(r["item"], 0) + spent.get(r["item"], 0)
        if r.get("kind") == "expired_lease":
            if attempts >= limits.max_attempts:
                out.append(_capped(r["item"], attempts, limits))
                capped.add(r["item"])  # a shown line is not an attempt: never counted
            elif r.get("salvageable") is False:
                out.append(_expiry(r, attempts))
                spent[r["item"]] = spent.get(r["item"], 0) + 1
            else:
                out.append(_resume(r, attempts))
                spent[r["item"]] = spent.get(r["item"], 0) + 1
        elif r.get("salvageable") is False:
            # A clean orphan / stale RUNNING item: nothing to lose, but taking it is
            # the operator's or the next agent's call -- shown, never done. The command
            # is the verbatim executable one; the item is named in the reason.
            out.append(
                Action(
                    r["item"],
                    PROPOSE,
                    "ddflow cleanup --apply",
                    f"{r['item']}'s tree is clean and no lease is held; cleanups raise "
                    + (r.get("advice") or "nothing else"),
                    attempts,
                    {"effect": "cleanup", "kind": r.get("kind"), "worktree": r.get("worktree", "")},
                )
            )
        elif attempts >= limits.max_attempts:
            out.append(_capped(r["item"], attempts, limits))
        else:
            out.append(_resume(r, attempts))
            spent[r["item"]] = spent.get(r["item"], 0) + 1
    return out


def _dead_run_actions(rows: tuple[dict[str, Any], ...]) -> list[Action]:
    out: list[Action] = []
    for r in rows:
        out.append(
            Action(
                r["item"],
                SAFE_AUTO,
                f"record `unavailable` for {r['gate']} (pid {r.get('pid')} is gone; the run "
                "was killed before it could record -- never a pass, never a failure)",
                f"the {r['gate']} run started {r.get('at')} died with its process; its "
                "started-marker outlives it",
                0,
                {"effect": "record_unavailable", "gate": r.get("gate"), "sha": r.get("sha", "")},
            )
        )
    return out


def _job_actions(rows: tuple[dict[str, Any], ...]) -> list[Action]:
    out: list[Action] = []
    for r in rows:
        if r.get("state") == "exited":
            why = f"its log records exit {r['exit_code']}"
        elif r.get("state") == "gone":
            why = "its process is gone and its log recorded no exit: it was killed"
        else:
            continue  # running, elsewhere, already ended: no recovery is owed
        out.append(
            Action(
                r["item"],
                SAFE_AUTO,
                f"ddflow job end {r['id']}",
                f"{r['id']} {why}; the owed `job.ended` event is recorded the normal way",
                0,
                {"effect": "job_end", "job": r.get("id"), "exit_code": r.get("exit_code")},
            )
        )
    return out


def _queue_actions(items: tuple[str, ...]) -> list[Action]:
    # One action per entry: the apply re-checks THIS entry only, so a plan action can
    # never requeue an entry the plan did not name.
    return [
        Action(
            e,
            SAFE_AUTO,
            "requeue the orphaned entry (every integrator run does this anyway)",
            "the integrator that owned this stopped while landing it; putting it "
            "back to QUEUED loses nothing",
            0,
            {"effect": "requeue", "items": [e]},
        )
        for e in items
    ]


def triage(facts: Facts, now: float, limits: Limits) -> list[Action]:
    """Every action for these facts: SAFE_AUTO first, then PROPOSE, then OPERATOR.

    ``now`` is the caller's clock, used only for an honest timestamp in evidence-less
    reasons; the triage itself reads facts alone, so it can be replayed for any moment.
    PROPOSE and OPERATOR are shown, never done; SAFE_AUTO is what an apply may run.
    """
    out = (
        _recovery_actions(facts, limits)
        + _dead_run_actions(facts.dead)
        + _job_actions(facts.jobs)
        + _queue_actions(facts.queue)
    )
    return sorted(out, key=lambda a: (_PRIORITY[a.cls], a.item))
