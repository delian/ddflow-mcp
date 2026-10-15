"""The `docs` tool and command, declared once: the documentation check (D-docs-stage).

ONE tool, `ddflow_docs`, with a `verb` (the record-surface convention: the tools/list budget
has no room for a tool per verb). `check` is the first verb; `ddflow docs check` is its CLI
half. Read-only by construction: it reads the tree and edits nothing.
"""

from __future__ import annotations

from ..registry import CliPolicy, Command, Param, by_tool
from ..tools._common import _api

#: The findings are the OUTPUT, so they print on every exit.
_SHOWN = CliPolicy(shown=None)

PAYLOAD = (
    "findings",
    "notes",
    "checked",
    "digest",
    "severity",
    "blocking",
    "diff",
    "unavailable",
    "not_configured",
)


def _text(out, a) -> str:
    d = out.data
    if "findings" not in d:
        return out.reason
    lines = [
        f"{f['path'] or '-'}:{f['line']}: {f['kind']} {f['ref']}: {f['detail']}"
        for f in d["findings"]
    ]
    lines += [f"unavailable, {k}: {v}" for k, v in d["unavailable"].items()]
    lines += [f"not configured, {k}: {v}" for k, v in d["not_configured"].items()]
    checked = d["checked"]
    head = (
        f"docs check: {len(d['findings'])} finding(s), {len(d['notes'])} note(s) in "
        f"{len(checked.get('docs', []))} document(s); severity {d['severity']}"
        f"{'; blocking' if d['blocking'] else ''}; digest {d['digest'][:12]}"
    )
    return "\n".join([*lines, head])


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("docs",),
        summary="the mechanical documentation check: `docs check [--diff BASE]`",
        tool="ddflow_docs",
        description=(
            "Documentation, read-only. verb=check: do the names, flags, links and anchors the "
            "documents ([docs].check_paths) mention exist? Findings, what was checked, a digest. "
            "diff=BASE keeps only surface the diff since BASE added. Exit 1 findings, 2 could "
            "not run (never a clean report), 3 refused."
        ),
        call=lambda repo, a, agent: _api().run_docs(
            repo, verb=a.get("verb") or "check", diff=a.get("diff") or ""
        ),
        payload=PAYLOAD,
        render=_text,
        cli=_SHOWN,
        params=(
            Param(
                "verb",
                help="check (default).",
                cli_help="check (default)",
                positional=True,
                nargs="?",
                choices=("check",),
                default=None,
            ),
            Param(
                "diff",
                help="check: a git base; report only the extracted surface added since it.",
                cli_help="check: only the extracted surface added since this git base",
                metavar="BASE",
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
