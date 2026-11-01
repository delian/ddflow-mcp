"""The defensive self-assessment pass: file what an assessment text names, by default.

Rule `r-ddflow-self-assessment` was advisory -- whether a found defect reached the queue
depended on each agent filing it by hand. `auto_file` is the default-on pass that closes
that gap: give it the assessment text and it files a bug for each defect the text names,
skipping one that reads like an existing record (the same duplicate check `bug found` uses)
so nothing is filed twice. The callers are the pipeline's ends -- `session_end`,
the phase end in `api.lifecycle.complete`, the pre-compaction hook -- and any caller that
holds assessment text; the `[self_assessment].auto_file` knob turns the whole pass off.
"""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from ...services import self_assessment as SA
from .._base import _load
from . import bugs


def auto_file(
    repo: Path,
    *,
    text: str = "",
    trigger: str = "assessment",
    agent: str = "",
    no_task: bool = False,
) -> O.Outcome:
    """File the defects `text` names, unless `[self_assessment].auto_file` is off.

    ``trigger`` is where the pass ran: ``session_end``, ``phase_end``, ``pre_compact`` or
    ``assessment`` (a caller handed it assessment text directly). ``no_task`` passes
    ``bug found --no-task``: a bug fixed in the commit that found it files no fix task.

    Never raises and never repeats a record: each outcome that files is a ``bug.found`` the
    duplicate check has already run on, and a candidate that would read like an existing
    record is refused by `bug found` and reported under ``skipped`` rather than filed.
    """
    _log, cfg, _st = _load(repo, agent)
    if not cfg.self_assessment.auto_file:
        return O.ok("self_assessment", trigger=trigger, how="off", filed=[], skipped=[])
    defects = SA.extract_defects(text)
    filed: list[str] = []
    skipped: list[str] = []
    for defect in defects:
        out = bugs.bug_found(
            repo,
            summary=defect.summary,
            title=defect.title,
            severity=defect.severity,
            scope="ddflow",
            no_task=no_task,
            agent=agent,
        )
        if out.ok:
            filed.append(str(out.data.get("id", "")))
        else:
            # A refusal from `bug found` is the duplicate check saying an existing record
            # reads like this one; a failure is the same treatment -- skip, never force.
            skipped.append(str(out.data.get("id", "")))
    if not defects:
        return O.ok(
            "self_assessment",
            trigger=trigger,
            how="none",
            filed=[],
            skipped=[],
            line=SA.report_line(text, filed=[], skipped=[]),
        )
    return O.ok(
        "self_assessment",
        trigger=trigger,
        no_task=no_task,
        filed=filed,
        skipped=skipped,
        line=SA.report_line(text, filed=filed, skipped=skipped),
    )
