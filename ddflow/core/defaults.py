"""Defaults both surfaces and the typed layer share, declared where a command declaration
(`surfaces/declared/`, which stays free of `ddflow.api`) can read them.

Each is re-exported by the module that owns the behaviour it sets, with the reason it
exists, so the name callers use does not change.
"""

from __future__ import annotations

#: The priority a new phase or task is filed at when none is given.
DEFAULT_PRIORITY = 100

#: What `next` and `wait` look for when no kind is named.
DEFAULT_NEXT_KIND = "task"

#: Seconds `wait` blocks when the caller does not say.
DEFAULT_WAIT_TIMEOUT_S = 600
