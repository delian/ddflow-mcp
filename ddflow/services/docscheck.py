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
import json
import posixpath
import re
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from ..config import EnforceConfig
from ..core.bookkeeping import is_state
from ..core.digest import content_digest
from ..infra import git as G
from .docscheck_count import check_counts
from .docscheck_extract import Extractor, surface, undocumented
from .docscheck_help import FLAG as _FLAG
from .docscheck_help import VERB as _VERB
from .docscheck_help import HelpIndex, Runner, flag_of
from .docscheck_md import (  # noqa: F401  (anchors_of, slugify: re-exported)
    SPAN,
    Headings,
    anchors_of,
    lines,
    slugify,
)
from .docscheck_types import KINDS, Finding, Report
from .docsync import glob_regex

#: Files larger than this are not scanned for names: a lockfile or a data dump is not a
#: place a document's identifier lives, and reading it costs the second the check lacks.
_MAX_CODE_BYTES = 1_000_000

_LINK = re.compile(
    r"(?<!\\)!?\[[^\]\n]*\]\(\s*<?((?:[^()\s>]|\([^()\s]*\))+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
#: `[label]: destination "optional title"`. A footnote (`[^1]: prose`) is not one, and the
#: destination must end the line or be followed only by a title, so prose never reads as a link.
_REFDEF = re.compile(
    r"""^\s{0,3}\[(?!\^)[^\]]+\]:\s*<?([^\s<>]+)>?(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*$"""
)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

_SNAKE = re.compile(r"_*[A-Za-z][A-Za-z0-9]*(?:_+[A-Za-z0-9]+)+")
_CAMEL = re.compile(r"[a-z]+(?:[A-Z][a-z0-9]*)+")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CORPUS_FLAG = re.compile(r"(?<![\w-])--[a-z](?:[a-z0-9-]*[a-z0-9])?")
_QUOTED = re.compile(r"""["']([a-z][a-z0-9-]*)["']""")
#: A file that registers command-line verbs (argparse, click, typer, cobra, commander). Only
#: the quoted strings and function names of such files can be a verb: `"setup"` is quoted all
#: over a project that merely has a setup tool.
_REGISTERS = ("add_parser", "add_subparsers", "click.", "typer.", ".command(", "cobra.", "Use:")
_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([a-z][a-z0-9_]*)", re.M)
_VERB_WORDS = 2  #: `<script> <verb>`

_EXT = (
    "py|md|rst|adoc|toml|json|jsonl|yml|yaml|sh|txt|cfg|ini|lock|rs|go|ts|tsx|js|jsx|html|css|"
    "c|h|cpp|hpp|java|kt|swift|php|rb|pl|lua|sql|xml|csv|tsv|log|svg|png|jpg|jpeg|gif|webp|ico|pdf|"
    "zip|tar|gz|tgz|whl|exe|dll|so|ps1|bat|cmd|env|conf|proto|tf|tfvars|vue|mjs|cjs|ipynb|parquet|"
    "db|sqlite|pkl|npy|npz|pt|pth|bin|onnx|gguf|safetensors|ckpt|md5|sha256"
)
_PATHY = re.compile(rf"[\w.\-]+\.(?:{_EXT})|[\w.\-]+(?:/[\w.\-]+)+/?")

#: A dotted name ending in one of these is a host name or a file, not an identifier.
_DOTTED_NOT_NAMES = frozenset(_EXT.split("|")) | {"com", "org", "io", "dev", "net", "ai", "app"}

DEFAULT_DOC_GLOBS = tuple(EnforceConfig().doc_globs)
DEFAULT_DOC_EXCLUDE = tuple(EnforceConfig().doc_exclude)


def _is_export_file(root, rel: str) -> bool:
    """True when ``rel`` starts with a ddflow export header (a generated document)."""
    try:
        with open(root / rel, "rb") as f:
            first = f.readline(512)
    except OSError:
        return False
    return first.startswith(b"<!-- ddflow:generated doc=")


def check_docs(  # noqa: PLR0913 -- one keyword per option of a public entry point
    repo,
    *,
    docs: list[str] | None = None,
    doc_globs: list[str] | tuple[str, ...] = DEFAULT_DOC_GLOBS,
    doc_exclude: list[str] | tuple[str, ...] = DEFAULT_DOC_EXCLUDE,
    ignore_paths: list[str] | tuple[str, ...] = (),
    commands: list[str] | None = None,
    ignore_names: list[str] | tuple[str, ...] = (),
    generated: list[str] | tuple[str, ...] = (),
    extract: Sequence[Mapping] = (),
    count: Sequence[Mapping] = (),
    runner: Runner | None = None,
    help_commands: list[str] | None = None,
) -> Report:
    """Check ``docs`` (default: tracked files matching ``doc_globs`` minus ``doc_exclude``)
    against the tracked tree of ``repo``.

    ``ignore_paths`` are globs of path-like references that are not worth even a note.
    ``commands`` are the script names whose `<script> <verb>` references are verified
    (default: the project's own `[project.scripts]` / package.json `bin`). ``ignore_names``
    are fnmatch patterns of identifiers and flags another tool owns (`ddflow_*` in an
    agent-instructions page of a project that merely uses ddflow). ``generated`` are
    repo-relative paths of export targets (``shared_files.export_targets``): a generated
    document is checked by ``ddflow export --check``, so it is neither scanned nor named
    explicitly here, and a tracked file whose first line is an export header is left out
    the same way. Raises
    ``extract`` are ``[[docs.extract]]`` entries (names the code offers that no document
    mentions), ``count`` are ``[[docs.count]]`` entries (``<!-- ddflow:count -->`` markers
    are always read). ``runner`` runs commands (``docscheck_help.command_runner``); without
    one nothing is spawned, so ``<tool> <verb> --flag`` mentions are checked only against the
    verb list and counts are noted as not recomputed. ``help_commands`` are the tools whose
    ``--help`` is walked (default: ``commands``). Raises
    OSError when git cannot list the tree or a named document cannot be read: "could not tell" is
    never a clean report.
    """

    root = Path(repo)
    files = _tree(root)
    exclude = [glob_regex(g) for g in doc_exclude]
    doc_re = [glob_regex(g) for g in doc_globs]
    is_doc = lambda p: any(r.fullmatch(p) for r in doc_re)  # noqa: E731
    skip = set(generated)
    if docs is None:
        docs = [p for p in files if is_doc(p) and not any(r.fullmatch(p) for r in exclude)]
    docs = sorted(p for p in set(docs) if p not in skip and not _is_export_file(root, p))
    corpus = _Corpus.build(
        root, [p for p in files if not is_doc(p) and p not in docs and not is_state(p)]
    )
    cmds = set(_project_commands(root) if commands is None else commands)
    ignore = [glob_regex(g) for g in ignore_paths]
    names = [re.compile(fnmatch.translate(g)) for g in ignore_names]
    tree = _Tree(files)
    walked = list(cmds if help_commands is None else help_commands)
    help_ = HelpIndex(runner, walked) if runner is not None and walked else None
    texts, unreadable = {}, []
    for d in docs:
        try:
            texts[d] = (root / d).read_text("utf-8", errors="replace")
        except OSError:
            unreadable.append(d)
    if unreadable:
        # a document nobody read is "could not tell", never a clean report
        raise OSError(f"cannot read: {', '.join(unreadable)}")
    heads = Headings(root, texts)

    report = Report()
    counts: Counter = Counter()
    for d in sorted(texts):
        scan = _Scan(d, texts[d], corpus, tree, heads, cmds, ignore, names, report, counts)
        scan.help = help_
        scan.run()
    if extract:
        found = surface(
            root,
            [p for p in files if not is_doc(p) and not is_state(p)],
            [Extractor.from_config(e) for e in extract],
        )
        report.findings += undocumented(
            found, texts.values(), lambda n: any(r.match(n) for r in names)
        )
        counts["extracted"] = len(found)
    check_counts(report, texts, count, runner, walked, counts)
    if runner is None and help_commands:  # asked for, not done: nobody looked
        report.notes.append(
            Finding(
                "help", "", 0, ",".join(help_commands), "verb flags not checked: no command runner"
            )
        )
    for tool, why in sorted((help_.unavailable if help_ else {}).items()):
        report.notes.append(Finding("help", "", 0, tool, f"flags of its verbs not checked: {why}"))
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
    report.digest = content_digest(blob)
    return report


# ---------------------------------------------------------------------------------------
# the tree


def _tree(root) -> list[str]:
    """Tracked files plus untracked-but-not-ignored ones: a doc written alongside the file
    it links to is checked before either is committed."""
    names = G.files(root, "all")
    if names is None:
        raise OSError(f"git ls-files failed in {root}")
    return sorted({n for n in names if (root / n).is_file()})


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
# one document


class _Scan:
    def __init__(
        self, path, text, corpus, tree, heads, cmds, ignore, names, report, counts
    ) -> None:
        self.path, self.text = path, text
        self.corpus, self.tree, self.heads = corpus, tree, heads
        self.cmds, self.ignore, self.names = cmds, ignore, names
        self.report, self.counts = report, counts
        self.help: HelpIndex | None = None
        self.dir = posixpath.dirname(path)

    def run(self) -> None:
        for n, line, code in lines(self.text):
            if code:  # fence markers are no command either: the first word must be a script
                self._command(line.strip().lstrip("$ ").strip(), n)
                continue
            prose = SPAN.sub(lambda s: " " * len(s.group(0)), line)
            for lm in _LINK.finditer(prose):
                self._link(lm.group(1), n)
            ref = _REFDEF.match(prose)
            if ref:
                self._link(ref.group(1), n)
            for sm in SPAN.finditer(line):
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
        walked = bool(words) and self.help is not None and words[0] in self.help.commands
        if len(words) < _VERB_WORDS or not (words[0] in self.cmds or walked):
            return False
        verb = words[1]
        if not _VERB.fullmatch(verb):
            return False
        self.counts["commands"] += 1
        got = self.help.resolve(words) if walked else None
        if (
            words[0] in self.cmds
            and verb not in self.corpus.verbs
            and not (got and len(got[0]) > 1)
        ):
            self.report.findings.append(
                Finding(
                    "command",
                    self.path,
                    n,
                    f"{words[0]} {verb}",
                    f"no tracked code registers {verb!r} as a command",
                )
            )
        elif got:  # --help is the authority for a walked tool
            self._verb_flags(words, n, got)
        return True

    def _verb_flags(self, words: list[str], n: int, got: tuple[list[str], set[str]]) -> None:
        """The flags of ``<tool> <verb>...`` must be ones that verb's own ``--help`` lists."""
        chain, flags = got
        for tok in words[len(chain) :]:
            flag = flag_of(tok)
            if flag is None or self._owned(flag):
                continue
            self.counts["verb_flags"] += 1
            if flag not in flags:
                self.report.findings.append(
                    Finding(
                        "flag",
                        self.path,
                        n,
                        f"{' '.join(chain)} {flag}",
                        f"`{' '.join(chain)} --help` lists no {flag}",
                    )
                )

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
