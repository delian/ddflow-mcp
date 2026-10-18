"""The project's own skills, commands and rules files, ranked against a task.

Read-only inventory: only the name, a one-line description and a path are kept. Content is
never copied into the brief -- the agent's own tooling loads the file when it is wanted.
Ranking is BM25 over name + description (+ a bounded head of the body), by the search core's
ranker (`core/rank.py`), computed in memory because this inventory is tiny and has no index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..core import textsim
from ..core.rank import bm25
from . import harnessreg as HR

MAX_FILES = 200
BODY_HEAD_CHARS = 1500
_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
_DDFLOW_BLOCK = re.compile(
    r"<!--\s*ddflow[^>]*?(?:begin|start)[^>]*-->.*?<!--\s*ddflow[^>]*?end[^>]*-->", re.S | re.I
)


@dataclass(frozen=True)
class Entry:
    kind: str  # skill | command | rule
    name: str
    description: str
    path: str  # repo-relative, posix
    text: str  # what is ranked; never rendered

    def line(self) -> str:
        desc = f" — {self.description}" if self.description else ""
        return f"- {self.kind} **{self.name}**{desc} (`{self.path}`)"


def _tokens(text: str) -> list[str]:
    return textsim.tokens(text)


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            meta[k.strip().lower()] = v.strip().strip("\"'")
    return meta, text[m.end() :]


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _first_line(body: str) -> str:
    for line in body.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:140]
    return ""


def _entry(kind: str, repo: Path, path: Path, name: str) -> Entry | None:
    text = _read(path)
    if not text.strip():
        return None
    meta, body = _frontmatter(text)
    desc = (meta.get("description") or _first_line(body))[:200]
    name = meta.get("name") or name
    rel = path.relative_to(repo).as_posix()
    return Entry(kind, name, desc, rel, f"{name} {desc} {body[:BODY_HEAD_CHARS]}")


def _agent_file(repo: Path, rel: str) -> Entry | None:
    path = repo / rel
    if not path.is_file():
        return None
    meta, body = _frontmatter(_DDFLOW_BLOCK.sub("", _read(path)))
    if not body.strip():
        return None
    desc = (meta.get("description") or _first_line(body))[:200]
    return Entry("rule", rel, desc, rel, f"{rel} {desc} {body[:BODY_HEAD_CHARS]}")


def _project_dirs(section: str) -> list[str]:
    """Each distinct project-relative directory a descriptor gives for ``section``
    (``skills``, ``commands``), in rank order; home-rooted ones are not the project's."""
    out: dict[str, None] = {}
    for h in HR.load():
        sec = getattr(h, section)
        if sec is not None and not sec.none and sec.dir and not sec.dir.startswith("~"):
            out.setdefault(sec.dir.rstrip("/"))
    return list(out)


def subagent_files(repo: Path) -> list[Path]:
    """Every subagent definition in the project, from each descriptor's ``subagents.dir``."""
    return [p for d in _project_dirs("subagents") for p in sorted((Path(repo) / d).glob("*.md"))]


def _rule_globs() -> tuple[list[str], list[str]]:
    """(rule globs, top-level instruction files) from every descriptor's ``instructions.reads``.

    A read with a directory part is a rules surface (``.cursor/rules/*.mdc``, ``.clinerules/``);
    one without is a file at the root (``AGENTS.md``, ``CLAUDE.md``, ``GEMINI.md``). Only
    markdown is inventoried: ``.aider.conf.yml`` and ``.goosehints`` are not rules text.
    """
    globs: dict[str, None] = {}
    files: dict[str, None] = {}
    for h in HR.load():
        for r in h.instructions.reads if h.instructions else ():
            if r.endswith("/"):
                globs.setdefault(r + "**/*.md")
            elif "/" in r and r.endswith((".md", ".mdc")):
                globs.setdefault(r)
            elif "/" not in r and r.endswith(".md"):
                files.setdefault(r)
    return list(globs), list(files)


def inventory(repo: Path) -> list[Entry]:
    """Every skill, command and rules file the project carries, capped at MAX_FILES.

    Where each lives is read from the harness descriptors (``ddflow/harnesses``), so a new
    agent's directories are inventoried without a change here.
    """
    repo = Path(repo)
    found: list[Entry | None] = []
    seen: set[Path] = set()

    def add(kind: str, p: Path, name: str) -> None:
        if p.is_file() and p not in seen:
            seen.add(p)
            found.append(_entry(kind, repo, p, name))

    for d in _project_dirs("skills"):
        for p in sorted((repo / d).glob("*/SKILL.md")):
            add("skill", p, p.parent.name)
    for d in _project_dirs("commands"):
        for p in sorted((repo / d).glob("**/*.md")):
            add("command", p, p.stem)
    patterns, root_files = _rule_globs()
    for pattern in patterns:
        for p in sorted(repo.glob(pattern)):
            add("rule", p, p.stem)
    for rel in root_files:
        found.append(_agent_file(repo, rel))
    return [e for e in found if e][:MAX_FILES]


def rank(entries: list[Entry], query: str, limit: int = 3) -> list[Entry]:
    """BM25 (k1=1.5, b=0.75) of `query` against each entry; zero-score entries are dropped."""
    q = set(_tokens(query))
    if not q or not entries:
        return []
    scores = bm25([_tokens(e.text) for e in entries], q)
    order = sorted(scores, key=lambda i: (-scores[i], entries[i].path))
    return [entries[i] for i in order[:limit]]


def relevant(repo: Path, query: str, limit: int = 3) -> list[Entry]:
    return rank(inventory(repo), query, limit)
