"""`report`: the fixed completion report of one item, read back from the log (DX-agent-report)."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import prompts as P
from ..services import report as R
from ..services import report_brief as RB
from ..services import report_render as RT
from ._base import _load


def item_report(
    repo: Path, *, item: str, by_agent: str = "", agent: str = "", brief: bool = False
) -> O.Outcome:
    """The report of ``item``: landing, regression tests, per-gate outcomes, overrides, what was
    filed, what is open and what was not done. ``by_agent`` narrows what was filed to that
    agent; ``brief`` is the 8-line form. Exit 1 for an unknown item; read-only."""
    log, cfg, st = _load(repo, agent)
    data = R.collect(repo, log.read_all(), st, cfg, item, by_agent)
    if data is None:
        return O.failed("report", f"no item {item!r}: `ddflow list` shows the ids", text="")
    if brief:
        return O.ok("report", text=RB.render(data, cfg.session.report_brief_chars), **data)
    try:
        text = RT.render(
            P.resolve("agent_report", repo, P.overrides_from(cfg)),
            data,
            cfg.session.report_test_files,
        )
    except P.TemplateError as exc:
        return O.failed("report", str(exc), text="")
    return O.ok("report", text=text, **data)


def show_report(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """`report` in the shape of `show` (its body is the ``item`` value): what ``ddflow_show
    report=true`` returns over MCP, where `report` has no tool of its own. The text leads and
    the data nests under ``report``, so the ``show`` bound (`mcp_bound.bound_show`, which
    clips anything carrying ``gates`` or ``state`` at its top level) leaves it whole."""
    out = item_report(repo, item=item, agent=agent)
    if out.exit:
        return out
    data = {k: v for k, v in out.data.items() if k != "text"}
    return O.ok("show", item={"text": out.data["text"], "report": data})
