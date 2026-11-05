"""The prompt library: list it, read one, render one, or eject a copy to edit."""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from ...infra import fsio
from ...services import prompts as P
from ...services.macros import macro_problems
from .._base import _load


def _prompts_get(P, repo: Path, name: str, pairs: list[str], problems: list[str]) -> O.Outcome:
    """`prompts get`: a workflow command or macro rendered as MCP `prompts/get` gives it.
    ``P`` is `services.prompts`, passed in by `prompts`, which already imported it."""

    def failed(why: str) -> O.Outcome:
        return O.failed("prompts", why, rows=[], action="get", text="", written=[], skipped=[])

    args: dict[str, str] = {}
    for pair in map(str, pairs):  # a schema-violating MCP client may send a number
        key, eq, value = pair.partition("=")
        if not eq or not key.strip():
            return failed(f"argument {pair!r}: expected KEY=VALUE")
        args[key.strip()] = value
    if name in P.TEMPLATE_NAMES and name not in P.all_commands(repo):
        return failed(
            f"{name!r} is a template, not a workflow command: it takes no arguments from a "
            f"caller. `ddflow prompts show {name}` prints it."
        )
    try:
        text = P.render_command(name, repo, args)
    except P.TemplateError as exc:
        return failed(str(exc))
    return O.ok(
        "prompts",
        rows=[],
        action="get",
        text=text,
        written=[],
        skipped=[],
        problems=[p for p in problems if f"macro {name!r}" in p],
    )


def prompts(
    repo: Path,
    *,
    action: str = "list",
    name: str = "",
    force: bool = False,
    agent: str = "",
    arg: list[str] | None = None,
) -> O.Outcome:
    """The prompt library: list it, read one, render one, or eject a copy to edit.

    `show` prints a prompt's SOURCE, as `eject` would write it. `get` renders a workflow
    command or `[[macro]]` with ``arg`` (``KEY=VALUE`` strings) exactly as MCP
    `prompts/get` serves it -- arguments bound, a macro's parameters required, its tool
    preamble on top -- through the same `prompts.render_command` (B5a2a2933c9).
    """

    _log, cfg, _st = _load(repo, agent)
    overrides = P.overrides_from(cfg)

    # Every configured macro that is NOT loaded, and why -- on the surface an operator
    # reads, not only in doctor (B-macro-clash-silent).
    problems = macro_problems(repo)
    if action == "list":
        rows = [
            {
                "name": t.name,
                "kind": t.kind,
                "source": t.source,
                # "" for an inline `[[macro]]`, which has no file: `str(None)` printed "None".
                "path": str(t.path) if t.path else "",
                "chars": len(t.text),
            }
            for t in P.list_all(repo, overrides)
        ]
        return O.ok(
            "prompts",
            rows=rows,
            action=action,
            text="",
            written=[],
            skipped=[],
            problems=problems,
        )
    if action == "show":
        try:
            return O.ok(
                "prompts",
                rows=[],
                action=action,
                text=P.resolve_any(name, repo, overrides).text,
                written=[],
                skipped=[],
                problems=[p for p in problems if f"macro {name!r}" in p],
            )
        except P.TemplateError as exc:
            return O.failed(
                "prompts", str(exc), rows=[], action=action, text="", written=[], skipped=[]
            )
    if action == "get":
        return _prompts_get(P, repo, name, list(arg or []), problems)
    if action == "eject":
        # With no name, everything -- BOTH registries, plus any `[[macro]]`. Writing only
        # the templates would mean the documented way to edit a workflow command does not
        # exist.
        names = (
            [name]
            if name
            # A `[[macro]]` named like a template (macros.py refuses only COMMANDS names)
            # is in BOTH `TEMPLATE_NAMES` and `all_commands`; dedupe so its one path lands
            # once -- in `written` OR `skipped`, never both (force would write it twice).
            else list(dict.fromkeys([*P.TEMPLATE_NAMES, *P.all_commands(repo)]))
        )
        out = repo / ".ddflow" / "prompts"
        (out / "commands").mkdir(parents=True, exist_ok=True)
        written, skipped = [], []
        for n in names:
            try:
                tmpl = P.resolve_any(n, None, {})  # the SHIPPED default, not the override
            except P.TemplateError:
                # A MACRO has no shipped default — its body IS the operator's already, so
                # there is nothing to eject and nothing to warn about.
                try:
                    tmpl = P.resolve_any(n, repo, overrides)
                except P.TemplateError as exc:
                    return O.failed("prompts", str(exc), rows=[], action=action, text="")
            # A command must land in `prompts/commands/`, which is where `resolve_command`
            # looks. Writing it beside the templates produces a file the operator edits and
            # the tool never reads -- an override that silently does nothing, which is worse
            # than refusing to eject it.
            dst = (out / "commands" / f"{n}.md") if tmpl.kind == "command" else (out / f"{n}.md")
            if dst.exists() and not force:
                skipped.append(str(dst))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            fsio.replace_text(dst, tmpl.text)
            written.append(str(dst))
        return O.ok(
            "prompts",
            rows=[{"path": w} for w in written],
            action=action,
            written=written,
            skipped=skipped,
            text="",
        )
    return O.failed(
        "prompts",
        f"unknown action {action!r}; known: list, show, get, eject",
        rows=[],
        action=action,
        text="",
        written=[],
        skipped=[],
    )
