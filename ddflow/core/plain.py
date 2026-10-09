"""Dataclass -> plain data, for anything that has to cross a wire.

Extracted at the THIRD copy. `surfaces/context.py` had it, then `api/decisions.py`
needed it (a surface may not be imported by the api layer), then `api/workflow.py` did
— and the rule here is that the second occurrence is reuse-or-extract and the third
makes extraction mandatory. Three copies of a recursive converter is three chances for
one of them to stop handling tuples.

Pure: no disk, no config, no knowledge of what it is converting. The RELATED conversion
— resolving a stored relative worktree path to something a caller can `cd` to — is
deliberately NOT here, because it needs the repo root; it lives in `infra/worktree.py`
as `absolutise`.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any


def plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        # Fields marked `metadata={"internal": True}` are folded state, not wire data.
        return {
            f.name: plain(getattr(obj, f.name))
            for f in fields(obj)
            if not f.metadata.get("internal")
        }
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    return obj
