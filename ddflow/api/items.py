"""Creating and changing queue items."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ._base import _load


def update(
    repo: Path,
    item: str,
    *,
    agent: str = "",
    title: str | None = None,
    body: str | None = None,
    needs: list[str] | None = None,
    globs: list[str] | None = None,
    tags: list[str] | None = None,
    priority: int | None = None,
) -> O.Outcome:
    """Change an item's fields. `None` means "leave alone"; `[]` means "clear".

    That sentence is the whole reason this function exists. Over argv the two collapse
    into one empty string, and the surface had to carry a `clearable` flag to tell them
    apart — a distinction the type system expresses for free.
    """
    log, _cfg, st = _load(repo, agent)
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("item.updated", f"no such item {item!r}{gone}", id=item)

    fields: dict[str, Any] = {}
    if title is not None:
        fields["title"] = title
    if body is not None:
        fields["body"] = body
    if needs is not None:
        fields["needs"] = list(needs)
    if globs is not None:
        fields["globs"] = list(globs)
    if tags is not None:
        fields["tags"] = list(tags)
    if priority is not None:
        fields["priority"] = int(priority)

    if not fields:
        return O.nothing(
            "item.updated",
            "nothing to change: every field was left unset. Pass a value to set one, "
            "or an empty list to clear it.",
            id=item,
        )
    log.append(f"{it.kind}.updated", item, fields)
    return O.ok("item.updated", id=item, changed=sorted(fields), fields=fields)
