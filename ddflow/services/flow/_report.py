"""PR flow: sync outcome records.

Part of the former ``ddflow/services/flow.py``, split into submodules that stay under
30k chars each (rule r-small-files); see the package docstring for the full story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Change:
    item: str
    what: str  # merged | completed | blocked | changes_requested | closed | merged_by_ddflow | retargeted | updated | back_merge | queued | queue_ejected
    detail: str = ""
    url: str = ""


@dataclass
class SyncReport:
    checked: int = 0
    changes: list[Change] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
