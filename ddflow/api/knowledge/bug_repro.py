"""What a bug's repro bundle (`services.reprobundle`) adds to the surfaces' answers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...services import reprobundle as RB


def repro_lead(repo: Path, out: Any, events: Any, item: str) -> Any:
    """``out`` (a claim's answer) with the repro bundle of each bug ``item`` is the fix task
    of put first in its guidance: the reader of a fix task starts from how the bug was seen."""
    return RB.lead_with(out, repo, events, item)
