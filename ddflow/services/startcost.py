"""What ddflow itself costs a session before the first turn (B-doctor-no-session-start-cost).

A spawned agent pays, in tokens, for the MCP `initialize` instructions, for `tools/list`
(the active `[mcp].tools` profile) and for the session brief the SessionStart hook prints.
They were measured one at a time (tests/test_mcp_tool_budget.py), never together, so a
64K first turn was invisible. This module only counts: the surface that owns each text
passes it in (the service layer imports no surface), and the estimate is bytes over a
ratio (`[session].start_bytes_per_token`), so no tokenizer is imported.

Measuring must not CHANGE anything: the upgrade notice is said once per version and the brief
sweeps finished worktrees. `measuring()` tells those to stand aside while a text is built; so a
pending upgrade notice (one line, said once per version) is covered by a reserve instead
(`[session].start_notice_reserve_tokens`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..core.measuring import is_measuring, measuring  # noqa: F401 -- re-exported
from . import harnessreg as HR


def effective_budget(session, target: str = "") -> int:
    """The start budget, in tokens, of a session of agent type ``target`` ("" = unknown): the
    smaller of the global ceiling (`context_budget_tokens`; the table's `global` key is the same
    value, folded in when the config is read) and the type's `[session.context_budget]` entry. A level that is 0 or unset sets no ceiling and drops
    out; a type above the global does not raise it (D-session-start-budget). 0 when nothing is set."""
    table = session.context_budget or {}
    # A `global` key never survives the config loader (it becomes `context_budget_tokens`), but a
    # session object built by hand may still carry one: it counts, as the same ceiling.
    levels = [
        session.context_budget_tokens,
        table.get("global", 0),
        table.get(target, 0) if target else 0,
    ]
    return min((v for v in levels if v > 0), default=0)


def configured_targets(session) -> dict[str, int]:
    """`{agent type: effective budget}` for each type the table names, in name order."""
    return {
        t: effective_budget(session, t)
        for t in sorted(session.context_budget or {})
        if t != "global"
    }


@dataclass(frozen=True)
class StartCost:
    """The first-turn cost of ddflow: tokens per part, and the budget they are held to."""

    parts: Mapping[str, int]  # name -> tokens
    bytes: Mapping[str, int]  # name -> UTF-8 bytes
    budget: int  # tokens; 0 = no budget
    #: Why the cost could not be measured (empty when it could): said, never silent.
    failed: str = ""
    #: Agent type -> its effective budget, for each type `[session.context_budget]` names.
    targets: Mapping[str, int] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return sum(self.parts.values())

    @property
    def over(self) -> int:
        """Tokens past the budget, 0 when within it (or no budget is set)."""
        return max(0, self.tokens - self.budget) if self.budget > 0 else 0

    def line(self) -> str:
        """`session start cost: N tokens (budget M)` and what it is made of."""
        budget = f"budget {self.budget}" if self.budget > 0 else "no budget"
        head = f"session start cost: {self.tokens} tokens ({budget})"
        if self.over:
            head += f" -- OVER by {self.over}"
        made = ", ".join(f"{name} {n}" for name, n in self.parts.items())
        head = f"{head}: {made}" if made else head
        return "\n".join([head, *self._target_lines()])

    def _target_lines(self) -> list[str]:
        """One line per configured agent type: the same cost against that type's budget."""
        out = []
        for name, budget in self.targets.items():
            over = max(0, self.tokens - budget) if budget > 0 else 0
            cap = f"budget {budget}" if budget > 0 else "no budget"
            odd = (
                "" if name in {h.id for h in HR.load()} else " [no such agent type: never matches]"
            )
            out.append(
                f"  {name}{odd}: {self.tokens} tokens ({cap})"
                + (f" -- OVER by {over}" if over else "")
            )
        return out

    def notes(self) -> list[str]:
        """The doctor note: the cost line, or nothing when no texts were measured."""
        if self.failed:
            return [f"session start cost: could not be measured ({self.failed})"]
        return [self.line()] if self.parts else []

    def data(self) -> dict[str, object]:
        return {"start_cost": self.as_dict()} if self.parts else {}

    def as_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "budget": self.budget,
            "over": self.over,
            "parts": dict(self.parts),
            "bytes": dict(self.bytes),
            "targets": dict(self.targets),
        }


def measure(
    texts: Mapping[str, str],
    *,
    bytes_per_token: float,
    budget: int,
    reserve: int = 0,
    targets: Mapping[str, int] | None = None,
) -> StartCost:
    """The cost of ``texts`` (name -> what the session is handed), at ``bytes_per_token``."""
    ratio = bytes_per_token if bytes_per_token > 0 else 4.0
    sizes = {name: len(text.encode("utf-8")) for name, text in texts.items()}  # bytes
    parts = {name: max(1, round(n / ratio)) if n else 0 for name, n in sizes.items()}
    if reserve > 0 and any(
        sizes.values()
    ):  # a pending notice cannot be counted without being used up
        parts["upgrade notice (reserve)"] = reserve
        sizes["upgrade notice (reserve)"] = round(reserve * ratio)
    return StartCost(parts=parts, bytes=sizes, budget=budget, targets=dict(targets or {}))


def report(repo, cfg, agent: str, start_texts) -> StartCost:
    """The cost of the texts ``start_texts(repo, agent)`` returns, at the project's ratio and
    budget (the global one, or that of the agent type running this command); empty when the surface supplied none or building them failed (a report must not
    fail on its own measuring)."""
    empty = StartCost({}, {}, 0)
    if start_texts is None:
        return empty
    try:
        with measuring():  # the surface's own guard is not the only one
            texts = start_texts(repo, agent)
        return measure(
            texts,
            bytes_per_token=cfg.session.start_bytes_per_token,
            # The headline is judged against the agent type running this very command, when its
            # harness can be told from the environment; the table's types follow it.
            budget=effective_budget(cfg.session, HR.detect(os.environ)),
            reserve=cfg.session.start_notice_reserve_tokens,
            targets=configured_targets(cfg.session),
        )
    except Exception as exc:
        return StartCost({}, {}, 0, failed=f"{type(exc).__name__}: {exc}")
