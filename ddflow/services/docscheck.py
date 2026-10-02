"""Documentation check: does what the docs name still exist?

A mechanical report over an explicit set of documents (decision D-docs-stage). It reads the
documents and the tracked tree and compares them; it never edits either.

FINDINGS (a reader following the doc would hit a wall):

* an **identifier** in inline code -- `snake_case`, `camelCase`, a dotted name -- that
  appears nowhere in tracked non-doc files or tracked paths;
* a **flag** (`--long-flag`, trailing punctuation stripped) that no tracked code spells;
* a **command** (`<script> <verb>` where <script> is a command the project installs) whose
  verb is never a quoted string in the code -- `ddflow setup` when setup is only an MCP
  tool;
* a relative **link** whose target is not in the tree, and an **anchor** (`#heading`,
  `file.md#heading`) that names no heading there, with GitHub's slug rules.

NOTES (informational, never findings): path-like inline code that names nothing in the
tree. Measured at only 10-30% precise -- runtime paths, generated files and illustrative
paths -- so a note carries an ignore list instead of failing anyone.

History pages (changelogs, session logs) say what was true when they were written; they
are left out by ``doc_exclude``, which is also why this checks a named set and not the whole
tree. Fenced code blocks are skipped for identifiers (they are examples, not claims) and
read only for `<script> <verb>` lines.

Stdlib only; one `git ls-files` plus a read of each tracked text file, well under a second
on a project of a few hundred files.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import posixpath
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import unquote

from ..config import EnforceConfig
from ..infra import proc as P
from .docsync import glob_regex

#: Files larger than this are not scanned for names: a lockfile or a data dump is not a
#: place a document's identifier lives, and reading it costs the second the check lacks.
_MAX_CODE_BYTES = 1_000_000

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
#: A line that cannot continue a paragraph: a list item, a quote or a table row.
_NOT_PARAGRAPH = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>|\|)")
_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_LINK = re.compile(
    r"(?<!\\)!?\[[^\]\n]*\]\(\s*<?((?:[^()\s>]|\([^()\s]*\))+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
#: `[label]: destination "optional title"`. A footnote (`[^1]: prose`) is not one, and the
#: destination must end the line or be followed only by a title, so prose never reads as a link.
_REFDEF = re.compile(
    r"""^\s{0,3}\[(?!\^)[^\]]+\]:\s*<?([^\s<>]+)>?(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*$"""
)
_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HTML_ANCHOR = re.compile(r"""<a\s[^>]*?\b(?:name|id)\s*=\s*["']([^"']+)["']""", re.I)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

_SNAKE = re.compile(r"_*[A-Za-z][A-Za-z0-9]*(?:_+[A-Za-z0-9]+)+")
_CAMEL = re.compile(r"[a-z]+(?:[A-Z][a-z0-9]*)+")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_FLAG = re.compile(r"--[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CORPUS_FLAG = re.compile(r"(?<![\w-])--[a-z](?:[a-z0-9-]*[a-z0-9])?")
_QUOTED = re.compile(r"""["']([a-z][a-z0-9-]*)["']""")
#: A file that registers command-line verbs (argparse, click, typer, cobra, commander). Only
#: the quoted strings and function names of such files can be a verb: `"setup"` is quoted all
#: over a project that merely has a setup tool.
_REGISTERS = ("add_parser", "add_subparsers", "click.", "typer.", ".command(", "cobra.", "Use:")
_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([a-z][a-z0-9_]*)", re.M)
_VERB_WORDS = 2  #: `<script> <verb>`
_VERB = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")

_EXT = (
    "py|md|rst|adoc|toml|json|jsonl|yml|yaml|sh|txt|cfg|ini|lock|rs|go|ts|tsx|js|jsx|html|css|"
    "c|h|cpp|java|rb|sql|xml|csv|log|svg|png"
)
_PATHY = re.compile(rf"[\w.\-]+\.(?:{_EXT})|[\w.\-]+(?:/[\w.\-]+)+/?")

#: A dotted name ending in one of these is a host name or a file, not an identifier.
_DOTTED_NOT_NAMES = frozenset(_EXT.split("|")) | {"com", "org", "io", "dev", "net", "ai", "app"}

DEFAULT_DOC_GLOBS = tuple(EnforceConfig().doc_globs)
DEFAULT_DOC_EXCLUDE = tuple(EnforceConfig().doc_exclude)

#: Finding kinds, in the order a report lists them.
KINDS = ("identifier", "flag", "command", "link", "anchor")


@dataclass(frozen=True)
class Finding:
    kind: str  #: one of KINDS, or "path" for a note
    path: str  #: the document
    line: int
    ref: str  #: what the document names
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "ref": self.ref,
            "detail": self.detail,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    notes: list[Finding] = field(default_factory=list)
    #: What was looked at: the documents and how many references of each kind were checked.
    checked: dict = field(default_factory=dict)
    digest: str = ""

    @property
    def clean(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        return {
            "findings": [f.as_dict() for f in self.findings],
            "notes": [n.as_dict() for n in self.notes],
            "checked": self.checked,
            "digest": self.digest,
        }


def check_docs(
    repo,
    *,
    docs: list[str] | None = None,
    doc_globs: list[str] | tuple[str, ...] = DEFAULT_DOC_GLOBS,
    doc_exclude: list[str] | tuple[str, ...] = DEFAULT_DOC_EXCLUDE,
    ignore_paths: list[str] | tuple[str, ...] = (),
    commands: list[str] | None = None,
    ignore_names: list[str] | tuple[str, ...] = (),
) -> Report:
    """Check ``docs`` (default: tracked files matching ``doc_globs`` minus ``doc_exclude``)
    against the tracked tree of ``repo``.

    ``ignore_paths`` are globs of path-like references that are not worth even a note.
    ``commands`` are the script names whose `<script> <verb>` references are verified
    (default: the project's own `[project.scripts]` / package.json `bin`). ``ignore_names``
    are fnmatch patterns of identifiers and flags another tool owns (`ddflow_*` in an
    agent-instructions page of a project that merely uses ddflow). Raises
    OSError when git cannot list the tree or a named document cannot be read: "could not tell" is
    never a clean report.
    """
    from pathlib import Path

    root = Path(repo)
    files = _tree(root)
    exclude = [glob_regex(g) for g in doc_exclude]
    doc_re = [glob_regex(g) for g in doc_globs]
    is_doc = lambda p: any(r.fullmatch(p) for r in doc_re)  # noqa: E731
    if docs is None:
        docs = [p for p in files if is_doc(p) and not any(r.fullmatch(p) for r in exclude)]
    docs = sorted(set(docs))
    corpus = _Corpus.build(
        root, [p for p in files if not is_doc(p) and not p.startswith(".ddflow/")]
    )
    cmds = set(_project_commands(root) if commands is None else commands)
    ignore = [glob_regex(g) for g in ignore_paths]
    names = [re.compile(fnmatch.translate(g)) for g in ignore_names]
    tree = _Tree(files)
    texts, unreadable = {}, []
    for d in docs:
        try:
            texts[d] = (root / d).read_text("utf-8", errors="replace")
        except OSError:
            unreadable.append(d)
    if unreadable:
        # a document nobody read is "could not tell", never a clean report
        raise OSError(f"cannot read: {', '.join(unreadable)}")
    heads = _Headings(root, texts)

    report = Report()
    counts: Counter = Counter()
    for d in sorted(texts):
        scan = _Scan(d, texts[d], corpus, tree, heads, cmds, ignore, names, report, counts)
        scan.run()
    report.findings.sort(key=lambda f: (f.path, f.line, KINDS.index(f.kind), f.ref))
    report.notes.sort(key=lambda f: (f.path, f.line, f.ref))
    report.checked = {"docs": sorted(texts), **dict(sorted(counts.items()))}
    blob = json.dumps(
        {
            "findings": [f.as_dict() for f in report.findings],
            "notes": [n.as_dict() for n in report.notes],
            "checked": report.checked,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    report.digest = hashlib.sha256(blob.encode()).hexdigest()
    return report


# ---------------------------------------------------------------------------------------
# the tree


def _tree(root) -> list[str]:
    """Tracked files plus untracked-but-not-ignored ones: a doc written alongside the file
    it links to is checked before either is committed."""
    r = P.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        timeout=60,
    )
    if r.returncode != 0:
        raise OSError(f"git ls-files failed in {root}: {r.stderr.decode('utf-8', 'replace')[:200]}")
    names = r.stdout.decode("utf-8", "surrogateescape").split("\0")
    return sorted({n for n in names if n and (root / n).is_file()})


class _Tree:
    def __init__(self, files: list[str]) -> None:
        self.files = set(files)
        self.dirs = {d for f in files for d in _parents(f)}
        self.suffixes = {f.split("/", i)[-1] for f in files for i in range(f.count("/") + 1)}

    def has(self, path: str) -> bool:
        path = path.rstrip("/")
        return path in self.files or path in self.dirs


def _parents(path: str):
    parts = path.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        yield "/".join(parts[:i])


@dataclass
class _Corpus:
    """Every name the code spells: words, `--flags` and quoted verbs, from tracked non-doc
    files and from the tracked paths themselves."""

    words: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    verbs: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, root, paths: list[str]) -> _Corpus:
        c = cls()
        for p in paths:
            c.words.update(_WORD.findall(p))
            try:
                with open(root / p, "rb") as fh:
                    raw = fh.read(_MAX_CODE_BYTES + 1)
            except OSError:
                continue
            if len(raw) > _MAX_CODE_BYTES or b"\0" in raw[:8192]:
                continue
            text = raw.decode("utf-8", errors="ignore")
            c.words.update(_WORD.findall(text))
            c.flags.update(_CORPUS_FLAG.findall(text))
            if any(m in text for m in _REGISTERS):
                c.verbs.update(_QUOTED.findall(text))
                c.verbs.update(d.replace("_", "-") for d in _DEF.findall(text))
        return c


def _project_commands(root) -> list[str]:
    """Scripts the project installs: `[project.scripts]` and package.json `bin`."""
    out: list[str] = []
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
        out += list(data.get("project", {}).get("scripts", {}))
    except (OSError, ValueError):
        pass
    try:
        bin_ = json.loads((root / "package.json").read_text("utf-8")).get("bin", {})
        out += list(bin_) if isinstance(bin_, dict) else []
    except (OSError, ValueError):
        pass
    return out


# ---------------------------------------------------------------------------------------
# headings


def slugify(heading: str) -> str:
    """GitHub's anchor for a heading: markup stripped, lower-cased, punctuation dropped
    (hyphens and underscores kept), each space a hyphen."""
    h = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    h = re.sub(r"<[^>]+>", "", h)
    h = re.sub(r"[`*~]", "", h).strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


class _Headings:
    def __init__(self, root, texts: dict[str, str]) -> None:
        self.root = root
        self.texts = texts
        self._cache: dict[str, set[str] | None] = {}

    def of(self, path: str) -> set[str] | None:
        """The anchors ``path`` offers; None when it cannot be read (could not tell)."""
        if path not in self._cache:
            text = self.texts.get(path)
            if text is None:
                try:
                    text = (self.root / path).read_text("utf-8", errors="replace")
                except OSError:
                    self._cache[path] = None
                    return None
            self._cache[path] = anchors_of(text)
        return self._cache[path]


def _lines(text: str):
    """(number, line, in_code) for each line; fence lines themselves are in_code. A fence
    closes only on a run of the SAME character at least as long as the one that opened it
    and carrying no info string (CommonMark), so a short fence inside a long one is code."""
    opened: tuple[str, int] | None = None
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if opened is None:
            if m and not ("`" in m.group(2) and m.group(1)[0] == "`"):
                opened = (m.group(1)[0], len(m.group(1)))
            yield n, line, opened is not None
        else:
            yield n, line, True
            if (
                m
                and m.group(1)[0] == opened[0]
                and len(m.group(1)) >= opened[1]
                and not m.group(2).strip()
            ):
                opened = None


def anchors_of(text: str) -> set[str]:
    """Every anchor a markdown document offers, lower-cased: heading slugs (ATX and
    setext; GitHub numbers repeats `-1`, `-2`, ...) and explicit `<a name=...>` / `id=...`
    targets."""
    out: set[str] = set()
    seen: Counter = Counter()

    def add(heading: str) -> None:
        slug = slugify(heading)
        n = seen[slug]
        seen[slug] += 1
        out.add(slug if n == 0 else f"{slug}-{n}")

    paragraph: list[str] = []  #: prose lines so far; a setext underline makes them a heading
    for _, line, code in _lines(text):
        if code:
            paragraph = []
            continue
        h = _ATX.match(line)
        if h:
            add(h.group(2))
            paragraph = []
        elif paragraph and _SETEXT.match(line):
            add(" ".join(paragraph))
            paragraph = []
        elif line.strip() and not (_SETEXT.match(line) or _NOT_PARAGRAPH.match(line)):
            paragraph.append(line.strip())
        else:
            paragraph = []
        out.update(a.lower() for a in _HTML_ANCHOR.findall(line))
    return out


# ---------------------------------------------------------------------------------------
# one document


class _Scan:
    def __init__(
        self, path, text, corpus, tree, heads, cmds, ignore, names, report, counts
    ) -> None:
        self.path, self.text = path, text
        self.corpus, self.tree, self.heads = corpus, tree, heads
        self.cmds, self.ignore, self.names = cmds, ignore, names
        self.report, self.counts = report, counts
        self.dir = posixpath.dirname(path)

    def run(self) -> None:
        for n, line, code in _lines(self.text):
            if code:  # fence markers are no command either: the first word must be a script
                self._command(line.strip().lstrip("$ ").strip(), n)
                continue
            prose = _SPAN.sub(lambda s: " " * len(s.group(0)), line)
            for lm in _LINK.finditer(prose):
                self._link(lm.group(1), n)
            ref = _REFDEF.match(prose)
            if ref:
                self._link(ref.group(1), n)
            for sm in _SPAN.finditer(line):
                self._span(sm.group(2).strip(), n)

    # -- inline code

    def _span(self, s: str, n: int) -> None:
        if not s or "<" in s or "{" in s:
            return
        head = s.split(None, 1)[0] if " " in s else s
        if " " in s and self._command(s, n):
            return
        if head.startswith("--"):
            flag = re.split(r"[=\s]", head, maxsplit=1)[0].rstrip(".,;:)")
            if _FLAG.fullmatch(flag):
                self._flag(flag, n)
            return
        if " " in s:
            return
        tok = s.removesuffix("()").rstrip(".,;:")
        tok = tok.split("=", 1)[0]
        if _PATHY.fullmatch(tok):
            self._path(tok, n)
        elif _FLAG.fullmatch(tok):
            self._flag(tok, n)
        elif _SNAKE.fullmatch(tok) or _CAMEL.fullmatch(tok):
            self._ident(tok, n)
        elif _DOTTED.fullmatch(tok):
            parts = tok.split(".")
            if parts[-1] not in _DOTTED_NOT_NAMES:
                self._ident(tok, n, parts)

    def _owned(self, name: str) -> bool:
        return any(r.fullmatch(name) for r in self.names)

    def _ident(self, tok: str, n: int, parts: list[str] | None = None) -> None:
        if self._owned(tok):
            return
        self.counts["identifiers"] += 1
        missing = [p for p in (parts or [tok]) if p not in self.corpus.words]
        if missing:
            self.report.findings.append(
                Finding("identifier", self.path, n, tok, "named in no tracked code or path")
            )

    def _flag(self, flag: str, n: int) -> None:
        if self._owned(flag):
            return
        self.counts["flags"] += 1
        if flag not in self.corpus.flags:
            self.report.findings.append(
                Finding("flag", self.path, n, flag, "no tracked code spells this flag")
            )

    def _command(self, s: str, n: int) -> bool:
        """`<script> <verb> ...`: True when ``s`` is such a reference (checked)."""
        words = s.split()
        if len(words) < _VERB_WORDS or words[0] not in self.cmds:
            return False
        verb = words[1]
        if not _VERB.fullmatch(verb):
            return False
        self.counts["commands"] += 1
        if verb not in self.corpus.verbs:
            self.report.findings.append(
                Finding(
                    "command",
                    self.path,
                    n,
                    f"{words[0]} {verb}",
                    f"no tracked code registers {verb!r} as a command",
                )
            )
        return True

    def _path(self, tok: str, n: int) -> None:
        if any(c in tok for c in "*~$<") or tok.startswith(("/", "http")):
            return
        self.counts["paths"] += 1
        want = posixpath.normpath(posixpath.join(self.dir, tok))
        if self.tree.has(want) or self.tree.has(tok) or tok.rstrip("/") in self.tree.suffixes:
            return
        if any(r.fullmatch(tok) for r in self.ignore):
            return
        self.report.notes.append(Finding("path", self.path, n, tok, "not in the tree"))

    # -- links

    def _link(self, url: str, n: int) -> None:
        if _SCHEME.match(url) or url.startswith("//") or "{" in url or "<" in url:
            return
        self.counts["links"] += 1
        target, _, frag = url.partition("#")
        target = unquote(target.partition("?")[0])
        frag = unquote(frag)
        if target:
            base = (
                target.lstrip("/") if target.startswith("/") else posixpath.join(self.dir, target)
            )
            resolved = posixpath.normpath(base)
            if resolved.startswith("..") or not self.tree.has(resolved):
                self.report.findings.append(
                    Finding("link", self.path, n, url, f"{resolved} is not in the tree")
                )
                return
        else:
            resolved = self.path
        if frag and resolved in self.tree.files and resolved.lower().endswith((".md", ".markdown")):
            offered = self.heads.of(resolved)
            if offered is None:
                return  # could not read the target: not checked, so not counted
            self.counts["anchors"] += 1
            if frag.lower() not in offered:
                self.report.findings.append(
                    Finding("anchor", self.path, n, url, f"no heading #{frag} in {resolved}")
                )
