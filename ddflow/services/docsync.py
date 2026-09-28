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

from ..infra import proc as P

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
#: default. The value is taken up to a trailing comment or comma.
ASSIGN = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=]*)?=\s*([^#,]+?)\s*,?\s*(?:#.*)?$")

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
    exactly a path `git grep` searched as one: `*` stops at `/`, `**/` is any number of
    directories (including none), a trailing `/**` is everything below. `fnmatch` would
    let `*` cross `/`, and `PurePath.full_match` needs Python 3.13."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("/**", i) and i + 3 == len(glob):
            out.append("/.*")
            i += 3
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out))


def is_doc(path: str, doc_globs: list[str]) -> bool:
    return any(glob_regex(g).fullmatch(path) for g in doc_globs)


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
    out = Removed()
    removed: set[str] = set()
    added: set[str] = set()
    old_assign: dict[str, str] = {}
    new_assign: dict[str, str] = {}
    old_path: str | None = None
    new_path: str | None = None
    doc = False
    lineno = 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            old_path = new_path = None
            continue
        if line.startswith("--- "):
            old_path = _header_path(line, "a/")
            continue
        if line.startswith("+++ "):
            new_path = _header_path(line, "b/")
            doc = is_doc(new_path or old_path or "", doc_globs)
            if not doc and old_path != new_path:
                # A deleted or renamed-away file takes its name with it.
                removed |= _stem_tokens(old_path)
                added |= _stem_tokens(new_path)
            continue
        if line.startswith("rename from ") or line.startswith("rename to "):
            # A pure rename (100% similar) has no ---/+++ lines at all.
            which = _unquote(line.split(" ", 2)[2])
            if not is_doc(which, doc_globs):
                (removed if line.startswith("rename from ") else added).update(_stem_tokens(which))
            continue
        if line.startswith("@@"):
            m = re.match(r"@@ -\S+ \+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
            continue
        if line.startswith("+"):
            if doc and new_path:
                out.added.add((new_path, lineno))
            elif not doc:
                added |= set(TOKEN.findall(line[1:]))
                _assign(line[1:], new_assign)
            lineno += 1
        elif line.startswith("-") and not doc:
            removed |= set(TOKEN.findall(line[1:]))
            _assign(line[1:], old_assign)
    out.names = removed - added
    for name, old in old_assign.items():
        new = new_assign.get(name)
        if new is not None and new != old:
            out.defaults[name] = old.strip("\"'")
    return out


def _assign(line: str, into: dict[str, str]) -> None:
    m = ASSIGN.match(line)
    if m and TOKEN.fullmatch(m.group(1)) and SIMPLE_VALUE.match(m.group(2)):
        into.setdefault(m.group(1), m.group(2))


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
        argv = ["git", "-C", str(repo), "grep", "--cached", "-z", "-n", "-I", "-w", "-F"]
        for pat in patterns[i : i + _BATCH]:
            argv += ["-e", pat]
        r = P.run([*argv, "--", *pathspec], capture_output=True, timeout=120)
        if r.returncode == 1:
            continue
        if r.returncode != 0:
            return None
        for raw in r.stdout.split(b"\n"):
            parts = raw.split(b"\0", 2)
            if len(parts) == _GREP_FIELDS:
                rows.append(
                    (os.fsdecode(parts[0]), int(parts[1]), parts[2].decode("utf-8", "replace"))
                )
    return rows


def _tracked_tokens(repo) -> set[str] | None:
    r = P.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached"], capture_output=True, timeout=60
    )
    if r.returncode != 0:
        return None
    return set(TOKEN.findall(os.fsdecode(r.stdout).replace("\0", "\n")))


def staged_diff(repo) -> str | None:
    r = P.run(
        [
            "git", "-C", str(repo), "-c", "core.quotepath=false", "diff", "--cached",
            "-U0", "-M", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/",
        ],
        capture_output=True,
        timeout=120,
    )  # fmt: skip
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "surrogateescape")


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
        and re.search(rf"(?<![\w.]){re.escape(old)}(?![\w.])", text)
    ]
