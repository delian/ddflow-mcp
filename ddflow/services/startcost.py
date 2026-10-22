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

from collections.abc import Mapping
from dataclasses import dataclass

from ..core.measuring import is_measuring, measuring  # noqa: F401 -- re-exported


@dataclass(frozen=True)
class StartCost:
    """The first-turn cost of ddflow: tokens per part, and the budget they are held to."""

    parts: Mapping[str, int]  # name -> tokens
    bytes: Mapping[str, int]  # name -> UTF-8 bytes
    budget: int  # tokens; 0 = no budget
    #: Why the cost could not be measured (empty when it could): said, never silent.
    failed: str = ""

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
        return f"{head}: {made}" if made else head

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
        }


def measure(
    texts: Mapping[str, str], *, bytes_per_token: float, budget: int, reserve: int = 0
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
    return StartCost(parts=parts, bytes=sizes, budget=budget)


def report(repo, cfg, agent: str, start_texts) -> StartCost:
    """The cost of the texts ``start_texts(repo, agent)`` returns, at the project's ratio and
    budget; empty when the surface supplied none or building them failed (a report must not
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
            budget=cfg.session.context_budget_tokens,
            reserve=cfg.session.start_notice_reserve_tokens,
        )
    except Exception as exc:
        return StartCost({}, {}, 0, failed=f"{type(exc).__name__}: {exc}")
