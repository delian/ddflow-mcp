"""What the project recorded, and what it did NOT — the half nothing ever said out loud.

B159. The log already knows that a task completed and no lesson followed, that a bug was
found and never closed, that a gate was skipped and never revisited. Every one of those is
an obligation the workflow states and nothing checks, so the only way anyone finds out is
by reading the log on purpose — which is exactly the thing an agent mid-session does not do.

**Why this is not a linter.** It reports only what is SPECIFIC and ACTIONABLE: an item id, a
count, and the tool that discharges it. A rule restated in the abstract ("remember to record
lessons") is a banner, and this repo has already learned that a standing banner is one
readers learn to skip — and then they skip the one that mattered
(`test_the_handshake_stays_quiet_once_the_import_is_finished`). Every finding here names
something that happened, and stops being reported once it is dealt with. That is the whole
design: it cannot be trained out by repetition, because repetition means it was ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Obligation:
    """One thing that was left undone, and the call that discharges it."""

    kind: str
    subject: str
    detail: str
    remedy: str

    def render(self) -> str:
        return f"{self.detail} — {self.remedy}"


#: How many findings a footer names before it stops. A footer longer than the answer the
#: agent asked for is one it scrolls past, and the point is to be read.
MAX_REPORTED = 3


def outstanding(state, cfg, *, repo=None, limit: int = MAX_REPORTED) -> list[Obligation]:
    """Obligations this project has taken on and not discharged.

    Reads the FOLDED state, plus — when `repo` is given — the rules files on disk. That is
    the one check here that touches the filesystem, and it is two `read_text` calls on a
    cadence rather than per call. That is what makes it
    affordable on a cadence: the caller has usually folded already, and where it has not,
    one fold every N calls is the budget.

    Ordered by how cheap the remedy is, not by severity. An open bug with no regression test
    is more serious than a missing lesson, but "record the lesson you just learned" is a
    single call and clearing it makes the next report shorter — which is what keeps the
    mechanism from being ignored.
    """
    from ..core.model import DONE

    found: list[Obligation] = []

    # 0. THE RULES FILE ITSELF, first because everything else assumes the agent read it.
    #    `adopt` writes the managed block and nothing ever looked again: a deleted
    #    AGENTS.md, a stripped block, or one from an older ddflow all left the agent
    #    reading rules that were absent or wrong, while every surface reported the project
    #    as adopted because `.ddflow/config.toml` existed. Adoption is a config file; the
    #    INSTRUCTIONS are a separate fact, and this is the one that checks it.
    try:
        from .adopt import rules_status

        stale = [r for r in rules_status(repo) if r.needs_attention] if repo else []
    except Exception:
        stale = []
    if stale:
        found.append(
            Obligation(
                "rules_drift",
                ", ".join(r.path for r in stale),
                "; ".join(r.render() for r in stale),
                "ask the operator, then `ddflow_setup` (shell: `ddflow adopt`) rewrites it",
            )
        )

    # 1. A bug found and never closed. The most concrete of the lot: somebody wrote down
    #    that something was broken, and the record still says so.
    open_bugs = [b for b in state.bugs.values() if b.open]
    if open_bugs:
        ids = ", ".join(sorted(b.id for b in open_bugs)[:4])
        found.append(
            Obligation(
                "open_bug",
                ids,
                f"{len(open_bugs)} bug(s) still open: {ids}",
                "close with `ddflow_bug_fixed` (it requires the regression test), or "
                "`ddflow_bug_invalid` with the probe if the finding was false",
            )
        )

    # 2. A gate SKIPPED rather than run. A skip is a recorded decision and legitimate --
    #    but a skip nobody revisited is a step the pipeline claims to enforce and did not.
    skipped: list[str] = []
    for item in state.items.values():
        if item.removed:
            continue
        for gate, record in (item.gates or {}).items():
            # `GateRecord`, not a dict. An `isinstance(record, dict)` guard here matched
            # nothing and reported no skipped gate ever — a check that silently found
            # nothing, which is the failure mode this whole module is about.
            if getattr(record, "outcome", "") == "skipped":
                skipped.append(f"{item.id}.{gate}")
    if skipped:
        found.append(
            Obligation(
                "skipped_gate",
                ", ".join(sorted(skipped)[:4]),
                f"{len(skipped)} gate(s) skipped, not run: {', '.join(sorted(skipped)[:4])}",
                "run them, or leave the skip on the record deliberately",
            )
        )

    # 3. Work completed with NOTHING learned from it. Deliberately the weakest signal here,
    #    and gated on a count: one task finishing without a lesson is normal, and reporting
    #    it would make this noise. Several in a row is the pattern the rule exists for.
    done = [i for i in state.items.values() if i.state == DONE and not i.removed]
    if len(done) >= cfg.lessons.reflect_after_items and not state.lessons:
        found.append(
            Obligation(
                "no_lessons",
                str(len(done)),
                f"{len(done)} item(s) finished and no lesson has ever been recorded",
                "`ddflow_lesson_add` — the pattern, not the incident",
            )
        )

    return found[:limit]


def footer(state, cfg, *, repo=None, limit: int = MAX_REPORTED) -> str:
    """The obligations as a block to append to a tool result, or "" when there are none.

    Empty is the common case and the important one: a footer that appears on every call is
    a banner, and this returns nothing at all when the project has nothing outstanding.
    """
    items = outstanding(state, cfg, repo=repo, limit=limit)
    if not items:
        return ""
    lines = ["ddflow: left undone in this project —"]
    lines += [f"  · {o.render()}" for o in items]
    return "\n".join(lines)


def summary(state, cfg, *, repo=None) -> dict[str, Any]:
    """The machine view, for `ddflow doctor` and anything else that wants the counts."""
    items = outstanding(state, cfg, repo=repo, limit=1000)
    return {
        "outstanding": [
            {"kind": o.kind, "subject": o.subject, "detail": o.detail, "remedy": o.remedy}
            for o in items
        ],
        "count": len(items),
    }
