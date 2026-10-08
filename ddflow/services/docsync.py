"""B17 — doc-surface sync, driven by the diff.

For every identifier, knob or default a commit removes or renames, grep the documentation
for mentions the commit leaves behind. A stale page is worse than a missing one: a reader
who finds nothing reads the code, while a reader who finds a wrong default trusts it.
"Update the docs when you rename something" exists in prose in most projects and is
therefore skipped; this is the mechanical half.

What counts as REMOVED, all from the staged `-U0` diff of non-doc files:

* an **identifier** on a removed line that is on no added line of the diff and, after the
  commit, appears in no non-doc file and no tracked path. Only identifier-SHAPED tokens —
  `snake_case`, `camelCase`, `--long-flag` — because a plain word ("merge", "lease") is
  prose as often as it is a name, and a check that fires on prose teaches `--no-verify`;
* the **stem of a file** the commit deletes or renames away (`probe_01_x.py` is cited by
  its name, and nothing inside it says so);
* a **default**: `name = old` removed and `name = new` added for the same identifier-shaped
  name. A doc line carrying both the name and the old value is stale.

A mention on a doc line the SAME commit adds is exempt: "renamed from `old_name`" is the
commit documenting itself, not a stale page.

Measured before it was built (research Rc2a19c455d): replayed over this repository's own
110 commits, 113 identifiers had left the code and 3 doc lines still named one — all three
genuinely stale. And one trap found on the way: `git grep -o` with many `-F` patterns
under-reports which patterns occur (lesson L5808fe73f8), so liveness is decided by
re-matching whole lines here, never by trusting `-o`.
"""

from __future__ import annotations

import codecs
import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..core import globs
from ..infra import git as G

#: An identifier-shaped token. The lookbehind refuses a backslash so the `\n` of
#: `"\nBlocked"` does not make `nBlocked` a camelCase name -- the replay's main noise.
TOKEN = re.compile(
    r"(?<![\w\\-])"
    r"(?:--[a-z][a-z0-9-]*[a-z0-9]"  # --long-flag
    r"|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"  # snake_case, SCREAMING_CASE
    r"|[a-z]+[A-Z][A-Za-z0-9]*)"  # camelCase
    r"(?![\w-])"
)

#: `name = value` or `name: type = value`, as Python, TOML and most config files spell a
#: default. A quoted value is taken whole -- it may hold a `,` or `#` -- and any other value
#: up to a trailing comment or comma.
ASSIGN = re.compile(
    r"""^\s*([A-Za-z_]\w*)\s*(?::[^=]*)?=\s*("[^"\n]*"|'[^'\n]*'|[^#,]+?)\s*,?\s*(?:#.*)?$"""
)

#: A default worth grepping for: a number, a quoted string or a boolean. `x = foo(bar)`
#: is code, not a default a page would quote.
SIMPLE_VALUE = re.compile(r"""^(?:-?\d+(?:\.\d+)?|"[^"\n]*"|'[^'\n]*'|true|false|True|False)$""")

#: git grep patterns per invocation, well under any platform's argv limit.
_BATCH = 200

#: `git grep -z -n` prints `path\0line\0text`.
_GREP_FIELDS = 3


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    name: str  #: the removed identifier, or `name = old` for a changed default
    text: str


@dataclass
class Removed:
    """What the diff took away, and where it added doc lines."""

    names: set[str] = field(default_factory=set)
    defaults: dict[str, str] = field(default_factory=dict)  #: name -> OLD value, unquoted
    added: set[tuple[str, int]] = field(default_factory=set)  #: (doc path, line) it adds


def glob_regex(glob: str) -> re.Pattern[str]:
    """``glob`` with git's `:(glob)` pathspec meaning, so a path this module calls a doc is
    exactly a path `git grep` searched as one (`core.globs`): `*` stops at `/`, `**/` is
    any number of directories (including none), a trailing `/**` is everything below."""
    return globs.regex(glob)


def is_doc(path: str, doc_globs: list[str]) -> bool:
    return any(globs.match(path, g) for g in doc_globs)


def _unquote(raw: str) -> str:
    """A diff header path. git C-quotes a name holding a quote, backslash or control
    character even with `core.quotepath=false`; undo that rather than miss the file."""
    if len(raw) > 1 and raw[0] == raw[-1] == '"':
        return codecs.escape_decode(raw[1:-1].encode("latin-1", "backslashreplace"))[0].decode(
            "utf-8", "surrogateescape"
        )
    return raw


def _header_path(line: str, prefix: str) -> str | None:
    rest = line[4:].rstrip("\n")
    if rest == "/dev/null":
        return None
    rest = _unquote(rest)
    return rest[len(prefix) :] if rest.startswith(prefix) else rest


def _stem_tokens(path: str | None) -> set[str]:
    return set(TOKEN.findall(PurePosixPath(path).stem)) if path else set()


def parse_diff(diff: str, doc_globs: list[str]) -> Removed:
    """What a `git diff -U0 -M` removes from code, and which doc lines it adds."""
    reader = _DiffReader(doc_globs)
    for line in diff.splitlines():
        reader.feed(line)
    return reader.result()


class _DiffReader:
    """One pass over a unified diff, tracking which file and which line it is in.

    Header lines (`--- `, `+++ `, `rename from `) are read ONLY between `diff --git` and
    the file's first `@@`. Past that point a line starting `--- ` or `+++ ` is CONTENT --
    a removed `-- comment`, an added `++ x` -- and reading it as a header dropped the
    removed line and flipped the doc state mid-file (rubber-duck on B17, reproduced).
    `diff --git` itself is unambiguous: hunk content always starts `+`, `-`, ` ` or `\\`.
    """

    def __init__(self, doc_globs: list[str]) -> None:
        self.doc_globs = doc_globs
        self.out = Removed()
        self.removed: set[str] = set()
        self.added: set[str] = set()
        self.old_assign: dict[str, str] = {}
        self.new_assign: dict[str, str] = {}
        self.old_path: str | None = None
        self.new_path: str | None = None
        self.doc = False
        self.header = False
        self.lineno = 0

    def feed(self, line: str) -> None:
        if line.startswith("diff --git "):
            self.old_path = self.new_path = None
            self.doc, self.header = False, True
        elif line.startswith("@@"):
            self.header = False
            m = re.match(r"@@ -\S+ \+(\d+)", line)
            self.lineno = int(m.group(1)) if m else 0
        elif self.header:
            self._header(line)
        elif line.startswith("+"):
            if self.doc and self.new_path:
                self.out.added.add((self.new_path, self.lineno))
            elif not self.doc:
                self.added |= set(TOKEN.findall(line[1:]))
                _assign(line[1:], self.new_assign)
            self.lineno += 1
        elif line.startswith("-") and not self.doc:
            self.removed |= set(TOKEN.findall(line[1:]))
            _assign(line[1:], self.old_assign)

    def _header(self, line: str) -> None:
        if line.startswith("--- "):
            self.old_path = _header_path(line, "a/")
        elif line.startswith("+++ "):
            self.new_path = _header_path(line, "b/")
            self.doc = is_doc(self.new_path or self.old_path or "", self.doc_globs)
            if not self.doc and self.old_path != self.new_path:
                # A deleted or renamed-away file takes its name with it.
                self.removed |= _stem_tokens(self.old_path)
                self.added |= _stem_tokens(self.new_path)
        elif line.startswith(("rename from ", "rename to ")):
            # A pure rename (100% similar) has no ---/+++ lines at all.
            which = _unquote(line.split(" ", 2)[2])
            if not is_doc(which, self.doc_globs):
                bucket = self.removed if line.startswith("rename from ") else self.added
                bucket |= _stem_tokens(which)

    def result(self) -> Removed:
        self.out.names = self.removed - self.added
        for name, old in self.old_assign.items():
            new = self.new_assign.get(name)
            # An empty old value is no evidence: no page quotes "", and as a pattern it
            # matches everywhere, so every line naming the knob would read as stale.
            if old and new is not None and new != old:
                self.out.defaults[name] = old
        return self.out


def _assign(line: str, into: dict[str, str]) -> None:
    """Record ``name -> value``, UNQUOTED, so `"x"` -> `'x'` is not a changed default."""
    m = ASSIGN.match(line)
    if m and TOKEN.fullmatch(m.group(1)) and SIMPLE_VALUE.match(m.group(2)):
        value = m.group(2)
        into.setdefault(m.group(1), value[1:-1] if value[0] in "\"'" else value)


def _pathspec(globs: list[str], *, exclude: bool = False) -> list[str]:
    magic = "exclude,glob" if exclude else "glob"
    return [f":({magic}){g}" for g in globs]


def _grep(repo, patterns: list[str], pathspec: list[str]) -> list[tuple[str, int, str]] | None:
    """(path, line, text) for every INDEX line holding one of ``patterns`` as a word.

    None when git could not say -- exit 1 is "no match", anything else is a failure, and
    a failure must never read as "nothing is stale".
    """
    rows: list[tuple[str, int, str]] = []
    for i in range(0, len(patterns), _BATCH):
        argv = ["grep", "--cached", "-z", "-n", "-I", "-w", "-F"]
        for pat in patterns[i : i + _BATCH]:
            argv += ["-e", pat]
        r = G.run(repo, *argv, "--", *pathspec, binary=True, timeout=120)
        if r.code == 1:
            continue
        if not r.ok:
            return None
        for raw in (r.out_bytes or b"").split(b"\n"):
            parts = raw.split(b"\0", 2)
            if len(parts) == _GREP_FIELDS:
                rows.append(
                    (os.fsdecode(parts[0]), int(parts[1]), parts[2].decode("utf-8", "replace"))
                )
    return rows


def _tracked_tokens(repo) -> set[str] | None:
    names = G.git_paths(repo, "ls-files", "--cached")
    if names is None:
        return None
    return set(TOKEN.findall("\n".join(names)))


def staged_diff(repo) -> str | None:
    r = G.run(
        repo,
        *("-c", "core.quotepath=false", "diff", "--cached", "-U0", "-M", "--no-color"),
        *("--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/"),
        binary=True,
        timeout=120,
    )
    if not r.ok:
        return None
    return (r.out_bytes or b"").decode("utf-8", "surrogateescape")


def stale_mentions(
    repo, doc_globs: list[str], exclude: list[str], *, diff: str | None = None
) -> list[Hit] | None:
    """Doc lines, outside the commit's own additions, naming what the commit removed.

    None when git could not answer -- the caller decides what "could not tell" costs, and
    it is never a pass.
    """
    diff = staged_diff(repo) if diff is None else diff
    if diff is None:
        return None
    gone = parse_diff(diff, doc_globs)
    if not gone.names and not gone.defaults:
        return []
    names = _not_live(repo, sorted(gone.names), doc_globs)
    if names is None:
        return None
    docs = _pathspec(doc_globs) + _pathspec(exclude, exclude=True)
    hits = []
    for found in (_name_hits(repo, names, docs, gone), _default_hits(repo, docs, gone)):
        if found is None:
            return None
        hits += found
    return sorted(set(hits), key=lambda h: (h.path, h.line, h.name))


def _not_live(repo, names: list[str], doc_globs: list[str]) -> list[str] | None:
    """``names`` minus those still used anywhere outside the docs -- moved, not removed."""
    if not names:
        return []
    code = _grep(repo, names, [".", *_pathspec(doc_globs, exclude=True)])
    paths = _tracked_tokens(repo)
    if code is None or paths is None:
        return None
    live = paths | {t for _, _, text in code for t in TOKEN.findall(text)}
    return [n for n in names if n not in live]


def _name_hits(repo, names: list[str], docs: list[str], gone: Removed) -> list[Hit] | None:
    if not names:
        return []
    rows = _grep(repo, names, docs)
    if rows is None:
        return None
    wanted = set(names)
    return [
        Hit(path, line, name, text.strip())
        for path, line, text in rows
        if (path, line) not in gone.added
        for name in sorted(wanted & set(TOKEN.findall(text)))
    ]


def _default_hits(repo, docs: list[str], gone: Removed) -> list[Hit] | None:
    """A doc line naming a changed default's knob AND its old value."""
    if not gone.defaults:
        return []
    rows = _grep(repo, sorted(gone.defaults), docs)
    if rows is None:
        return None
    return [
        Hit(path, line, f"{name} = {old}", text.strip())
        for path, line, text in rows
        if (path, line) not in gone.added
        for name, old in sorted(gone.defaults.items())
        if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text)
        # A hyphen joins words (`foo-bar` does not hold `foo`), and a period does only when
        # a word follows it: `30.5` does not hold `30`, the `30.` ending a sentence does.
        and re.search(rf"(?<![\w.-]){re.escape(old)}(?![\w-]|\.\w)", text)
    ]
