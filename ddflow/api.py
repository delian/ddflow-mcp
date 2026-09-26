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
from .infra.log import EventLog, effective_agent_id


def _load(repo: Path, agent: str = "") -> tuple[EventLog, Config, State]:
    """Config FIRST, then the log — because the identity depends on the config.

    This used to be `EventLog(repo, agent)` with an empty `agent`, which falls to
    `default_agent_id()` and reads neither `DDFLOW_AGENT` nor `[agent].id`. The argv
    path resolved all four layers; this one resolved one. Same connection, same call,
    two identities, two shards.
    """
    cfg = Config.load(repo)
    log = EventLog(repo, effective_agent_id(repo, cfg, agent))
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


def loops(repo: Path) -> O.Outcome:
    """Circular references and runtime loops. Reads only.

    The shape B37 is about: ONE description of the result, from which both surfaces
    derive their view. `cmd_loops` used to build the JSON body and the human paragraph
    independently — two renderings of one answer, kept in step by hand, which is how
    `cmd_complete` came to print a coverage gap to humans only.

    Exit stays as it was: 1 when there are findings, 2 when there are none. "Loops
    found" is a finding rather than a tool failure, but that contract is what callers
    already branch on and changing it silently would be worse than its imperfection.
    """
    from .core import progress as PR

    log = EventLog(repo)
    events = log.read_all()
    st = fold(events, strict=False)
    cfg = Config.load(repo)
    findings = [f.__dict__ for f in PR.detect(events, st, cfg)]
    data: dict[str, Any] = {
        "findings": findings,
        "events": len(events),
        "items": len(st.items),
        "blocking": [f for f in findings if f.get("severity") == "block"],
        "checked": [
            "dependency cycles",
            "repeat claims",
            "gate flapping",
            "reopened items",
            "duplicate work",
            "stalled queue",
        ],
    }
    if not findings:
        return O.nothing("loops", "no loops detected", **data)
    n, b = len(findings), len(data["blocking"])
    return O.failed("loops", f"{n} finding(s)" + (f", {b} blocking" if b else ""), **data)


def progress(repo: Path, item: str = "") -> O.Outcome:
    """What work has actually been done, aggregated from the log. Reads only.

    The wire body is the ROW ARRAY, exactly as `ddflow progress --json` emits it — see
    `MIGRATED_WIRE_SHAPES`. The extra keys here are for the human renderer and for
    callers that want the count without walking the list; the `payload` entry on the
    tool keeps the MCP body unchanged.
    """
    from .core import progress as PR

    log = EventLog(repo)
    events = log.read_all()
    st = fold(events, strict=False)
    rows = [r for r in PR.work(events, st).values() if not item or r.item == item]
    rows.sort(key=lambda r: (-r.total_seconds, r.item))
    data: dict[str, Any] = {
        "rows": [r.summary() for r in rows],
        "count": len(rows),
        "item": item,
    }
    if item and not rows:
        return O.failed("progress", f"no such item {item!r}", **data)
    if not rows:
        return O.nothing("progress", "no work recorded yet", **data)
    return O.ok("progress", **data)


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
