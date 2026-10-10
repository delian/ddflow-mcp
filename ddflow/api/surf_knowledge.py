"""What the knowledge command line takes from `infra` (D-unify: surfaces go through api).

`recall` prints each hit as the head and body the index summarises it to; the summary is the
store's, so the CLI reaches it here rather than importing `infra.store`."""

from __future__ import annotations

from ..infra.store import summarise_row

__all__ = ["summarise_row"]
