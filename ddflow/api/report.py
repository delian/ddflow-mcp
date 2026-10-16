"""`report`: the fixed completion report of one item, read back from the log (DX-agent-report)."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import prompts as P
from ..services import report as R
from ..services import report_render as RT
from ._base import _load


def report(repo: Path, *, item: str, by_agent: str = "", agent: str = "") -> O.Outcome:
    """The report of ``item``: landing, regression tests, per-gate outcomes, overrides, what was
    filed, what is open and what was not done. ``by_agent`` narrows what was filed to that
    agent. Exit 1 for an unknown item; read-only."""
    log, cfg, st = _load(repo, agent)
    data = R.collect(repo, log.read_all(), st, cfg, item, by_agent)
    if data is None:
        return O.failed("report", f"no item {item!r}: `ddflow list` shows the ids", text="")
    try:
        text = RT.render(P.resolve("agent_report", repo, P.overrides_from(cfg)), data)
    except P.TemplateError as exc:
        return O.failed("report", str(exc), text="")
    return O.ok("report", text=text, **data)
