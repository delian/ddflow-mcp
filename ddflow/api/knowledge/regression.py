"""Resolving a regression test's node id to a test that exists, in any worktree."""

from __future__ import annotations

import ast
import re
from pathlib import Path


def _unresolved_tests(repo: Path, spec: str) -> tuple[list[str], list[str]]:
    """Split a `--regression-test` list into (missing, unchecked) pytest node ids.

    A node id (`path.py::name[...]`, `path.py::Class::name`) is looked up in EVERY
    worktree, because a bug is closed from the fix branch before its test reaches the
    base. Only names are checked, statically: whether the test FAILS without the fix is
    B-bugfix-verified's job. Anything that is not a Python node id -- a spec, a shell
    command -- cannot be resolved here and is returned as unchecked, not refused.
    """
    from ...infra import worktree as W

    trees = [Path(t["worktree"]) for t in W.list_worktrees(repo) if t.get("worktree")] or [repo]
    missing: list[str] = []
    unchecked: list[str] = []
    for entry in _split_outside_brackets(spec):
        path, sep, names = entry.partition("::")
        # A command (`pytest tests/test_x.py`) can end in `.py` too; a path has no
        # whitespace (B65bbe327c7).
        if not path.endswith(".py") or any(c.isspace() for c in path):
            unchecked.append(entry)
            continue
        # The parametrize id is cut off BEFORE splitting: `::` and `,` are legal inside
        # `[...]`, and splitting them refused a real test (B-bfu-param-sep).
        wanted = names.split("[", 1)[0].split("::") if sep else []
        # `path::` or `path::[p]` names no test; an empty part must not pass for one.
        # Several tests joined by whitespace are never one test: `a.py::t[1] a.py::t2`
        # resolved `t` and accepted the unchecked rest (B227585c781).
        if (
            "" in wanted
            or _looks_like_several(entry)
            or not any(_defines(_inside(tree, path), wanted) for tree in trees)
        ):
            missing.append(entry)
    return missing, unchecked


def _looks_like_several(entry: str) -> bool:
    """A node id followed, after whitespace OUTSIDE its `[...]`, by another test path:
    tests joined by spaces, not one test (`a.py::t1 a.py::t2`, `a.py::t[1] a.py::t2`).

    Asked of a node id whose path has no whitespace (a command never gets here). Inside
    the brackets anything goes -- a parameter id may hold spaces, `::` and `.py`
    (`t[python foo.py -v]`, `t[a b::c]`) -- and a value may hold `]` itself (`t[x] y]`),
    so only a following token that starts like a test PATH counts. A bare trailing word
    is not refused: the permissive side, since refusing a real test locks the bug open.
    """
    depth, tokens, cur = 0, [], []
    for ch in entry:
        if ch.isspace() and depth == 0:
            tokens.append("".join(cur))
            cur = []
            continue
        depth = max(0, depth + {"[": 1, "]": -1}.get(ch, 0))
        cur.append(ch)
    tokens.append("".join(cur))
    return any(tok.split("::", 1)[0].endswith(".py") for tok in tokens[1:] if tok)


#: A node id's path: no whitespace, ends in `.py`, then `::` or nothing.
_NODE_PATH = re.compile(r"[^\s:]+\.py(::|$)")


def _malformed(entry: str) -> bool:
    """A piece no list was written with: a node id (or a bare token) with a `]` before any
    `[`, or with brackets followed by anything but `::name` (`TestC[x]::test_m` is a
    class-parametrized id) or whitespace. A command -- whitespace, not a node id -- is never malformed:
    `pytest -k "a]b"` must not be glued onto the id before it."""
    e = entry.strip()
    if not e or (not _NODE_PATH.match(e) and any(c.isspace() for c in e)):
        return False
    if "]" in e and ("[" not in e or e.index("]") < e.index("[")):
        return True
    if "[" not in e:
        return False
    if "]" not in e:
        return True
    # After the brackets: nothing, `::name`, or whitespace and more words -- tests joined
    # by spaces, which `_looks_like_several` refuses with its own hint.
    tail = e[e.rindex("]") + 1 :]
    return not (tail == "" or tail[0].isspace() or (tail.startswith("::") and "[" not in tail))


def _split_outside_brackets(spec: str) -> list[str]:
    """Entries separated by ',' or ';', ignoring both inside a parametrize id's brackets.

    ';' as well as ',' (B227585c781): a ';'-joined list was resolved as one node id and
    refused as a single missing test. Brackets cannot be counted: a parametrize value may
    hold `]`, `[`, `,` and `;` (`t[x]y]`, `t[a]b]c,d]`, `t[a],b]`, `t[a],b.py,c]`). Counting drove the depth negative and swallowed every later
    separator, so a missing second test was never resolved (Bfc9daca269), and every local
    rule tried after it had a counterexample. So the whole list is parsed at once: of all
    the ways to cut it at its separators, the one with the fewest malformed entries
    (`_malformed`), then the most cuts -- every separator is one unless cutting there
    breaks an entry. Where two readings are both well-formed
    (`t[a],tests/b.py::u[x]`), the one with more entries wins: a list of tests is likelier
    than a value shaped like one.
    """
    cuts = [-1, *(i for i, ch in enumerate(spec) if ch in ",;"), len(spec)]
    # best[j] for spec[: cuts[j]]: (malformed entries, -cuts made, the cut before)
    best: list[tuple[int, int, int]] = [(0, 0, -1)]
    for j in range(1, len(cuts)):
        best.append(
            min(
                (best[i][0] + _malformed(spec[cuts[i] + 1 : cuts[j]]), best[i][1] - 1, i)
                for i in range(j)
            )
        )
    out, j = [], len(cuts) - 1
    while j > 0:
        i = best[j][2]
        out.append(spec[cuts[i] + 1 : cuts[j]])
        j = i
    return [e.strip() for e in reversed(out) if e.strip()]


def _inside(tree: Path, path: str) -> Path | None:
    """`tree / path`, or None when it resolves outside `tree`.

    An absolute path made `tree / path` discard the tree, and `..` walks out of it, so
    any file anywhere could close a bug (B-bfu-abs-path).
    """
    candidate = (tree / path).resolve()
    return candidate if candidate.is_relative_to(tree.resolve()) else None


def _defines(source: Path | None, names: list[str]) -> bool:
    """Does `source` exist and define the node `names` -- module, then class, then method?

    Resolved as a CHAIN: matching each name anywhere in the file accepted a method for a
    module-level test and a function outside the class for `Class::method`
    (B-bfu-structure). A file that does not parse under THIS interpreter may still be
    valid under the project's own, so it falls back to finding each name defined
    somewhere -- the permissive side, since refusing a real test locks the bug open.
    """
    if source is None:
        return False
    try:
        raw = source.read_bytes()
    except OSError:
        return False
    try:
        # Bytes, so a PEP 263 coding cookie is honoured as pytest would (B1d4b2e6914).
        scope: list[ast.stmt] = ast.parse(raw).body
    except (SyntaxError, ValueError):
        text = raw.decode("utf-8", errors="replace")
        return all(
            re.search(rf"^\s*(?:async\s+def|def|class)\s+{re.escape(n)}\b", text, re.M)
            for n in names
        )
    module = _definitions(scope)
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for depth, name in enumerate(names):
        found = (
            next((d for d in module if d.name == name), None)
            if node is None
            else _member(node, name, module, set())
        )
        if found is None:
            return False
        if found is True:
            return True
        node = found
        if depth < len(names) - 1 and not isinstance(node, ast.ClassDef):
            return False  # pytest collects from classes only, never a function body
    return True


def _member(
    cls: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    module: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str],
) -> ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | bool | None:
    """`name` defined in `cls` or, as pytest collects it, inherited from a base.

    Bases are followed when they are classes of the same module (Bd671110650). A base
    defined elsewhere cannot be read here, so its members are unknown: True, the
    permissive side, since refusing a real test locks the bug open.
    """
    own = next((d for d in _definitions(cls.body) if d.name == name), None)
    if own is not None or not isinstance(cls, ast.ClassDef):
        return own
    seen.add(cls.name)
    unknown = False
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id == "object":
            continue  # defines no tests; treating it as unknown would accept any name
        local = next(
            (
                d
                for d in module
                if isinstance(base, ast.Name) and isinstance(d, ast.ClassDef) and d.name == base.id
            ),
            None,
        )
        if local is None:
            unknown = True
        elif local.name not in seen:
            hit = _member(local, name, module, seen)
            if hit is not None:
                return hit
    return True if unknown else None


def _definitions(
    body: list[ast.stmt],
) -> list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    """Classes and functions defined directly in `body`, including under `if`/`try`/`with`
    at the same level, but not inside another definition."""
    out: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            out.append(node)
        elif isinstance(
            node, ast.If | ast.Try | ast.With | ast.AsyncWith | ast.For | ast.AsyncFor | ast.While
        ):
            for block in (node.body, getattr(node, "orelse", []), getattr(node, "finalbody", [])):
                out += _definitions(block)
            for handler in getattr(node, "handlers", []):
                out += _definitions(handler.body)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                out += _definitions(case.body)
    return out
