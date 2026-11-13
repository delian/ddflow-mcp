"""The ETA layer: read models derived from the event log's one facts fold.

Everything here answers "when will it finish" from what already happened; nothing here
adds a new store or a second fold (r-derive-dont-store, r-single-load). The data tables
:mod:`ddflow.services.eta.data` are the only place a duration (cycle time, gate duration,
lease wait, review rounds) is measured: the model and the simulator read them back rather
than measuring a second way.
"""

from __future__ import annotations

from . import data

__all__ = ["data"]
