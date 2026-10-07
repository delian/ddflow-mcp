"""B16 — the tests a change reaches, derived from the diff rather than guessed.

"Choosing which tests to run by reasoning about the change is guessing — derive it."
This is the fast-feedback half: while an agent works, it runs the tests its diff can
reach, in parallel, after each change. The other half is the WHOLE suite, because a
targeted sweep hides standing breakage (on the source project a full run found 12
pre-existing failures that every targeted run had missed). Where that whole run happens
is `unit_tests_scope`'s answer (D-gate-economy 1): once the item's ci gate has PASSED the
whole suite on the source its tree holds now, the unit_tests gate of a bug fix or a small
task runs the selection; in every other case -- no passing ci on this tree, a larger
task, a selection that cannot be made -- the unit_tests gate runs the whole suite itself.

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

What it misses, stated so nobody mistakes it for the suite -- the whole run catches these:

* a test that only drives the program in a SUBPROCESS (`python -m pkg ...`) imports
  nothing it exercises;
* a test more than `MAX_HOPS` imports away from the change (it imports C, C imports B,
  B imports the changed A);
* a test that reaches the change only through a re-exporting package ``__init__``;
* a test that builds a changed data file's path at run time, when another test spells
  the file's name together with its directory: only that most specific match is taken.

The two import bounds are chosen: unbounded, the layer that imports everything (a CLI,
an MCP registry) made one leaf module "reach" 42 of 87 test files on this repository.
Precision for fast feedback, recall at the gate.

And what it over-selects, by choice: when no test spells a changed data file's name with
one of its directories, every test that spells the bare name or the nearest directory is
taken -- the reader that gets the directory from a constant (`FIXTURES / "corpus.jsonl"`),
and also a test that only writes a `corpus.jsonl` of its own or reads another file in
`fixtures/`. Text cannot tell them apart; a missed reader hides breakage until the gate,
an extra test costs seconds. Taking every spelling always would cost more than it saves:
a nearest directory like `dedupe` is also a gate name in 22 tests here.
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
    directory (`fixtures/mcp-schema` and `LICENSE`); failing that, its name OR its
    NEAREST directory -- either part may be built at run time (`FIXTURES /
    "corpus.jsonl"`, `BASELINES / f"{kind}.toml"`), and taking only one would let a noisy
    other part (`fixtures` is in every test that reads any fixture) hide the reader. A
    farther directory alone is never enough."""
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

    out: dict[str, str] = {}
    for c in changed:
        if is_test_file(c) or PurePosixPath(c).name == "conftest.py":
            continue  # each has its own rule above
        dirs = _dirs_below_tests(c, homes)
        if dirs is None:
            continue
        name = PurePosixPath(c).name
        hit = [t for t in tests if names(t, name) and any(names(t, d) for d in dirs)]
        if not hit:
            hit = [t for t in tests if names(t, name) or (dirs and names(t, dirs[0]))]
        for t in hit:
            out.setdefault(t, f"names data file {c}")
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


# -- the unit_tests gate's scope (D-gate-economy 1) ------------------------------------


@dataclass
class Scope:
    """What the unit_tests gate runs for one item, and why: the whole suite, or only the
    tests the change reaches. ``command`` is the command to run ("" = the gate's own)."""

    scope: str  #: "selected" | "full"
    why: str
    command: str = ""
    tests: list[Selected] = field(default_factory=list)
    changed_lines: int | None = None

    def evidence(self) -> dict[str, object]:
        ev: dict[str, object] = {"scope": self.scope, "scope_why": self.why}
        if self.changed_lines is not None:
            ev["changed_lines"] = self.changed_lines
        if self.scope == "selected":
            ev["selected_tests"] = [{"path": t.path, "reason": t.reason} for t in self.tests]
        return ev


def changed_lines(tree: Path, base: str) -> int | None:
    """Added plus removed lines since ``base``'s merge-base: committed, uncommitted and
    untracked, ddflow's own `.ddflow/` bookkeeping excluded. None when git cannot say."""
    mb = W.git(tree, "merge-base", base, "HEAD")
    if mb.code != 0:
        return None
    num = W.git(tree, "diff", "--numstat", "--no-renames", mb.out, "--", ".", ":(exclude).ddflow")
    if num.code != 0:
        return None
    n = 0
    for row in num.out.splitlines():
        added, removed = [*row.split("\t", 2), "", ""][:2]
        n += int(added) if added.isdigit() else 0
        n += int(removed) if removed.isdigit() else 0
    for p in W.git_paths(tree, "ls-files", "--others", "--exclude-standard") or ():
        if p.startswith(".ddflow/"):
            continue
        try:
            n += len((tree / p).read_bytes().splitlines())
        except OSError:
            continue
    return n


def unit_tests_scope(cfg, st, it, command: str, tree: Path) -> Scope:
    """Whether the unit_tests gate runs the selection or the whole suite for item ``it``.

    The selection (D-gate-economy 1) is for a BUG FIX (the task fixes a bug) or a SMALL
    task (fewer changed lines than `[gates].unit_tests_small_lines`), and only once the
    whole suite has PASSED on this very tree: the item's `ci` gate is recorded `passed`
    with the source this tree holds now (ci runs the whole suite on the branch merged
    with the base). A ci that was skipped, could not run, failed, or ran on other source
    proves nothing about this tree, so the gate runs the whole suite (roborev on
    7da06042: a selection resting on a ci that never passed let a fix merge with the whole
    suite never run). Everything else -- a phase, a promotion, a larger task, a project
    with `[gates].unit_tests_scope = "full"` -- runs the whole suite, and so does a
    selection that cannot be made: no base, git cannot say what changed, no test reaches
    the change (running nothing would pass vacuously), or the command does not run
    pytest. Each answer says why. The base is the item's, else the configured base ref,
    else the default branch."""
    if cfg.gates.unit_tests_scope != "selected":
        return Scope("full", f'[gates].unit_tests_scope = "{cfg.gates.unit_tests_scope}"')
    if it.kind == "phase" or it.promote_to:
        what = "phase" if it.kind == "phase" else "promotion"
        return Scope("full", f"a {what} runs the whole suite")
    if why := _ci_not_passed_here(it, tree):
        return Scope("full", why)
    try:
        base = it.base or cfg.worktree.base_ref or W.default_branch(W.repo_root(tree))
    except W.GitError:
        return Scope("full", "no base to compare the change with")
    lines = changed_lines(tree, base)
    kind = _selectable(cfg, it, lines)
    if kind.startswith("not "):
        return Scope("full", kind, changed_lines=lines)
    return _selected(st, it, command, tree, base, kind, lines)


def _ci_not_passed_here(it, tree: Path) -> str:
    """ "" when the item's ci gate PASSED on the source ``tree`` holds now; else why not."""
    ci = it.gates.get("ci")
    if ci is None or ci.outcome != "passed":
        state = "has no outcome" if ci is None else f"is {ci.outcome}"
        return (
            f"the ci gate {state}: the whole suite has not passed on this tree, so this "
            "gate runs it (run ci first, and a bug fix or small task then runs the selection)"
        )
    ran_on = (ci.evidence or {}).get("source_tree", "")
    if not ran_on or ran_on != G.source_tree(tree):
        return "the ci gate passed on other source than this tree holds now: this gate runs the whole suite"
    return ""


def _selectable(cfg, it, lines: int | None) -> str:
    """Why ``it`` may run the selection ("a bug fix (...)", "a small task (...)"), or,
    starting "not ", why it may not."""
    small = cfg.gates.unit_tests_small_lines
    if it.fixes:
        return f"a bug fix ({', '.join(it.fixes)})"
    if lines is not None and small > 0 and lines < small:
        return f"a small task ({lines} changed lines < [gates].unit_tests_small_lines = {small})"
    size = "its size is unknown" if lines is None else f"{lines} changed lines"
    return f"not a bug fix, and {size} (small is under {small})"


def _selected(st, it, command: str, tree: Path, base: str, kind: str, lines: int | None) -> Scope:
    """The selection for a selectable item, or the whole suite when none can be made."""
    sel = select(tree, base)
    if sel is None:
        return Scope(
            "full", f"{kind}, but git could not say what changed since {base}", changed_lines=lines
        )
    picked = {s.path for s in sel.tests}
    # Only what pytest runs: a `scripts/check.sh` regression check is run by no command
    # built here, and listing it would claim a test that never ran.
    regression = sorted(
        {
            t
            for b in it.fixes
            if (bug := st.bugs.get(b)) is not None
            for t in bug.regression_tests
            if t.endswith(".py") or "::" in t
        }
        - picked
    )
    tests = [
        *sel.tests,
        *(Selected(t, "a regression test of the bug it fixes") for t in regression),
    ]
    if not tests:
        return Scope("full", f"{kind}, but no test reaches its change", changed_lines=lines)
    cmd = run_command(command, [t.path for t in tests], tree)
    if not cmd:
        return Scope(
            "full", f"{kind}, but the unit_tests command does not run pytest", changed_lines=lines
        )
    return Scope(
        "selected",
        f"{kind}: {len(tests)} test(s) its change reaches; the ci gate passed the whole suite on this tree",
        command=cmd,
        tests=tests,
        changed_lines=lines,
    )
