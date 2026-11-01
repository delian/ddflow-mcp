"""The repo's pre-commit config: what command the CI gate runs, and the tripwire hooks.

Split out of ``ddflow/services/ci.py``: the command resolution (``resolve_command``) and
the tripwire scan of the pre-push config (``tripwire_ids``).
"""

from __future__ import annotations

import re
from pathlib import Path

from ...config import Config

PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"
#: The pre-push run every derived default command is recognised by; the all-files arg is the
#: only part `scope_to_diff` replaces (the configured run now checks the change, Bf333cb2f5d).
PRE_PUSH_RUN = "pre-commit run --hook-stage pre-push"
DEFAULT_COMMAND = f"{PRE_PUSH_RUN} --all-files"


def resolve_command(repo: Path, cfg: Config) -> tuple[str, str]:
    """(command, why-not): the configured command, else the project's pre-push stage, else
    nothing to run (and the reason, so the gate says "unavailable" rather than passing)."""
    if cfg.ci.command.strip():
        return cfg.ci.command.strip(), ""
    if not (Path(repo) / PRE_COMMIT_CONFIG).is_file():
        return "", f"no [ci].command and no {PRE_COMMIT_CONFIG}: there is nothing to run"
    return DEFAULT_COMMAND, ""


_REPO_START = re.compile(r"^\s*-\s+repo:")
_HOOKS_KEY = re.compile(r"^(?P<ind>\s*)hooks:\s*(#.*)?$")
_HOOK_ID = re.compile(r"^\s*(?:-\s+)?id:\s*[\"']?(?P<id>[^\s\"'#]+)")
_FAIL_LANGUAGE = re.compile(r"^\s*(?:-\s+)?language:\s*[\"']?fail[\"']?\s*(#.*)?$")
_LIST_ITEM = re.compile(r"^\s*-\s*[\"']?(?P<v>[\w-]+)[\"']?\s*(#.*)?$")
#: The names pre-commit gives the pre-push stage (the old and the new).
_PUSH_STAGES = ("pre-push", "push")


def _runs_at_pre_push(stages: list[str] | None) -> bool:
    return stages is None or any(s in _PUSH_STAGES for s in stages)


def _hook_blocks(lines: list[str]) -> list[list[str]]:
    """The lines of each hook of the config: every list item under a ``hooks:`` key, whatever
    key it starts with (YAML mappings are unordered) -- a line of its own ends the block when it
    is as shallow as the item before it."""
    blocks: list[list[str]] = []
    hooks_indent: int | None = None
    item_indent: int | None = None
    for line in lines:
        text = line.lstrip()
        indent = len(line) - len(text)
        if m := _HOOKS_KEY.match(line):
            hooks_indent, item_indent = len(m["ind"]), None
        elif _REPO_START.match(line):
            hooks_indent = item_indent = None
        elif hooks_indent is not None and text and not text.startswith("#"):
            if indent <= hooks_indent and not text.startswith("-"):
                hooks_indent = item_indent = None  # a key of the enclosing mapping again
            elif text.startswith("-") and (item_indent in (None, indent)):
                item_indent = indent
                blocks.append([line])
            elif blocks:
                blocks[-1].append(line)
    return blocks


def tripwire_ids(tree: Path) -> list[str]:
    """The hooks of the project's pre-commit config that are tripwires (`language: fail`) and
    run at the pre-push stage: they fire when a changed path matches their `files:`, which is a
    statement about a DIFF, and over every tracked file they fail whenever the paths they guard
    exist at all. A tripwire kept to another stage (`stages: [pre-commit]`) is not one the
    pre-push run has, so it is not named."""
    try:
        lines = (Path(tree) / PRE_COMMIT_CONFIG).read_text("utf-8").splitlines()
    except OSError:
        return []
    default_stages = _stage_list(lines, "default_stages", top=True)
    found: list[str] = []
    for body in _hook_blocks(lines):
        ident = next((m["id"] for ln in body if (m := _HOOK_ID.match(ln))), "")
        if not ident or ident in found or not any(_FAIL_LANGUAGE.match(ln) for ln in body):
            continue
        if _runs_at_pre_push(_stage_list(body, "stages", top=False) or default_stages):
            found.append(ident)
    return found


def _stage_list(lines: list[str], key: str, *, top: bool) -> list[str] | None:
    """The stages a ``key:`` line (inline list or block list) names; None when it is absent."""
    for i, line in enumerate(lines):
        indent = "" if top else r"\s*(?:-\s+)?"
        m = re.match(rf"^{indent}{key}:\s*(?P<inline>\[[^\]]*\])?", line)
        if not m:
            continue
        if m["inline"]:
            return [x.strip(" \"'") for x in m["inline"].strip("[]").split(",") if x.strip()]
        out = []
        for nxt in lines[i + 1 :]:
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue  # a comment or a blank line inside the list is not its end
            item = _LIST_ITEM.match(nxt)
            if not item:
                break
            out.append(item["v"])
        return out
    return None
