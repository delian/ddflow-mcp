"""One glob semantics for every path pattern ddflow reads (D-unify).

A pattern is read as git reads it: `*` and `?` stop at `/`; `**/` is any leading directories
(none included), a trailing `/**` is everything below; `[...]` is a class (`[!x]` and `[^x]`
negate); a leading `/` anchors. Two named readings differ in ONE thing, whether a pattern
with no `/` is anchored at the root:

* pathspec (the default): `README.md` is the root file -- git's `:(glob)` pathspec, the
  `[enforce]` doc and code globs.
* ``bare_any_depth=True``: `README.md` is that name at any depth -- a `.gitattributes` or
  `.gitignore` line, which is what `[lease] shared_globs`, lesson globs and `[importer]
  archive_globs` are written as.

`inside` (a claim covers a path) and `overlap` (two claims could share a file) are the two
questions the lease conflict detector asks; they are built on the same translator.
"""

from __future__ import annotations

import functools
import re
from fnmatch import fnmatch


def _class_end(pat: str, i: int) -> int:
    """Index of the `]` closing the class opened at ``pat[i]``, or -1 (then `[` is literal).

    A `]` right after `[`, `[!` or `[^` is a MEMBER, as in git and Python: `[]]`, `[^]]`.
    Taken as the closer, it left `[^]` -- an invalid regex that crashed a claim.
    """
    k = i + 1
    if k < len(pat) and pat[k] in "!^":
        k += 1
    if k < len(pat) and pat[k] == "]":
        k += 1
    return pat.find("]", k)


@functools.lru_cache(maxsize=512)
def regex(pattern: str, bare_any_depth: bool = False) -> re.Pattern[str]:
    """``pattern`` as a compiled regex matched against a whole path (``.match``).

    Raises `re.error` for a pattern git would read and Python cannot (a descending class
    range); `match` turns that into "matches only itself".
    """
    anchored = "/" in pattern.rstrip("/") or not bare_any_depth
    pat = pattern.lstrip("/")
    out: list[str] = []
    i = 0
    while i < len(pat):
        # `**` is "any depth" only on a path boundary -- a leading `**/`, a `/**/`, a
        # trailing `/**`; elsewhere it is two plain `*`s, which stop at `/` (gitignore(5)).
        at_start = i == 0 or pat[i - 1] == "/"
        if at_start and pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif at_start and pat.startswith("**", i) and i + 2 == len(pat):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "[" and _class_end(pat, i) != -1:
            j = _class_end(pat, i)
            body = pat[i + 1 : j].replace("\\", "\\\\")
            # git negates with `[!...]` as well as `[^...]`; Python knows only `^`.
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = j + 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out) + r"\Z")


def match(path: str, glob: str, *, bare_any_depth: bool = False) -> bool:
    """Is ``path`` matched by ``glob``? A pattern that cannot be compiled matches only
    itself, never crashes a claim or a commit."""
    if path == glob:
        return True
    try:
        return bool(regex(glob, bare_any_depth).match(path))
    except re.error:
        return False


def inside(path: str, glob: str) -> bool:
    """Is the concrete ``path`` INSIDE the claim glob ``glob``?

    Not `overlap`, whose literal-prefix rule is right for "could two patterns share a file"
    and wrong here: a staged `a.md` counted as covered by a claim on `a.md.bak`
    (B1997c64c5a). Inside means: the glob itself, a file under a directory glob (`src/a` or
    `src/a/`), an fnmatch match (whose `*` crosses `/`, as the claims written so far
    assume), or -- for a glob with a `/` -- git's match, where a `**/` on a path boundary is
    zero or more directories (`src/**/*.py` covers `src/b.py`).
    """
    if path == glob or path.startswith(glob.rstrip("/") + "/") or fnmatch(path, glob):
        return True
    if "/" not in glob.rstrip("/"):
        return False  # git would match a bare name at any depth; a claim on `a.md` is one file
    return match(path, glob, bare_any_depth=True)


def overlap(a: str, b: str) -> bool:
    """Do two path patterns plausibly cover a common file?

    Exact match, either pattern matching the other as a literal, or a shared literal
    prefix. Deliberately approximate: computing true regular-language intersection of
    two globs is both hard and beside the point, since the answer only decides whether
    to re-order.
    """
    a, b = a.lstrip("/"), b.lstrip("/")  # a leading `/` anchors; it names no different file
    if a == b:
        return True
    if fnmatch(a, b) or fnmatch(b, a):
        return True
    pa = a.split("*", maxsplit=1)[0].split("?", maxsplit=1)[0]
    pb = b.split("*", maxsplit=1)[0].split("?", maxsplit=1)[0]
    if not pa or not pb:
        return True  # a bare "*" covers everything
    return pa.startswith(pb) or pb.startswith(pa)
