"""What `merge` does about required gates that are not settled (DX-merge-force-flag).

`[merge].require_gates`: `warn` lands the branch and names the unmet gates (D-scope-focus-2026-11);
`refuse` needs `--force`: a person's alone, an agent's with an operator approval bound to the exact
unmet gates and spent only when nothing else refuses the merge. Also the real state of every gate,
for the merge record (Bb55340e14b: the post-merge check's `passed` read as the ci gate's)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.digest import content_digest
from . import approval as AP
from . import completion as CM
from .approvals import consume as CN
from .approvals.canon import Canon

VERB = "merge.force"
#: The merge gate is the one this very merge records, so it is never "unmet" by the merge.
_SELF = "merge"


@dataclass
class Assessment:
    """The gates a merge would leave unsettled, and the mode that decides what that means."""

    mode: str = "warn"
    #: gate -> its recorded outcome (or "not run"), only the unmet required ones
    unmet: dict[str, str] = field(default_factory=dict)
    #: gate -> recorded outcome ("" when never run), every gate that has a record or is required
    states: dict[str, str] = field(default_factory=dict)

    @property
    def refuses(self) -> bool:
        return self.mode == "refuse" and bool(self.unmet)

    def awaiting(self) -> list[str]:
        return [f"{g}={o}" for g, o in self.unmet.items()]


def mode_of(cfg: Any) -> str:
    """`[merge].require_gates`: `warn` lands and names the unmet gates; `refuse` needs
    `--force` (a person's alone, an agent's with an operator approval)."""
    return cfg.merge.require_gates or "warn"


def assess(repo: Path, cfg: Any, st: Any, item: str) -> Assessment:
    """Which required gates of ``item`` are unsettled, the complete verdict's own list."""
    it = st.items[item]
    v = CM.verdict(st, cfg, item, repo=repo)
    unmet = {g: it.gate_outcome(g) or "not run" for g in v.unmet_gates if g != _SELF}
    return Assessment(mode=mode_of(cfg), unmet=unmet, states=gate_states(it, unmet))


def gate_states(it: Any, extra: Any = ()) -> dict[str, str]:
    """Every gate's real recorded outcome; unrecorded required gates read "not run"."""
    states = {g: rec.outcome for g, rec in it.gates.items()}
    for g in extra:
        states.setdefault(g, "not run")
    return states


def warning(item: str, a: Assessment) -> str:
    return (
        f"merged {item} with required gates unsettled: {', '.join(a.awaiting())}. The item is "
        f"merged, awaiting gates; settle them (`ddflow gate status {item}`), then "
        f"`ddflow complete {item}`."
    )


def refusal(item: str, a: Assessment, *, by_agent: bool) -> str:
    """The refusal of `refuse` mode with no `--force`: names the gates and the next call."""
    nxt = (
        f"`ddflow merge {item} --force --approval request` (an agent: the operator answers in chat)"
        if by_agent
        else f"`ddflow merge {item} --force`"
    )
    return (
        f"cannot merge {item}: [merge].require_gates = refuse and required gates are unsettled: "
        f"{', '.join(a.awaiting())}. Settle them (`ddflow gate status {item}`) or override with {nxt}."
    )


def canon_of(log: Any, item: str, a: Assessment) -> Canon:
    """The command an approval is of: this agent, this item, exactly these unmet gates."""
    return Canon(
        verb=VERB,
        agent=log.agent_id,
        item=item,
        target=",".join(sorted(a.unmet)),
        state=content_digest("\n".join(sorted(f"{g}={o}" for g, o in a.unmet.items()))),
    )


def authorise(
    log: Any, cfg: Any, item: str, a: Assessment, *, force: bool, approval: str, agent: str
) -> tuple[dict[str, Any] | None, str]:
    """(proof, refusal): the override's authority WITHOUT spending an agent's approval. A
    person's `--force` needs none (`{"by": "person"}`); an agent's `approval` is only checked
    here and returned as its proof -- [`spend`] records the one use, and the caller runs it
    AFTER the landing landed, so a landing that fails leaves the approval unspent
    (B527b0a518c)."""
    if not a.refuses:
        return None, ""  # nothing is unsettled, or the mode only warns: no override is needed
    by_agent = bool(AP.agent_marker(agent))
    if not force:
        return None, refusal(item, a, by_agent=by_agent)
    if not by_agent:
        return {"by": "person"}, ""
    if approval and approval != CN.REQUEST:
        # a real approval id: VALIDATE without spending; [`spend`] records the use only after
        # the caller's landing landed, so a landing that fails leaves it unspent (B527b0a518c).
        d = CN.check(log, canon_of(log, item, a), approval, cfg=cfg)
        return (d.stamp, "") if d.ok else (None, d.message)
    # no id, or `--approval request`: file the request / give the re-run hint (never a spend).
    d = CN.operator_gate(log, cfg, canon_of(log, item, a), approval_id=approval, auto=False)
    return (d.stamp, "") if d.ok else (None, d.message)


def spend(log: Any, cfg: Any, item: str, a: Assessment, approval: str) -> CN.Decision:
    """Record one use of the agent's validated ``approval`` ([`authorise`]), after the landing
    landed (B527b0a518c). Returns CN's `Decision`, not just the stamp: the caller checks
    ``.ok`` -- a spend that fails here means another consumer took the single use (or it
    expired) between [`authorise`]'s check and this spend, and the landing must not record a
    bogus proof."""
    return CN.spend(log, canon_of(log, item, a), approval)
