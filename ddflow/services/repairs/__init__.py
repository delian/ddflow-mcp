"""Versioned data repairs: find damage an older ddflow left in a log, mend it with new events.

Decision D-upgrade-model (4), task B-upgrade.5-repairs. `REGISTRY` lists every repair
(`base.Repair`: id, since, detect, repair). `pending` asks each detector and leaves out the
findings a `repair.applied` event already settled; `apply` appends each repair's corrective
events and then `repair.applied` (one per RECORD_FINDINGS findings) naming the repair and the
keys of the findings it settled, so a second run finds nothing. Nothing here edits or deletes a log line.

`ddflow upgrade` (B-upgrade.3/.4) and `ddflow doctor` are the callers.

The registry rule (`uncovered`): a fixed bug whose fix task is tagged `data-damage` left
damage in logs written before the fix, so it ships a repair here that names it in `bugs`,
or a `FOLD_ONLY` note saying why reading the log is already enough.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.events import REPAIR_APPLIED_KIND
from ...core.model import State
from ...infra.log import EventLog, running_version
from .base import AGENT, OPERATOR, Context, Corrective, Finding, Repair, Unavailable, context
from .seeds import AUTHORS, MISMATCHED, ORPHANS, SEEDS, TORN, unknown_author_shards

__all__ = [
    "AGENT",
    "DATA_DAMAGE_TAG",
    "FOLD_ONLY",
    "OPERATOR",
    "REGISTRY",
    "Context",
    "Corrective",
    "Finding",
    "Pending",
    "Repair",
    "Unavailable",
    "apply",
    "by_id",
    "context",
    "doctor_notes",
    "integrity",
    "pending",
    "settled",
    "uncovered",
    "unknown_authors",
]

REGISTRY: tuple[Repair, ...] = SEEDS

#: The tag on a bug's fix task that says the bug damaged data already written.
DATA_DAMAGE_TAG = "data-damage"

#: Bug id -> why no repair is needed: the damage is mended by reading the log as it is
#: (the fold or the reader already interprets the old shape correctly).
FOLD_ONLY: dict[str, str] = {
    "B5035a55092": "the reader split lines on U+2028, U+2029 and U+0085 written raw inside a "
    "string; the fixed reader reads every such line whole, so nothing needs appending",
}


#: A `repair.applied` event names at most this many findings, each detail cut to
#: DETAIL_CHARS: a repair with more findings writes several events, so no one line grows
#: past what the log reads back cheaply (`infra.log` reads a tail of at most 64 KiB).
RECORD_FINDINGS = 100
DETAIL_CHARS = 240


def by_id(rid: str) -> Repair:
    for r in REGISTRY:
        if r.id == rid:
            return r
    raise KeyError(f"no repair {rid!r}; known: {', '.join(r.id for r in REGISTRY)}")


def settled(st: State, rid: str) -> set[str]:
    """The finding keys every `repair.applied` of `rid` has recorded."""
    return {k for rec in st.repairs_applied.get(rid, ()) for k in rec["findings"]}


@dataclass
class Pending:
    repair: Repair
    findings: list[Finding] = field(default_factory=list)
    #: Why the detector could not run ("" when it ran).
    unavailable: str = ""

    def data(self) -> dict[str, Any]:
        return {
            **self.repair.data(),
            "findings": [{"key": f.key, "detail": f.detail} for f in self.findings],
            "unavailable": self.unavailable,
        }


def _split(ctx: Context, r: Repair) -> tuple[list[Finding], int]:
    """`(findings still open, how many a repair.applied already holds)` for `r`."""
    found = r.detect(ctx)
    done = settled(ctx.st, r.id)
    still = [f for f in found if f.key not in done]
    return still, len(found) - len(still)


def _detect(ctx: Context, r: Repair) -> Pending:
    try:
        still, _held = _split(ctx, r)
    except Unavailable as exc:
        return Pending(r, unavailable=str(exc))
    return Pending(r, still)


def pending(ctx: Context, ids: Iterable[str] | None = None) -> list[Pending]:
    """Each repair (or each named one) with the findings no `repair.applied` settled yet.
    A repair with nothing to do and a detector that ran is left out."""
    wanted = None if ids is None else {by_id(i).id for i in ids}
    out = [_detect(ctx, r) for r in REGISTRY if wanted is None or r.id in wanted]
    return [p for p in out if p.findings or p.unavailable]


def _apply_one(r: Repair, ctx: Context, log: EventLog) -> dict[str, Any] | None:
    """Apply `r` to what `ctx` read; None when nothing is pending. The caller holds the lock."""
    p = _detect(ctx, r)
    if not p.findings:
        return None
    corrective = r.repair(ctx, p.findings)
    for kind, subject, data in corrective:
        log.append(kind, subject, data)
    record = {
        "repair": r.id,
        "since": r.since,
        "version": running_version(),
        "findings": [f.key for f in p.findings],
        "events": len(corrective),
    }
    for at in range(0, len(p.findings), RECORD_FINDINGS):
        part = p.findings[at : at + RECORD_FINDINGS]
        log.append(
            REPAIR_APPLIED_KIND,
            r.id,
            {
                **record,
                "findings": [f.key for f in part],
                "details": [f.detail[:DETAIL_CHARS] for f in part],
                "events": len(corrective) if at == 0 else 0,
                "summary": f"{r.title}: {len(part)} finding(s) settled",
            },
        )
    return record


def apply(
    repo: Path, log: EventLog, cfg: Config, ids: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Apply the pending repairs: every `agent`-consent one, or exactly the named ones
    (an `operator`-consent repair runs only when named). Returns one record per repair
    applied, in registry order. Raises KeyError for an unknown id."""
    named = None if ids is None else {by_id(i).id for i in ids}
    applied: list[dict[str, Any]] = []
    for r in REGISTRY:
        if (named is None and r.consent != AGENT) or (named is not None and r.id not in named):
            continue
        # Read, decide and append under the log's lock: two agents applying the same repair
        # at once must not both adopt one orphan or both record the same findings.
        with log.transaction():
            record = _apply_one(r, context(repo, log, cfg), log)
        if record:
            applied.append(record)
    return applied


#: Repairs whose damage `ddflow doctor` already words itself (`integrity`, the orphan note
#: and the unknown-author note): `doctor_notes` leaves them out so nothing is said twice.
DOCTOR_WORDED = frozenset({TORN.id, MISMATCHED.id, AUTHORS.id, ORPHANS.id})


def doctor_notes(ctx: Context, *, skip: Iterable[str] = ()) -> list[str]:
    """One line per repair with pending findings or a detector that could not run, less
    the repairs named in `skip`."""
    notes = []
    for p in pending(ctx, [r.id for r in REGISTRY if r.id not in set(skip)]):
        if p.unavailable:
            notes.append(
                f"unavailable: data repair {p.repair.id} could not check ({p.unavailable})"
            )
            continue
        who = "" if p.repair.consent == AGENT else " (the operator decides)"
        notes.append(
            f"data repair {p.repair.id}{who}: {len(p.findings)} finding(s), "
            f"{p.repair.title} -- e.g. {p.findings[0].detail}. Applying it: "
            f"{p.repair.action}"
        )
    return notes


def integrity(ctx: Context) -> tuple[list[str], list[str]]:
    """`(problems, notes)` of the log's integrity -- what `EventLog.verify` reports -- less
    what a quarantine repair recorded: an edited event or unreadable line already on a
    `repair.applied` is a note, not a problem that would fail `doctor` forever."""
    edited, held = _split(ctx, MISMATCHED)
    problems = [
        f"{f.key}: content does not match its address (edited after the fact?)" for f in edited
    ]
    try:
        torn, held_torn = _split(ctx, TORN)
    except Unavailable as exc:
        torn, held_torn = [], 0
        problems.append(f"unavailable: the event shards could not be read ({exc})")
    if torn:
        problems.append(f"{len(torn)} unparseable line(s) — likely a torn append after a crash")
    held += held_torn
    if not held:
        return problems, []
    return problems, [f"{held} damaged log line(s) quarantined by a data repair, left in place"]


def unknown_authors(ctx: Context) -> tuple[list[str], str]:
    """The unknown-author shards no operator has reviewed (`unknown-author-shards`), and
    the base branch. Raises `Unavailable` when git cannot say."""
    new, base = unknown_author_shards(ctx)
    done = settled(ctx.st, AUTHORS.id)
    return [n for n in new if n not in done], base


def uncovered(st: State) -> list[str]:
    """Fixed bugs whose fix task is tagged `data-damage` and that neither a repair (its
    `bugs`) nor `FOLD_ONLY` accounts for -- the registry rule."""
    covered = {b for r in REGISTRY for b in r.bugs} | set(FOLD_ONLY)
    out = []
    for b in st.bugs.values():
        task = st.items.get(b.fix_task) if b.fix_task else None
        if b.fixed_at and task is not None and DATA_DAMAGE_TAG in task.tags:
            if b.id not in covered:
                out.append(b.id)
    return sorted(out)
