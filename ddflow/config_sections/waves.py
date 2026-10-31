"""The `[waves]` section: whether derived `wave:N` tags are kept stamped by default.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("waves")
@dataclass
class WavesConfig:
    """Dependency waves (D-wave-semantics), `[waves]`."""

    auto_stamp: bool = knob(
        True,
        doc="Keep the derived `wave:N` tags (wave 0 = no open prerequisite, task-level or inherited; wave N = 1 + the highest wave among open prerequisites) current by DEFAULT: after a command that changed the graph (a task or phase added, a `needs` change, a completion, an abandonment, a removal, a reopen) ddflow restamps, in one transaction, ONLY the open items whose wave changed -- never the whole queue per event. A hand edit of `wave:*` is overwritten on the next restamp. `false`: nothing is stamped on its own; `ddflow waves --stamp` still does it on request and `--check` reports what is stale.",
    )
