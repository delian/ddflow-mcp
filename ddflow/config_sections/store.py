"""The `[store]` section: live knobs of the recall index (`ddflow/infra/store`).

A new file here is a new section, nothing more registers (`ddflow.config` assembles
`Config` by convention). The projection reads `stale_index_days` for how long an index
built by OTHER code survives before a rebuild garbage-collects it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("store")
@dataclass
class StoreConfig:
    """The recall index's live knobs (the event-log-backed index, not a config store)."""

    stale_index_days: float = knob(
        7.0,
        doc="How long an index built by OTHER code (a different fingerprint, or the pre-fingerprint `index.db`) is kept after its last write before a rebuild garbage-collects it. Worktrees on different ddflow versions each keep their own index meanwhile (D-compat).",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            else "must be a number >= 0"
        ),
    )
    index_touch_s: float = knob(
        86400.0,
        doc="An index in use is `touch`ed (its mtime refreshed) at most this often, so its mtime means 'last used', not 'last rebuilt', and `stale_index_days` of silence really is disuse.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            else "must be a number >= 0"
        ),
    )
