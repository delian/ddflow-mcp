"""Constants the schedule ordering shares: the `[schedule].order` default.

A leaf module at `ddflow.core` (not `ddflow.core.schedule`), so `config_sections` can
import it without importing `ddflow.core.schedule` (whose `_filters` imports `ddflow.config`,
which imports `config_sections` -- a cycle) and so `core` never imports `config_sections`
(B0db4e156e8: the contract `core -> config_sections` is broken otherwise).
"""

from __future__ import annotations

#: The default order: today's, pinned by tests/test_schedule_order_knob.py. The listed
#: keys only -- `critical_path`, `waited`, `phase_priority` and `wave` are selectable
#: through `[schedule].order` but not in the default.
DEFAULT_ORDER: tuple[str, ...] = ("recover_first", "bugs_first", "unphased_first", "priority", "id")
