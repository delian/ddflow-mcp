"""The project's own skills, commands and rules files, ranked against a task.

Read-only inventory: only the name, a one-line description and a path are kept. Content is
never copied into the brief -- the agent's own tooling loads the file when it is wanted.
Ranking is BM25 over name + description (+ a bounded head of the body), by the search core's
ranker (`searchcore/rank.py`), computed in memory because this inventory is tiny and has no index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..core import textsim
from .searchcore.rank import bm25

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


def inventory(repo: Path) -> list[Entry]:
    """Every skill, command and rules file the project carries, capped at MAX_FILES."""
    repo = Path(repo)
    found: list[Entry | None] = []
    for p in sorted((repo / ".claude" / "skills").glob("*/SKILL.md")):
        found.append(_entry("skill", repo, p, p.parent.name))
    for p in sorted((repo / ".claude" / "commands").glob("**/*.md")):
        found.append(_entry("command", repo, p, p.stem))
    for sub, pattern in (
        (".cursor/rules", "*.mdc"),
        (".cursor/rules", "*.md"),
        (".kilo", "**/*.md"),
        (".kilocode/rules", "**/*.md"),
        (".clinerules", "**/*.md"),
    ):
        for p in sorted((repo / sub).glob(pattern)):
            if p.is_file():
                found.append(_entry("rule", repo, p, p.stem))
    for rel in ("AGENTS.md", "CLAUDE.md"):
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
