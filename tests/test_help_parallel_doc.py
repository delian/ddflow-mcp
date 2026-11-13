"""Ba7c5816744: help/parallel.md agrees with claim/wait/next on the parallelism cap.

`claim <id>` never applies the cap to a NAMED item
(`test_a_cap_hold_says_a_named_claim_is_not_capped` pins this), so the help text must say
the cap trims only `next`'s ready list — not that `claim` refuses "when the parallelism
caps are full".
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ddflow"
    / "templates"
    / "prompts"
    / "help"
    / "parallel.md"
)


def test_help_parallel_says_a_named_claim_is_not_capped():
    text = _TEMPLATE.read_text()
    assert "does NOT hold" in text
    assert "claim for a NAMED item" in text
    assert "caps are full" not in text
