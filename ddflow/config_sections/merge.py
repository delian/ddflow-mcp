"""The `[merge]` section: what `ddflow merge` does when required gates are not settled.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("merge")
@dataclass
class MergeConfig:
    """Landing a branch (`ddflow merge`)."""

    require_gates: str = knob(
        "warn",
        doc="What `ddflow merge` does when the item still has required gates without a settled outcome (silent, unavailable, partial, failed review). 'warn' (default, D-scope-focus-2026-11): the branch lands, the result and a progress line list the exact unmet gates and the board reads 'merged, awaiting gates'; `complete` is where they bind. 'refuse': the merge is refused and names the unmet gates; a person passes `--force`, an agent passes `--force --approval ID` (an operator approval bound to that exact command, single-use, not spent by a refused call) and the landing is recorded as a `merge.forced` event naming the gates it overrode. Under `[flow].integration = pr` the same check runs before the request opens (Bc6c07dfe52): `refuse` refuses opening it and `warn` opens it naming the unmet gates. A failed review or failed ci gate refuses in both modes. An agent's edit of this knob is a `config.set`, which needs a person's approval.",
        choices=("warn", "refuse"),
        strictest=("refuse", "a merge with unsettled required gates needs --force and an approval"),
    )
