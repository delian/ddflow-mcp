"""`ddflow docs check`: the documentation check as the project's `[docs]` section asks for it.

``check_configured`` runs the reference checks over the right documents; this adds what the
section's other knobs decide -- which documents (``check_paths``), whether links are followed
and how far (``check_links``), a lint line (``lint_command``), and ``--diff`` -- and seals the
result in one digest. Anything asked for that could not be done is ``unavailable`` with its
reason: a check that did not run is never a clean one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.digest import content_digest
from .cmdrunner import CommandRunner, Declared
from .docscheck import _tree
from .docscheck_config import check_configured
from .docscheck_diff import added_lines, restrict
from .docscheck_net import check_remote
from .docscheck_types import KINDS, Finding, Report
from .docsync import glob_regex
from .shared_files import doc_exclude, export_targets


@dataclass
class Check:
    """The report, and what `[docs]` made of it."""

    report: Report
    severity: str
    diff: str = ""
    #: Asked for and not done, with the reason: the check is not clean while this is set.
    unavailable: dict[str, str] = field(default_factory=dict)
    #: Not asked for (no ``lint_command``): said, but it does not make a clean report unclean.
    not_configured: dict[str, str] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        """A finding stands and ``severity`` is 'block': the docs gate may not pass."""
        return self.severity == "block" and not self.report.clean

    def as_dict(self) -> dict:
        return {
            **self.report.as_dict(),
            "severity": self.severity,
            "blocking": self.blocking,
            "diff": self.diff,
            "unavailable": dict(sorted(self.unavailable.items())),
            "not_configured": dict(sorted(self.not_configured.items())),
        }


def _seal(check: Check) -> None:
    """Sort and digest the final report: one digest for everything the check says."""
    rep = check.report
    rep.findings.sort(key=lambda f: (f.path, f.line, KINDS.index(f.kind), f.ref))
    rep.notes.sort(key=lambda f: (f.path, f.line, f.ref))
    blob = json.dumps(
        {**rep.as_dict(), "digest": "", "unavailable": check.unavailable, "diff": check.diff},
        sort_keys=True,
        separators=(",", ":"),
    )
    rep.digest = content_digest(blob)


def _documents(repo: Path, cfg: Config) -> list[str] | None:
    """The tracked documents ``check_paths`` names; None (the enforce set) when it is empty."""
    if not cfg.docs.check_paths:
        return None
    rx = [glob_regex(g) for g in cfg.docs.check_paths]
    return [p for p in _tree(repo) if any(r.fullmatch(p) for r in rx)]


def _lint(repo: Path, cfg: Config, report: Report, check: Check) -> None:
    line = cfg.docs.lint_command.strip()
    if not line:
        check.not_configured["lint"] = "no [docs].lint_command is configured"
        return
    run = CommandRunner().run(
        Declared(line, "[docs].lint_command"),
        cwd=repo,
        timeout_s=cfg.docs.command_timeout_s,
        merge_stderr=True,
    )
    if not run.ran:
        check.unavailable["lint"] = f"`{line}` did not run: {run.reason}"
        return
    report.checked["lint"] = 1
    if run.code != 0:
        report.findings.append(
            Finding("lint", "", 0, line, f"exit {run.code}: {run.output.strip()}")
        )


def _links(repo: Path, cfg: Config, check: Check) -> None:
    """Apply ``check_links`` to a report that checked local links."""
    rep, mode = check.report, cfg.docs.check_links
    if mode == "off":
        rep.findings = [f for f in rep.findings if f.kind not in ("link", "anchor")]
        for key in ("links", "anchors"):
            rep.checked.pop(key, None)
    elif mode == "network":
        texts = {d: (repo / d).read_text("utf-8", errors="replace") for d in rep.checked["docs"]}
        remote = check_remote(texts, cfg.docs.link_timeout_s)
        rep.findings += remote.findings
        rep.checked["remote_links"] = remote.checked
        if remote.unavailable:
            check.unavailable["links"] = remote.unavailable


def run_check(repo: Path | str, cfg: Config, *, diff: str = "", run_commands: bool = True) -> Check:
    """Check the documents ``cfg`` names. Raises OSError when the tree cannot be listed, a
    document cannot be read or ``diff`` is not a base git knows: "could not tell" is never a
    clean report."""
    root = Path(repo)
    report = check_configured(
        root,
        cfg,
        run_commands=run_commands,
        docs=_documents(root, cfg),
        doc_globs=cfg.enforce.doc_globs,
        doc_exclude=doc_exclude(cfg),
        ignore_paths=cfg.docs.ignore_paths,
        ignore_names=cfg.docs.ignore_names,
        generated=export_targets(cfg, generated_only=True),
    )
    check = Check(report, cfg.docs.severity, diff)
    _links(root, cfg, check)
    if diff:
        docs = set(report.checked["docs"])
        report.findings = restrict(report.findings, docs, added_lines(root, diff))
    _lint(root, cfg, report, check)
    _seal(check)
    return check
