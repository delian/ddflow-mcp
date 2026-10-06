"""Research notes: a falsifiable claim, the probe behind it and its verdict."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...config import csv_list
from ...core import ids as IDS
from ...core import outcome as O
from ...core.model import fold
from .._base import _load

VERDICTS = ("CONFIRMED", "REFUTED", "THEORETICAL")


@dataclass
class Finding:
    """One research result, named once.

    Eleven fields that travel together — argparse flags, MCP input properties, event
    payload. The same reason `decisions.Draft` and `gates.Evidence` exist: a field added
    to one of those three lists is a field the other two silently drop.
    """

    question: str
    verdict: str
    claim: str = ""
    mechanism: str = ""
    falsifier: str = ""
    probe: str = ""
    probe_output: str = ""
    sources: str = ""
    budget: str = ""
    item: str = ""
    id: str = ""
    #: What the adder says about a possible duplicate (``_dedupe.Answer``).
    answer: DD.Answer | None = None


def research_add(repo: Path, finding: Finding, *, agent: str = "") -> O.Outcome:
    """Record a research finding, with the verdict it earned.

    Both refusals are the rule that makes the record worth keeping: a note with no
    verdict is a literature summary, and a CONFIRMED with no probe is an opinion wearing
    a label.
    """
    if finding.verdict not in VERDICTS:
        return O.failed(
            "research.recorded",
            "verdict must be CONFIRMED, REFUTED or THEORETICAL. A note with no verdict "
            "is a literature summary, not research.",
        )
    if finding.verdict in ("CONFIRMED", "REFUTED") and not (finding.probe or finding.probe_output):
        return O.failed(
            "research.recorded",
            f"{finding.verdict} requires a --probe (and ideally --probe-output): a "
            f"verdict with no probe behind it is an opinion. Use THEORETICAL and say why "
            f"no probe was possible.",
        )
    log, cfg, st = _load(repo, agent)
    minted = IDS.mint(
        cfg,
        st,
        "research",
        events=log.read_all,
        given=finding.id,
        hash_parts=(finding.question, finding.claim),
    )
    rid = minted.id
    taken = _research_id_taken(st, rid, finding)
    if taken is not None:
        return taken
    chk = DD.check_add(
        repo,
        log,
        cfg,
        st,
        DD.Record(
            kind="research",
            event_kind="research.recorded",
            rid=rid,
            title=finding.question,
            body="\n".join(x for x in (finding.claim, finding.mechanism) if x),
            item=finding.item,
        ),
        finding.answer,
    )
    if chk.refusal is not None:
        return chk.refusal
    if chk.extension:
        return DD.extend(log, cfg, chk, "research.recorded")
    with log.transaction():
        # Asked again under the log's lock: another agent may have filed this id (as
        # research or as anything else) since `st` was read. Only a NAMED id can collide
        # (`auto_id` is time-salted), so only an explicit --id pays for this re-read and
        # re-fold while the lock is held.
        if finding.id:
            raced = _research_id_taken(fold(log.read_all(), strict=False), rid, finding)
            if raced is not None:
                return raced
        minted = IDS.confirm(
            cfg,
            "research",
            minted,
            used=IDS.used_now(log),
            hash_parts=(finding.question, finding.claim),
        )
        log.append(
            "research.recorded",
            rid,
            _research_fields(finding) | IDS.key_field(minted) | chk.fields,
        )
        DD.after_add(log, cfg, rid, chk)
    return O.ok("research.recorded", id=rid, verdict=finding.verdict, **chk.data())


def _research_fields(finding: Finding) -> dict[str, Any]:
    """What a research note records, as the fold will hold it. These keys are both the
    event payload (with the dedupe fields beside them) and what "the same record again"
    compares (`_research_id_taken`), so a field added here is compared too."""
    return {
        "question": finding.question,
        "claim": finding.claim,
        "mechanism": finding.mechanism,
        "falsifier": finding.falsifier,
        "probe": finding.probe,
        "probe_output": finding.probe_output,
        "verdict": finding.verdict,
        "sources": csv_list(finding.sources),
        "budget": finding.budget,
        "item": finding.item,
    }


def _research_id_taken(st, rid: str, finding: Finding) -> O.Outcome | None:
    """A re-add under an id that is already filed (B14d796e03b), or None to go ahead.

    The add-time check passes a taken id straight through, and the fold replaces a note
    wholesale, so a second `research add --id R1` used to overwrite R1's question, claim
    and verdict with no trace in state. The same record again is idempotent (nothing is
    written); anything else is refused and pointed at the paths that keep both."""
    kind = DD.kind_of(st, rid)
    if not kind:
        return None
    if kind != "research":
        return O.refused(
            "research.recorded",
            f"{rid} is already a {kind}: a research note needs an id of its own (choose "
            f"another --id, or omit it for a generated one).",
            id=rid,
        )
    old = st.research[rid]
    new = _research_fields(finding)
    if all(getattr(old, k) == v for k, v in new.items()):
        return O.ok("research.recorded", id=rid, verdict=finding.verdict, unchanged=True)
    differ = [k for k, v in new.items() if getattr(old, k) != v]
    return O.refused(
        "research.recorded",
        f"research {rid} already exists ({old.verdict.lower() or 'recorded'}: "
        f"{old.question[:100]}), and this add differs in {', '.join(differ)}. Adding never "
        f"overwrites a record. To add to it: --extends {rid} without --id. To file a "
        f"separate finding: another --id, or none for a generated one.",
        id=rid,
        differs=differ,
    )
