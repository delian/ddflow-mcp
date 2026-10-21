"""Document types in ``ddflow docs check`` (B-dt-validate): the ``doctype`` finding family.

Every tracked document that the check covers (``check_paths``, else the document globs minus
the excluded and generated files, as for every other family) and that a type's ``path`` or
``pattern`` names is validated against that type (shipped types, then
`.ddflow/doctypes/*.toml`). A type that cannot be read, or a lint command that cannot run, is ``unavailable`` with the reason -- never a clean report. A type's
``[doctype.validate] lint_command`` (e.g. ``vale {path}``) runs once per document, but only for
a shipped type: a command in a project-added type stays inert until it is approved, said in
``not_configured`` (declared, not asked for by the operator, so it does not poison the report).
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import Config
from .cmdrunner import CommandRunner, Declared
from .docscheck import _tree
from .docscheck_types import Finding
from .docsync import glob_regex
from .doctypes import spec
from .doctypes.find import SHIPPED, type_glob, types_of
from .doctypes.validate import validate


def _lint(repo: Path, cfg: Config, dt: spec.DocType, path: str, check: Any) -> Iterable[Finding]:
    line = str(dt.validate.get("lint_command", "")).strip().replace("{path}", shlex.quote(path))
    key = f"lint:{dt.name}"
    if not line:
        return []
    if not dt.source.startswith(SHIPPED):
        check.not_configured[key] = (
            f"{dt.name}: a lint command in a project-added type is inert until approved"
        )
        return []
    run = CommandRunner().run(
        Declared(line, f"doctype {dt.name} lint_command"),
        cwd=repo,
        timeout_s=cfg.docs.command_timeout_s,
        merge_stderr=True,
    )
    if not run.ran:
        check.unavailable[key] = f"`{line}` did not run: {run.reason}"
        return []
    return (
        [Finding("doctype", path, 0, "lint", f"exit {run.code}: {run.output.strip()}")]
        if run.code
        else []
    )


def check_doctypes(repo: Path, cfg: Config, check: Any, *, run_commands: bool = True) -> None:
    """Add the ``doctype`` findings of every document that has a type to ``check``."""
    report = check.report
    try:
        types = types_of(repo)
    except (ValueError, OSError) as exc:
        check.unavailable["doctypes"] = str(exc)
        return
    tree = _tree(repo)
    have = set(tree)
    checked = set(report.checked.get("docs", []))  # the documents this check covers, no others
    dirs = {str(p) for f in tree for p in PurePosixPath(f).parents}

    def read(path: str) -> str | None:
        """The text of a tracked file; "" for a tracked directory (a link to it resolves)."""
        if path in have:
            return (repo / path).read_text("utf-8", errors="replace")
        return "" if path.rstrip("/") in dirs else None

    count = 0
    for dt in types:
        rx = glob_regex(type_glob(dt))
        for path in (p for p in tree if p in checked and rx.fullmatch(p)):
            try:
                text = read(path)
                report.findings += validate(dt, text or "", path, read=read)
            except OSError as exc:
                check.unavailable["doctypes"] = f"{path}: {exc}"
                continue
            count += 1
            if run_commands:
                report.findings += _lint(repo, cfg, dt, path, check)
            elif dt.validate.get("lint_command") and dt.source.startswith(SHIPPED):
                check.unavailable[f"lint:{dt.name}"] = "commands are off for this check"
            elif dt.validate.get("lint_command"):
                check.not_configured[f"lint:{dt.name}"] = (
                    f"{dt.name}: a lint command in a project-added type is inert until approved"
                )
    report.checked["doctypes"] = count
