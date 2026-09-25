"""The application layer: one typed entry point per operation, for both surfaces.

`surfaces/mcp.py` reached `surfaces/cli.py` — a protocol adapter depending on a
presentation layer — and the edge was carried by **strings**. Typed MCP arguments were
flattened to argv, re-parsed by argparse, and the result recovered by scraping stdout
plus an exit code. Two costs are visible in the code itself:

* `mcp._opt(..., clearable=True)` exists only to rebuild the "absent vs empty"
  distinction argv erased. `ddflow_update(id="X", needs="")` silently did nothing while
  `ddflow update X --needs ""` cleared the field — and breaking a dependency cycle is
  exactly the operation that needs it, and the one the loop detector tells you to do.
  In a typed call `None` and `""` are simply different values and no helper is needed.
* `mcp._run_cli` swaps process-global `sys.stdout`/`sys.stderr` for every call. That is
  not reentrant: it forecloses concurrency in a server for a tool whose entire purpose
  is parallel agents.

Parity was held by ratchets where types would hold it structurally, and those ratchets
catch a *missing* flag, never a *changed encoding*.

**Migration, not a rewrite.** Each operation here is one a surface used to implement
inline. A tool with an `api` entry is dispatched through this module; the rest still go
through argv, and `tests/test_mcp_parity.py` counts the remainder and refuses to let it
grow. A big-bang port of ~60 commands would be one unreviewable change against a suite
that cannot tell which half broke.

Every function takes plain values and returns an `Outcome` — one description of a
result, from which both the machine view (`data`) and the human view are derived. It is
never both surfaces describing the same thing independently, which is the shape that
produced the bug still commented in `cmd_complete`: a coverage gap printed in human
mode only, invisible to the agent reading JSON that most needed it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config
from .core import outcome as O
from .core.model import State, fold
from .infra.log import EventLog


def _load(repo: Path, agent: str = "") -> tuple[EventLog, Config, State]:
    log = EventLog(repo, agent)
    cfg = Config.load(repo)
    return log, cfg, fold(log.read_all(), strict=False)


def completion_verdict(repo: Path, item: str, *, model: str = "") -> O.Outcome:
    """Would this item complete, and if not, why not? Reads only.

    Exposed as its own operation because "may I" and "do it" are different questions,
    and an agent that can only ask by *attempting* learns the answer by causing the
    thing it was checking for.
    """
    from .services import completion as CM

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
