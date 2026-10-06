"""B16 — the tests a change reaches, derived from the diff rather than guessed.

"Choosing which tests to run by reasoning about the change is guessing — derive it."
This is the fast-feedback half: while an agent works, it runs the tests its diff can
reach, in parallel, after each change. The other half stays where it was: the
`unit_tests` gate runs the WHOLE suite, because a targeted sweep hides standing breakage
(on the source project a full run found 12 pre-existing failures that every targeted
run had missed). A selection is advice, never a pass.

A test is selected when, relative to the item's base:

* it is itself a changed test file;
* a changed ``conftest.py`` sits in its directory or above it (pytest applies it there
  without any import);
* it imports a changed module, directly or through modules that import it — read from
  the Python import graph of the repository, not from names;
* its file name carries a changed file's stem (``test_gates.py`` for ``gates.py``) — the
  convention that covers languages this module does not parse;
* it names a changed DATA file -- a fixture, a golden file, a baseline -- that lives below
  a directory holding tests: a test reads one by its path, never by an import
  (`_data_readers` says how it is matched).

What it misses, stated so nobody mistakes it for the suite -- the gate runs all of these:

* a test that only drives the program in a SUBPROCESS (`python -m pkg ...`) imports
  nothing it exercises;
* a test more than `MAX_HOPS` imports away from the change (it imports C, C imports B,
  B imports the changed A);
* a test that reaches the change only through a re-exporting package ``__init__``;
* a test that reads a data file under a name it builds at run time, when another test
  spells out that file's name and directory without reading it (the most specific
  match wins).

The last two are the bound, chosen: unbounded, the layer that imports everything (a CLI,
an MCP registry) made one leaf module "reach" 42 of 87 test files on this repository.
Precision for fast feedback, recall at the gate.
"""

from __future__ import annotations

import ast
import re
import shlex
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..infra import worktree as W
from . import gates as G

#: A test file by name, in the conventions of pytest, Jest/Vitest and Go.
_TEST_FILE = re.compile(
    r"(?:^|/)(?:test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(?:test|spec)\.[cm]?[jt]sx?|[^/]*_test\.go)$"
)


#: A stem this short (`db`, `io`) is inside too many test names to mean anything.
_MIN_STEM = 3


def is_test_file(path: str) -> bool:
    return bool(_TEST_FILE.search(path))


def _named(stem: str, test_name: str) -> bool:
    """``stem`` as a whole word of ``test_name`` (a plural allowed): `cli` names
    `test_cli_parity` and not `test_client`."""
    return bool(re.search(rf"(?:^|[_.-]){re.escape(stem)}s?(?:$|[_.-])", test_name))


@dataclass(frozen=True)
class Selected:
    path: str
    reason: str


@dataclass
class Selection:
    base: str
    changed: list[str] = field(default_factory=list)
    tests: list[Selected] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)  #: .py files the graph could not read


def changed_files(tree: Path, base: str) -> list[str] | None:
    """Every path that differs from ``base``: committed on the branch, staged, unstaged
    and untracked. None when git cannot answer — never an empty "nothing changed"."""
    mb = W.git(tree, "merge-base", base, "HEAD")
    if mb.code != 0:
        return None
    parts = [
        # `--no-renames`: a rename is its OLD path too. The old module's importers are the
        # ones a rename breaks, and `--name-only` alone reports only the new path.
        W.git_paths(tree, "diff", "--name-only", "--no-renames", f"{mb.out}..HEAD"),
        W.git_paths(tree, "diff", "--name-only", "--no-renames", "HEAD"),
        W.git_paths(tree, "ls-files", "--others", "--exclude-standard"),
    ]
    if any(p is None for p in parts):
        return None
    return sorted({n for p in parts for n in p or ()})


def module_name(path: str) -> str:
    """``pkg/sub/mod.py`` -> ``pkg.sub.mod``; a package's ``__init__.py`` is the package;
    a ``src/`` layout's prefix is not part of the name."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: str, source: str) -> set[str]:
    """Absolute dotted names ``path`` imports, relative imports resolved."""
    tree = ast.parse(source, filename=path)
    here = module_name(path).split(".")
    package = here if path.endswith("__init__.py") else here[:-1]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                target = ".".join([*anchor, *([node.module] if node.module else [])])
            else:
                target = node.module or ""
            if target:
                out.add(target)
            # `from pkg import mod` imports the MODULE when `mod` is one.
            out.update(f"{target}.{a.name}" if target else a.name for a in node.names)
    return out


def _resolve(name: str, known: set[str]) -> str:
    """The longest known module that ``name`` is, or lies inside (``pkg.mod.func`` ->
    ``pkg.mod``); "" when it is not this repository's code."""
    parts = name.split(".")
    for n in range(len(parts), 0, -1):
        cand = ".".join(parts[:n])
        if cand in known:
            return cand
    return ""


def _listed(tree: Path) -> list[str] | None:
    """Every tracked and untracked (not ignored) path."""
    paths = W.git_paths(tree, "ls-files", "--cached", "--others", "--exclude-standard")
    return None if paths is None else sorted(set(paths))


def select(tree: Path, base: str) -> Selection | None:
    """The tests ``tree``'s change reaches since ``base``, each with why. None when git
    could not say what changed."""
    changed = changed_files(tree, base)
    listed = _listed(tree)
    if changed is None or listed is None:
        return None
    sel = Selection(base=base, changed=changed)
    py = [p for p in listed if p.endswith(".py") and (tree / p).is_file()]
    tests = [p for p in listed if is_test_file(p) and (tree / p).is_file()]
    reasons: dict[str, str] = {}

    def pick(test: str, why: str) -> None:
        reasons.setdefault(test, why)

    for c in changed:
        if is_test_file(c) and (tree / c).exists():
            pick(c, "changed")
    for c in changed:
        if PurePosixPath(c).name == "conftest.py":
            scope = str(PurePosixPath(c).parent)
            for t in tests:
                if scope in (".", "") or t.startswith(scope + "/"):
                    pick(t, f"under changed {c}")

    for t, why in _import_reach(tree, py, changed, sel).items():
        if t in tests:
            pick(t, why)

    stems = {PurePosixPath(c).stem for c in changed if not is_test_file(c)}
    stems.discard("__init__")
    stems.discard("conftest")
    for t in tests:
        name = PurePosixPath(t).stem
        hit = next((s for s in sorted(stems) if len(s) >= _MIN_STEM and _named(s, name)), "")
        if hit:
            pick(t, f"named after {hit}")

    for t, why in _data_readers(tree, changed, tests).items():
        pick(t, why)

    sel.tests = [Selected(t, reasons[t]) for t in sorted(reasons)]
    return sel


def _names(text: str, word: str) -> bool:
    """``word`` (a file or directory name) spelt out in ``text``, not inside a longer name:
    `schema.json` in `"fixtures/mcp-schema/schema.json"`, not in `old_schema.json` or
    `schema.json.orig` (a full stop that ends a sentence is not part of a name)."""
    if word not in text:  # the common answer, without a regex
        return False
    return re.search(rf"(?<![\w.-]){re.escape(word)}(?![\w-]|\.\w)", text) is not None


def _data_readers(tree: Path, changed: list[str], tests: list[str]) -> dict[str, str]:
    """The tests that read a changed data file below a directory holding tests, each with
    why. A test spells a data file's path out, never imports it, so it is matched on what
    it names: the file's name together with one of its directories below that test
    directory (`fixtures/mcp-schema` and `LICENSE`); failing that, the file's NEAREST
    directory alone (`guard_baselines`, whose file names the test builds at run time).
    The name alone counts only for a file directly in a test directory: a bare `LICENSE`
    or `.gitattributes` is also in tests that write one of their own, and a farther
    directory (`fixtures`) is in every test that reads any fixture at all."""
    homes = {str(d) for t in tests for d in PurePosixPath(t).parents if str(d) != "."}
    sources: dict[str, str] = {}
    seen: dict[tuple[str, str], bool] = {}  # one directory is asked about for every file in it

    def names(test: str, word: str) -> bool:
        if (test, word) not in seen:
            if test not in sources:
                try:
                    sources[test] = (tree / test).read_text("utf-8", errors="replace")
                except OSError:  # unreadable: it names nothing, rather than failing the run
                    sources[test] = ""
            seen[test, word] = _names(sources[test], word)
        return seen[test, word]

    def spelt(test: str, every: list[str], some: list[str]) -> bool:
        return all(names(test, w) for w in every) and (
            not some or any(names(test, w) for w in some)
        )

    out: dict[str, str] = {}
    for c in changed:
        if is_test_file(c) or PurePosixPath(c).name == "conftest.py":
            continue  # each has its own rule above
        dirs = _dirs_below_tests(c, homes)
        if dirs is None:
            continue
        name = PurePosixPath(c).name
        # (every word, at least one of these words), most specific first; with no
        # directory between the file and its tests, the first is the name alone
        rungs = [([name], dirs), *([([dirs[0]], [])] if dirs else [])]
        for every, some in rungs:
            hit = [t for t in tests if spelt(t, every, some)]
            if hit:
                for t in hit:
                    out.setdefault(t, f"names data file {c}")
                break
    return out


def _dirs_below_tests(path: str, homes: set[str]) -> list[str] | None:
    """The directories between ``path`` and the nearest directory holding tests, nearest
    first; None when no directory above it (the repository root aside) holds tests."""
    dirs: list[str] = []
    for parent in PurePosixPath(path).parents:
        if str(parent) == ".":
            return None
        if str(parent) in homes:
            return dirs
        dirs.append(parent.name)
    return None


#: How far along the import graph a test still counts as reaching a change: it imports
#: the changed module (1), or imports a module that does (2). Unbounded, the top layer --
#: a CLI or an MCP registry that imports everything, and tests import -- made every test
#: "reach" every module: 42 of 87 test files for one leaf module on this repository,
#: 72 for the event log. Past two hops the edge is an accident of layering, not evidence.
MAX_HOPS = 2


def _import_reach(tree: Path, py: list[str], changed: list[str], sel: Selection) -> dict[str, str]:
    """File -> why, for every .py file within `MAX_HOPS` imports of a changed module,
    breadth first so the reason names the nearest changed module and the path to it."""
    known = {module_name(p): p for p in py}
    # A deleted (or renamed-away) module is still a name its importers use -- and they are
    # now broken, the tests most worth running -- so it resolves like a live one.
    gone = {module_name(c) for c in changed if c.endswith(".py") and c not in py}
    names_known = set(known) | gone
    importers: dict[str, set[str]] = {}
    for p in py:
        try:
            names = _imports(p, (tree / p).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            sel.unparsed.append(p)
            continue
        for n in names:
            mod = _resolve(n, names_known)
            if mod and known.get(mod) != p:
                importers.setdefault(mod, set()).add(p)

    roots = [module_name(c) for c in changed if c.endswith(".py")]
    # (module, the changed module it leads to, the reason so far, hops taken)
    queue = deque((m, m, f"imports {m}", 0) for m in roots if m in names_known)
    seen_mod: set[str] = {m for m, *_ in queue}
    reach: dict[str, str] = {}
    while queue:
        mod, origin, why, hops = queue.popleft()
        for f in sorted(importers.get(mod, ())):
            fm = module_name(f)
            reach.setdefault(f, why)
            # A package `__init__` re-exporting everything (an API facade) is the same
            # accident at a smaller scale: importing the facade is not evidence a test
            # exercises the module behind it, so the graph does not continue through one.
            if hops + 1 >= MAX_HOPS or f.endswith("__init__.py") or fm in seen_mod:
                continue
            seen_mod.add(fm)
            queue.append((fm, origin, f"imports {fm}, which imports {origin}", hops + 1))
    return reach


def run_command(configured: str, tests: list[str], root: Path) -> str:
    """A command that runs ``tests`` the way the project runs its suite, in parallel.

    Built from the configured unit_tests command when it runs pytest: its runner prefix
    (`uv run pytest`) and its options (`--timeout=420`, `-n 48`) are kept, the path
    arguments are replaced by the selected files. `-n auto` is added when the command
    chose no worker count and the project declares pytest-xdist. "" when the project
    does not run pytest: the files are the answer then.

    An entry may be a test FILE or a pytest NODE ID (`tests/x.py::test_y`), so callers
    that name one test (`bug fixed --regression-test`, B-bugfix-verified) can run it
    through the project's own runner prefix rather than a second command.
    """
    py_tests = [t for t in tests if t.endswith(".py") or "::" in t]
    if not py_tests:
        return ""
    try:
        tokens = shlex.split(configured) if configured.strip() else []
    except ValueError:
        tokens = []
    at = next(
        (i for i, t in enumerate(tokens) if PurePosixPath(t).name in ("pytest", "py.test")),
        -1,
    )
    if at >= 0:
        cmd = [*tokens[: at + 1], *_without_test_locations(tokens[at + 1 :], root)]
    elif G.uses_pytest(root):
        cmd = ["pytest", "-q"]
    else:
        return ""
    if G.runs_pytest_serially(shlex.join(cmd)) and G.declares_xdist(root):
        cmd += ["-n", "auto"]
    return shlex.join([*cmd, *py_tests])


#: pytest options whose value is the NEXT token. A value is never a test location, even
#: when it names a directory (`--rootdir tests`).
_VALUE_FLAGS = frozenset(
    {"-c", "-p", "-k", "-m", "-o", "-W", "-n", "--rootdir", "--confcutdir", "--basetemp",
     "--ignore", "--ignore-glob", "--deselect", "--timeout", "--maxfail", "--numprocesses",
     "--dist", "--durations", "--junitxml", "--cov", "--cov-report", "--log-level"}
)  # fmt: skip


def _without_test_locations(args: list[str], root: Path) -> list[str]:
    """``args`` minus the TEST LOCATIONS it names (`tests/`, `tests/test_x.py`,
    `tests/test_x.py::test_y`), so the selected files replace them. Everything else is
    kept -- an option's value above all, even one naming a file (`-c pytest.ini`)."""
    out: list[str] = []
    for i, t in enumerate(args):
        value_of_flag = i > 0 and args[i - 1] in _VALUE_FLAGS
        location = "::" in t or (root / t).is_dir() or (is_test_file(t) and (root / t).exists())
        if t.startswith("-") or value_of_flag or not location:
            out.append(t)
    return out
