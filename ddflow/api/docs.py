"""`ddflow docs`: the documentation check, as one tool with a verb (D-docs-stage).

``check`` is the only verb so far; search and read join the same tool. The check itself is
`services/docscheck_run.py`; this reports it with the exit codes every command shares:
0 clean, 1 findings, 2 could not run or nothing to check (never 0), 3 refused.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core import outcome as O
from ..services.docscheck_run import run_check

VERBS = ("check",)


def docs(repo: Path, *, verb: str = "check", diff: str = "") -> O.Outcome:
    """Run ``verb`` of the documentation tool; ``diff`` is a git base for ``check``."""
    if verb not in VERBS:
        return O.refused("docs", f"unknown docs verb {verb!r}; one of {', '.join(VERBS)}")
    return _check(Path(repo), diff or "")


def _check(repo: Path, diff: str) -> O.Outcome:
    cfg = Config.load(repo)
    if cfg.docs.severity == "off":
        return O.nothing("docs.check", "the docs check is off ([docs].severity = off)")
    try:
        check = run_check(repo, cfg, diff=diff)
    except OSError as exc:
        return O.nothing("docs.check", f"the docs check could not run: {exc}")
    data = check.as_dict()
    rep = check.report
    if not rep.checked["docs"]:
        return O.nothing(
            "docs.check", "no document to check ([docs].check_paths matches none)", **data
        )
    if rep.findings:
        return O.failed("docs.check", f"{len(rep.findings)} finding(s) in the documents", **data)
    if check.unavailable:
        why = "; ".join(f"{k}: {v}" for k, v in sorted(check.unavailable.items()))
        return O.nothing("docs.check", f"unavailable, so not a clean report -- {why}", **data)
    return O.ok("docs.check", **data)
