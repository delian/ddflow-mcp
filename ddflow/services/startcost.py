"""What ddflow itself costs a session before the first turn (B-doctor-no-session-start-cost).

A spawned agent pays, in tokens, for the MCP `initialize` instructions, for `tools/list`
(the active `[mcp].tools` profile) and for the session brief the SessionStart hook prints.
They were measured one at a time (tests/test_mcp_tool_budget.py), never together, so a
64K first turn was invisible. This module only counts: the surface that owns each text
passes it in (the service layer imports no surface), and the estimate is characters over a
ratio (`[session].start_chars_per_token`), so no tokenizer is imported.

Measuring must not CHANGE anything: the upgrade notice is said once per version and the brief
sweeps finished worktrees. `measuring()` tells those to stand aside while a text is built.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

_MEASURING: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ddflow_measuring_start_cost", default=False
)


@contextmanager
def measuring() -> Iterator[None]:
    """Inside this block a text is being MEASURED, not delivered: once-per-version notices are
    not consumed and the brief does no housekeeping."""
    token = _MEASURING.set(True)
    try:
        yield
    finally:
        _MEASURING.reset(token)


def is_measuring() -> bool:
    return _MEASURING.get()


@dataclass(frozen=True)
class StartCost:
    """The first-turn cost of ddflow: tokens per part, and the budget they are held to."""

    parts: Mapping[str, int]  # name -> tokens
    chars: Mapping[str, int]  # name -> characters
    budget: int  # tokens; 0 = no budget

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
        return [self.line()] if self.parts else []

    def data(self) -> dict[str, object]:
        return {"start_cost": self.as_dict()} if self.parts else {}

    def as_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "budget": self.budget,
            "over": self.over,
            "parts": dict(self.parts),
            "chars": dict(self.chars),
        }


def measure(texts: Mapping[str, str], *, chars_per_token: float, budget: int) -> StartCost:
    """The cost of ``texts`` (name -> what the session is handed), at ``chars_per_token``."""
    ratio = chars_per_token if chars_per_token > 0 else 4.0
    return StartCost(
        parts={
            name: max(1, round(len(text) / ratio)) if text else 0 for name, text in texts.items()
        },
        chars={name: len(text) for name, text in texts.items()},
        budget=budget,
    )


def report(repo, cfg, agent: str, start_texts) -> StartCost:
    """The cost of the texts ``start_texts(repo, agent)`` returns, at the project's ratio and
    budget; empty when the surface supplied none or building them failed (a report must not
    fail on its own measuring)."""
    empty = StartCost({}, {}, 0)
    if start_texts is None:
        return empty
    try:
        return measure(
            start_texts(repo, agent),
            chars_per_token=cfg.session.start_chars_per_token,
            budget=cfg.session.context_budget_tokens,
        )
    except Exception:
        return empty
