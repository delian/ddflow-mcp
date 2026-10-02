"""The README names real commands, real tools, and every top-level command at least once.

`test_readme_agent_reader.py` checks the short agent-reader section. This checks the whole
file, because the README is also the reference a person reads, and on 2026-10-01 75 merges
landed while it changed in 9 commits (decision D-readme-current): commands it never named
and ones it named wrongly would otherwise only be found by someone following them.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ddflow.surfaces import cli, mcp

README = (Path(__file__).resolve().parents[1] / "README.md").read_text("utf-8")


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _spans() -> list[str]:
    """Every `ddflow …` span: inline code, and the lines of fenced blocks (with a leading
    `$ ` shell prompt removed). Prose is not read: only what a reader would type."""
    inline = re.findall(r"`(ddflow [^`\n]+)`", README)
    fenced = []
    for block in re.findall(r"^```[^\n]*\n(.*?)^```", README, flags=re.M | re.S):
        for raw in block.splitlines():
            ln = raw.strip().removeprefix("$ ")
            if ln.startswith("ddflow "):
                fenced.append(ln)
    return inline + fenced


def test_every_cli_command_is_named_in_the_readme():
    # `ddflow --agent X heartbeat T` names heartbeat as much as `ddflow heartbeat T` does.
    flat = re.sub(r"ddflow(?: --(?:agent|repo)(?:=| )\S+| --json)+", "ddflow", README)
    missing = [
        c
        for c in _subcommands(cli.build_parser())
        if not re.search(rf"ddflow {re.escape(c)}(?![\w-])", flat)
    ]
    assert not missing, f"commands the README never names (add them to the reference): {missing}"


def test_every_command_the_readme_names_exists():
    top = _subcommands(cli.build_parser())
    bad = []
    for raw in _spans():
        # `ddflow --agent X heartbeat T`: the global options come before the command.
        span = re.sub(r"^ddflow(?: --(?:agent|repo)(?:=| )\S+| --json)+", "ddflow", raw)
        # One invocation: cut at a chained command, a comment, or the reference's
        # description column (two spaces), which are prose or another command.
        span = re.split(r"\s{2,}|\s*(?:&&|;|\|\||\||#)\s*", span)[0]
        m = re.match(r"ddflow ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", span)
        if not m:
            continue
        cmd, sub = m.groups()
        if cmd not in top:
            bad.append(span)
            continue
        subs = _subcommands(top[cmd])
        if sub and subs and sub not in subs:
            bad.append(span)
            continue
        parser = subs[sub] if sub and subs else top[cmd]
        known = {o for a in parser._actions for o in a.option_strings}
        # A flag it names must be one the command takes (`--flag=v` and `--flag v` alike).
        for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)(?![\w-])", span):
            if flag not in known:
                bad.append(f"{span}  [{flag} is not an option of `ddflow {cmd}`]")
    assert not bad, f"not commands: {sorted(set(bad))}"


def test_every_mcp_tool_the_readme_names_exists():
    named = set(re.findall(r"`(ddflow_[a-z_]+)`", README))
    assert named
    missing = sorted(t for t in named if t not in mcp.TOOLS)
    assert not missing, f"not MCP tools: {missing}"


# -- exporting documents (B-export-docs) ---------------------------------------------------


def _export_section() -> str:
    start = README.index("## Exporting documents")
    # The section ends at the next `## ` heading OUTSIDE a fenced block (an example may
    # itself contain `## [1.2.3]` lines).
    out, fenced = [], False
    for i, ln in enumerate(README[start:].splitlines(keepends=True)):
        if ln.startswith("```"):
            fenced = not fenced
        elif i and not fenced and ln.startswith("## "):
            break
        out.append(ln)
    return "".join(out)


_HELP = (
    Path(__file__).resolve().parents[1] / "ddflow" / "templates" / "prompts" / "help" / "export.md"
).read_text("utf-8")


def _export_spans(text: str) -> list[str]:
    spans = re.findall(r"`(ddflow export[^`\n]*)`", text)
    for raw in text.splitlines():
        ln = raw.strip().removeprefix("$ ")
        if ln.startswith("ddflow export"):
            spans.append(ln)
    return spans


def test_every_export_kind_is_documented_in_the_readme_and_the_help_topic():
    from ddflow.services.export import registry

    registry.discover()
    kinds = sorted(registry._KINDS)
    assert len(kinds) >= 8
    section = _export_section()
    for kind in kinds:
        assert f"`{kind}`" in section, f"README 'Exporting documents' never names the kind {kind}"
        assert re.search(rf"\b{kind}\b", _HELP), f"help/export.md never names the kind {kind}"
        default = registry.get(kind).default_target
        assert default in section, f"README does not give {kind}'s default target {default}"
        assert default in _HELP, f"help/export.md does not give {kind}'s default target {default}"


def test_every_export_flag_and_verb_the_docs_name_exists():
    from ddflow.surfaces.commands import export as X

    parser = _subcommands(cli.build_parser())["export"]
    known = {o for a in parser._actions for o in a.option_strings}
    bad = []
    for where, text in (("README", _export_section()), ("help/export.md", _HELP)):
        for raw in _export_spans(text):
            span = re.split(r"\s{2,}|\s*(?:&&|;|\|\||\||#)\s*", raw)[0]
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)(?![\w-])", span):
                if flag not in known:
                    bad.append(f"{where}: {span} [{flag}]")
            m = re.match(r"ddflow export (enable|disable|ack|eject|validate)\b", span)
            if m and m.group(1) not in X.VERBS:
                bad.append(f"{where}: {span} [verb]")
    assert not bad, f"export flags/verbs the docs name that do not exist: {bad}"
    # and the reverse for verbs: every verb is documented in both places
    for verb in X.VERBS:
        assert f"ddflow export {verb}" in _export_section(), f"README never shows `export {verb}`"
        assert f"ddflow export {verb}" in _HELP, f"help/export.md never shows `export {verb}`"


def test_no_export_doc_says_a_shipped_feature_is_not_done_yet():
    for where, text in (("README", _export_section()), ("help/export.md", _HELP)):
        for stale in ("only off acts", "not applied yet", "arrives with", "not yet implemented"):
            assert stale not in text, f"{where}: stale text {stale!r}"
