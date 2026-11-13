"""The rules inventory: every agent's native rule and command files, and who loads what.

A project's OWN rules bind only the agent whose surface they live in. This is the read-only
census of that: per file, which agents load it and when (always / by path / on request);
per agent, the always-loaded size; and rules that restate a workflow ddflow already owns,
as NOTES (the operator resolves a conflict; nothing here edits a file).

The agent -> surface table is `harnessreg` (`instructions.reads`, `commands.dir`) and
`adopt.NATIVE_RULES`; there is no second list here. `parse_frontmatter` and
`scan_rule_files` are public for the importers that read the same files
(B-path-rules-to-decisions, B-rules-import).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.budget import CHARS_PER_TOKEN
from . import harnessreg as HR
from .adopt import FORM_WHOLE, NATIVE_RULES, adopted_agents

ALWAYS = "always"
BY_PATH = "by path"
ON_REQUEST = "on request"
KIND_RULE = "rule"
KIND_CROSS = "cross-agent"
KIND_COMMAND = "command"

#: The one project-wide token estimate (`core.budget`), labelled approximate in the report.
TOKENS_NOTE = "approximate: bytes / 4, not a tokenizer's count"

#: Suffixes read out of a directory entry (`.clinerules/`, `.kilo/`) or a command directory.
_DIR_SUFFIXES = {".md", ".mdc", ".txt", ".instructions"}

#: Directories a recursive entry never descends into: other checkouts and vendored trees
#: hold their own copies of the rule files, which are not this project's.
_SKIP_DIRS = frozenset({"worktrees", "node_modules", ".git", ".venv", "__pycache__"})

_FRONT_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_GLOB_KEYS = ("globs", "paths", "applyTo")
_ALWAYS_KEYS = ("alwaysApply", "always_apply", "alwaysapply")

#: ddflow's own managed region, in either marker grammar: not the project's rule.
_MANAGED = re.compile(r"<!--\s*ddflow:begin[^>]*-->.*?<!--\s*ddflow:end[^>]*-->", re.S | re.I)

#: Workflow ddflow owns, as phrases a project rule might restate.
_WORKFLOW: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("todo list", re.compile(r"\bto-?do\s*(\.md|list)|\btask\s+list\b|\btasks?\.md\b", re.I)),
    (
        "issue logging",
        re.compile(
            r"\blog\s+(every\s+|each\s+|any\s+)?(issue|bug)s?\b|\b(issues?|bugs?)\.md\b", re.I
        ),
    ),
    (
        "branch lifecycle",
        re.compile(
            r"\b(create|make|cut|open)\s+an?\s+(feature\s+)?branch\b|\bbranch\s+per\b", re.I
        ),
    ),
    ("lessons file", re.compile(r"\blessons?(\.md|\s+file|\s+learned)\b", re.I)),
)


def _scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw[:1] in ('"', "'") and len(raw) > 1 and raw.endswith(raw[0]):
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    return raw


def _inline_list(raw: str) -> list[str]:
    return [str(_scalar(p)) for p in raw.strip()[1:-1].split(",") if p.strip()]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a rule file into its YAML-subset frontmatter and the body.

    Reads `key: value` scalars (quotes stripped, true/false to bool), `[a, b]` lists and
    `- item` block lists: all the shapes Cursor, Claude, Copilot and Windsurf rule files use.
    No frontmatter, or one never closed, is ``({}, text)``: unreadable is not "empty rule"."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    fm: dict[str, Any] = {}
    key = ""
    for line in lines[1:end]:
        item = line.strip()
        if item.startswith("- ") and key and isinstance(fm.get(key), list):
            fm[key].append(str(_scalar(item[2:])))
            continue
        m = _FRONT_KEY.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            fm[key] = _inline_list(val)
        elif val == "":
            fm[key] = []
        else:
            fm[key] = _scalar(val)
    return fm, "\n".join(lines[end + 1 :])


def _globs(fm: dict[str, Any]) -> list[str]:
    for k in _GLOB_KEYS:
        v = fm.get(k)
        if isinstance(v, str) and v:
            return [g.strip() for g in v.split(",") if g.strip()]
        if isinstance(v, list) and v:
            return [str(g) for g in v]
    return []


def _load_mode(fm: dict[str, Any], kind: str) -> str:
    """When the file reaches the model: a command only when invoked; frontmatter decides a
    rule (path globs bind by path, a bare description is asked for); no frontmatter, always."""
    if kind == KIND_COMMAND:
        return ON_REQUEST
    if any(fm.get(k) is True for k in _ALWAYS_KEYS):
        return ALWAYS
    if _globs(fm):
        return BY_PATH
    if fm:
        return ON_REQUEST
    return ALWAYS


def _expand(repo: Path, pattern: str) -> list[Path]:
    """The files one `reads`/`dir` entry names under the repo; entries outside it ($VAR, ~)
    are not project files, and neither is anything inside another checkout (`_SKIP_DIRS`)."""
    if not pattern or pattern[0] in "$~/":
        return []
    if pattern.endswith("/"):
        base = repo / pattern.rstrip("/")
        found = [p for p in base.rglob("*") if p.suffix in _DIR_SUFFIXES] if base.is_dir() else []
    elif any(c in pattern for c in "*?["):
        found = list(repo.glob(pattern))
    else:
        found = [repo / pattern]
    return sorted(
        p for p in found if p.is_file() and not _SKIP_DIRS.intersection(p.relative_to(repo).parts)
    )


def _surfaces() -> list[tuple[str, str, str]]:
    """(agent, kind, pattern) for every instruction and command entry the registry knows."""
    out: list[tuple[str, str, str]] = []
    for h in HR.load():
        if h.instructions is not None:
            for pat in h.instructions.reads:
                kind = KIND_CROSS if "/" not in pat else KIND_RULE
                out.append((h.id, kind, pat))
        if h.commands is not None and h.commands.dir:
            out.append((h.id, KIND_COMMAND, h.commands.dir.rstrip("/") + "/"))
    return out


def scan_rule_files(repo: Path) -> list[dict[str, Any]]:
    """Every rule, cross-agent and command file present under ``repo`` that some known
    agent reads, one row per file: path, kind, frontmatter, load mode, globs, bytes,
    `loaded_by` (agents), `ddflow_owned` and the body (`text`, not for display)."""
    repo = Path(repo)
    rows: dict[str, dict[str, Any]] = {}
    for agent, kind, pat in _surfaces():
        for p in _expand(repo, pat):
            rel = p.relative_to(repo).as_posix()
            row = rows.get(rel)
            if row is None:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                fm, _body = parse_frontmatter(text)
                size = len(text.encode("utf-8"))
                row = rows[rel] = {
                    "path": rel,
                    "kind": kind,
                    "frontmatter": fm,
                    "load": _load_mode(fm, kind),
                    "globs": _globs(fm),
                    "description": str(fm.get("description", "") or ""),
                    "bytes": size,
                    "loaded_by": [],
                    "load_by": {},
                    "ddflow_owned": any(
                        r.path == rel and r.form == FORM_WHOLE for r in NATIVE_RULES.values()
                    ),
                    "text": text,
                }
            if row["kind"] == KIND_COMMAND and kind != KIND_COMMAND:
                # a rule entry that also names the file wins over a command directory,
                # whichever agent was listed first
                row["kind"] = kind
                row["load"] = _load_mode(row["frontmatter"], kind)
            if agent not in row["loaded_by"]:
                row["loaded_by"].append(agent)
            # per agent: a command-directory agent reads the file only when invoked,
            # even where another agent's rule entry makes it an always-loaded rule
            mode = _load_mode(row["frontmatter"], kind)
            if agent not in row["load_by"] or kind != KIND_COMMAND:
                row["load_by"][agent] = mode
    return sorted(rows.values(), key=lambda r: r["path"])


def _restatements(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    for r in rows:
        if r["kind"] == KIND_COMMAND or r["ddflow_owned"]:
            continue
        body = _MANAGED.sub("", r["text"])
        for topic, rx in _WORKFLOW:
            m = rx.search(body)
            if m:
                notes.append(
                    {
                        "path": r["path"],
                        "topic": topic,
                        "text": f"{r['path']} restates ddflow's {topic} ({m.group(0).strip()!r}); "
                        "ddflow owns it: the operator resolves a conflict, nothing edits the file",
                    }
                )
    return notes


def _agents(rows: list[dict[str, Any]], adopted: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for h in HR.load():
        mine = [r for r in rows if h.id in r["loaded_by"]]
        always = sum(
            r["bytes"]
            for r in mine
            if r["load_by"].get(h.id) == ALWAYS and r["kind"] != KIND_COMMAND
        )
        if not mine and h.id not in adopted:
            continue
        out[h.id] = {
            "adopted": h.id in adopted,
            "files": [r["path"] for r in mine],
            "always_bytes": always,
            "always_tokens_approx": always // CHARS_PER_TOKEN,
        }
    return out


def _adopted_ids(repo: Path) -> list[str]:
    return adopted_agents(repo)


def _cut_off(seen: dict[str, list[str]]) -> list[tuple[str, str]]:
    """(a, b) for every pair of adopted agents where b sees some rule files and a shares none
    of them: the rules in b's files do not bind a."""
    return [
        (a, b)
        for a, mine in sorted(seen.items())
        for b, theirs in sorted(seen.items())
        if a != b and theirs and not set(mine) & set(theirs)
    ]


def doctor_note(repo: Path) -> str | None:
    """A note only when one adopted agent sees none of the project rule files another
    adopted agent sees (it may see others, or none): the rules then bind only part of the fleet."""
    repo = Path(repo)
    adopted = _adopted_ids(repo)
    rows = [r for r in scan_rule_files(repo) if r["kind"] != KIND_COMMAND and not r["ddflow_owned"]]
    seen = {a: [r["path"] for r in rows if a in r["loaded_by"]] for a in adopted}
    pairs = _cut_off(seen)
    if not pairs:
        return None
    detail = "; ".join(f"{a} sees none of the rule files {b} sees" for a, b in pairs)
    return f"some adopted agents miss rules others load: {detail} (`ddflow rule inventory`)"


def inventory(repo: Path) -> dict[str, Any]:
    """The whole report: `rows` (files), `agents` (who sees what, always-loaded cost),
    `notes` (restated ddflow workflow) and `doctor_note` (None unless the fleet is split)."""
    repo = Path(repo)
    rows = scan_rule_files(repo)
    adopted = _adopted_ids(repo)
    return {
        "rows": [{k: v for k, v in r.items() if k not in ("text", "frontmatter")} for r in rows],
        "count": len(rows),
        "agents": _agents(rows, adopted),
        "adopted": adopted,
        "notes": _restatements(rows),
        "doctor_note": doctor_note(repo),
        "tokens_note": TOKENS_NOTE,
    }
