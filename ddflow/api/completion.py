"""May this item complete, and did it?"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..services import completion as CM
from ._base import _load


def completion_verdict(repo: Path, item: str, *, model: str = "") -> O.Outcome:
    """Would this item complete, and if not, why not? Reads only.

    Exposed as its own operation because "may I" and "do it" are different questions,
    and an agent that can only ask by *attempting* learns the answer by causing the
    thing it was checking for.
    """

    _log, cfg, st = _load(repo)
    v = CM.verdict(st, cfg, item, repo=repo, model=model)
    data: dict[str, Any] = {
        "id": item,
        "may_complete": v.may_complete,
        "blockers": v.blockers,
        "warnings": v.warnings,
        "coverage_gaps": v.coverage_gaps,
        "coverage_note": v.coverage_note,
        "independence": v.independence,
    }
    if v.may_complete:
        return O.ok("completion.verdict", **data)
    return O.refused(
        "completion.verdict",
        f"{len(v.blockers)} unmet condition(s): " + "; ".join(v.blockers),
        **data,
    )
