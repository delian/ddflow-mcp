"""What the reporting and viewer commands take from the engine, through the api layer.

The four viewer modules (`list`, `search`, `session`) and `status`/`show` declare their own
command lines and render their own prose, so they need a few limits, names and one reader
that live in `services/` and `infra/`. A surface reaches those only through `ddflow.api`
(`.importlinter`: surfaces-through-api); this module is that way, with nothing of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..infra import worktree as W
from ..services import search as S
from ..services import session_view as SV
from ..services import viewers as V

#: Most rows a `<kind> list` shows unless asked for more.
LIST_DEFAULT_LIMIT = V.DEFAULT_LIMIT
SEARCH_DEFAULT_LIMIT = S.DEFAULT_LIMIT
SEARCH_MAX_LIMIT = S.MAX_LIMIT
#: The kinds of row `search` covers, and the names of the sources behind them.
SEARCH_KINDS = S.SOURCES
SESSION_DEFAULT_LIMIT = SV.DEFAULT_LIMIT
SESSION_MAX_LIMIT = SV.MAX_LIMIT

#: Raised by `show_session` for an id that names no session.
SessionViewError = SV.SessionViewError


def list_filters(kind: str) -> Any:
    """The filters the list engine takes for `kind` (`agent` is the leaseholder filter)."""
    return V._FILTERS[kind]


def search_source_names() -> Any:
    """The names `search --source` accepts."""
    return S.source_names()


def show_session(events: Any, cfg: Any, session_id: str) -> dict[str, Any]:
    """One session whole: its prompts and notes in order, redacted."""
    return SV.show_session(events, cfg, session_id)


def worktree_path(repo: Path | str, name: str) -> Path:
    """The path of the item worktree recorded as `name`."""
    return W.load_path(repo, name)
