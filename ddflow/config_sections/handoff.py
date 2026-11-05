"""The `[handoff]` section: the bounds of a peer's change suggestion to a file's lease holder
(D-handoff-peers, D-handoff-autoapply).

Re-exported from `ddflow.config`, which assembles `Config` from every section. Read by
`ddflow.services.handoff`. Every number is a knob: set it for the project, this machine, a
run (DDFLOW_HANDOFF_*) or over MCP with `ddflow_configure`; an organisation may lock it."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


def _count(value: object) -> str:
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return "" if ok else "a whole number of zero or more"


@declare("handoff")
@dataclass
class HandoffConfig:
    """Suggestions: a small patch recorded as data and sent to the agent that holds the lease
    on the file the sender needs. Never an instruction, never an approval."""

    max_files: int = knob(
        3,
        check=_count,
        doc="The most files one suggestion may touch (default 3; D-handoff-peers). A bigger change is an ordinary task, not a suggestion. 0 refuses every suggestion.",
    )
    max_lines: int = knob(
        60,
        check=_count,
        doc="The most added plus removed lines one suggestion may carry (default 60). A bigger change is an ordinary task.",
    )
    max_bytes: int = knob(
        65536,
        check=_count,
        doc="The most bytes of patch text one suggestion may carry (default 65536), checked before the patch is parsed or stored.",
    )
    max_open_per_owner_file: int = knob(
        5,
        check=_count,
        doc="How many suggestions one sender may have open (sent and not yet resolved) to the same owner for the same file (default 5; the operator's answer to D-handoff-peers). When the resolved ones free a place the sender may send more; at the limit it waits for the file's release or opens a task for the rest.",
    )
    min_interval_s: int = knob(
        30,
        check=_count,
        doc="The fewest seconds between two suggestions from one sender to one owner (default 30; 0 turns the spacing off). Spaces out a burst so an owner is not flooded.",
    )
    reject_breaker: int = knob(
        3,
        check=_count,
        doc="After this many suggestions in a row from one sender to one owner were REJECTED, the sender may send that owner no more until the owner accepts one or an operator resets it (default 3; 0 turns the breaker off). The refusal names the task to open instead.",
    )
    ttl_s: int = knob(
        86400,
        check=_count,
        doc="How many seconds a suggestion may stay unanswered before the expiry sweep marks it expired (default 86400, a day; 0 turns time expiry off). The owner is still told to answer; an expired one is re-opened as an ordinary task that needs the owner's item, not lost.",
    )
    kinds: list[str] = knob(
        factory=lambda: ["fix", "gate_fix", "refactor", "doc"],
        doc="The kinds a suggestion may declare (default fix, gate_fix, refactor, doc). A suggestion of any other kind is refused.",
    )
