"""Constants the schedule ordering shares: the `[schedule].order` default of the schedule
port, in `dpadapters` so both `config_sections` (which may import only `adapters`) and
`core.schedule` (which imports `adapters`) use it, with neither importing the other
(B0db4e156e8: `core -> config_sections` is a broken contract).
"""

from __future__ import annotations

#: The default order: today's, pinned by tests/test_schedule_order_knob.py. The listed
#: keys only -- `critical_path`, `waited`, `phase_priority` and `wave` are selectable
#: through `[schedule].order` but not in the default.
DEFAULT_ORDER: tuple[str, ...] = ("recover_first", "bugs_first", "unphased_first", "priority", "id")
