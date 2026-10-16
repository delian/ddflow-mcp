"""B16 — the tests a change reaches, derived from the diff rather than guessed.

"Choosing which tests to run by reasoning about the change is guessing — derive it."
This is the fast-feedback half: while an agent works, it runs the tests its diff can
reach, in parallel, after each change. The other half is the WHOLE suite, because a
targeted sweep hides standing breakage (on the source project a full run found 12
pre-existing failures that every targeted run had missed). Where that whole run happens
is `unit_tests_scope`'s answer (D-gate-economy 1): once the item's ci gate has PASSED the
whole suite on the clean commit its tree holds now, the unit_tests gate of a bug fix or a small
task runs the selection; in every other case -- no passing ci on this tree, a larger
task, a selection that cannot be made -- the unit_tests gate runs the whole suite itself.

A test is selected when, relative to the item's base:

* it is itself a changed test file;
* a changed ``conftest.py`` sits in its directory or above it (pytest applies it there
  without any import);
* it imports a changed module, directly or through ONE module that does — read from
  the Python import graph of the repository, not from names (`testreach`: the walk is cut
  at a hub, a module that `[gates].unit_tests_hub_importers` or more files import);
* its file name carries a changed file's stem (``test_gates.py`` for ``gates.py``) — the
  convention that covers languages this module does not parse;
* it DECLARES it governs the changed path: a test of repo-wide structure (architecture
  guards, a package layout, packaging) imports nothing it checks, so it says what it
  checks with a module-level ``GOVERNS = ("ddflow/api/**", ...)`` of globs, and a change
  to a path matching one selects it (B2a1eaa259e);
* it LISTS a package a module was added to or removed from (`testidents.listing_tests`): a
  layout test that walks the package from disk spells its name in quotes, and imports
  nothing the new module could change;
* it names a changed DATA file -- a fixture, a golden file, a baseline -- that lives below
  a directory holding tests: a test reads one by its path, never by an import
  (`_data_readers` says how it is matched).

What it misses, stated so nobody mistakes it for the suite -- the whole run catches these:

* a test that only drives the program in a SUBPROCESS (`python -m pkg ...`) imports
  nothing it exercises;
* a test more than `[gates].unit_tests_max_hops` imports away from the change (it imports C, C imports B,
  B imports the changed A);
* a test that reaches the change only through a re-exporting package ``__init__``;
* a package name built at run time, for the listing rule;
* a test that builds a changed data file's path at run time, when another test spells
  the file's name together with its directory: only that most specific match is taken.

A selection is also bounded: past `[gates].unit_tests_max_files` the farthest tests are
cut (changed tests, then declared ones, then direct importers, then two-hop ones) and the
cut set is reported with the selection. A changed hub or test infrastructure selects
nothing short of the whole suite (`Selection.full`).

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
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..core import globs
from ..infra import git as GIT
from . import changes as CH
from . import gates as G
from . import testidents as TI
from . import testreach as TR

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


#: How near a test is to the change, nearest first; the budget cuts from the far end.
RANK_CHANGED, RANK_DECLARED, RANK_DIRECT, RANK_TWO_HOP = 0, 1, 2, 3


@dataclass(frozen=True)
class Selected:
    path: str
    reason: str
    rank: int = RANK_DECLARED


@dataclass
class Selection:
    base: str
    changed: list[str] = field(default_factory=list)
    tests: list[Selected] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)  #: .py files the graph could not read
    cut: list[Selected] = field(default_factory=list)  #: over the budget, not selected
    cut_at: list[str] = field(default_factory=list)  #: hubs the import walk did not pass
    full: str = ""  #: why only the whole suite is safe (a hub or test infrastructure changed)


def changed_files(tree: Path, base: str) -> list[str] | None:
    """Every path that differs from ``base``: committed on the branch, staged, unstaged
    and untracked. None when git cannot answer — never an empty "nothing changed"."""
    # A rename is its OLD path too (`changed_paths` default): the old module's importers
    # are the ones a rename breaks.
    return CH.changed_paths(tree, base, include=("committed", "tracked", "untracked"))


def _listed(tree: Path) -> list[str] | None:
    """Every tracked and untracked (not ignored) path."""
    paths = GIT.files(tree, "all")
    return None if paths is None else sorted(set(paths))


class _Picks:
    """Test -> (why, rank); the first reason stays, the nearest rank wins."""

    def __init__(self) -> None:
        self.by: dict[str, tuple[str, int]] = {}

    def __call__(self, test: str, why: str, rank: int) -> None:
        if test not in self.by:
            self.by[test] = (why, rank)
        elif rank < self.by[test][1]:
            self.by[test] = (self.by[test][0], rank)

    def many(self, found: dict[str, str], rank: int) -> None:
        for t, why in found.items():
            self(t, why, rank)


def _by_name(changed: list[str], tests: list[str], pick: _Picks) -> None:
    stems = {PurePosixPath(c).stem for c in changed if not is_test_file(c)}
    stems -= {"__init__", "conftest"}
    for t in tests:
        name = PurePosixPath(t).stem
        hit = next((s for s in sorted(stems) if len(s) >= _MIN_STEM and _named(s, name)), "")
        if hit:
            pick(t, f"named after {hit}", RANK_DECLARED)


def _by_conftest(changed: list[str], tests: list[str], pick: _Picks, budget: int) -> str:
    """Pick every test under a changed conftest; the reason the whole suite is the only
    safe answer when that is more than the budget, else ""."""
    full = ""
    for c in changed:
        if PurePosixPath(c).name != "conftest.py":
            continue
        scope = str(PurePosixPath(c).parent)
        under = [t for t in tests if scope in (".", "") or t.startswith(scope + "/")]
        for t in under:
            pick(t, f"under changed {c}", RANK_CHANGED)
        if 0 < budget < len(under):
            full = f"{c} is test infrastructure for {len(under)} tests (budget {budget})"
    return full


def _bounded(sel: Selection, picks: _Picks, max_files: int) -> None:
    """Fill ``sel.tests`` from the picks, nearest first; the farthest past ``max_files``
    go to ``sel.cut`` (a changed test is never one of them)."""
    ordered = sorted(picks.by, key=lambda t: (picks.by[t][1], t))
    keep = ordered
    if max_files > 0 and len(ordered) > max_files:
        floor = sum(1 for t in ordered if picks.by[t][1] == RANK_CHANGED)
        keep = ordered[: max(max_files, floor)]
        sel.cut = [Selected(t, *picks.by[t]) for t in ordered[len(keep) :]]
    sel.tests = [Selected(t, *picks.by[t]) for t in sorted(keep)]


def select(
    tree: Path,
    base: str,
    *,
    max_files: int = 0,
    hub_importers: int = 0,
    max_hops: int = 2,
    name_max_tests: int = 30,
    changed: list[str] | None = None,
    listed: list[str] | None = None,
    idents: dict[str, str] | None = None,
    membership: list[str] | None = None,
) -> Selection | None:
    """The tests ``tree``'s change reaches since ``base``, each with why and how near. None
    when git could not say what changed.

    ``hub_importers`` (`[gates].unit_tests_hub_importers`) is the importer count from which
    a module is a hub (0 = none): the import walk is cut at one, and a changed hub sets
    ``full``; ``max_hops`` and ``name_max_tests`` are `unit_tests_max_hops` and
    `unit_tests_name_max_tests`. ``max_files`` (`[gates].unit_tests_max_files`, 0 = unlimited) bounds the
    selection: the farthest tests are moved to ``cut`` until it fits, never a changed test.
    ``changed``, ``listed`` and ``idents`` stand in for git's answers (and
    `testidents.changed_identifiers`; ``membership`` the added and removed paths), to select over a checkout of an older commit that
    has no history of its own (the recall replay)."""
    changed = changed_files(tree, base) if changed is None else changed
    listed = _listed(tree) if listed is None else listed
    if changed is None or listed is None:
        return None
    sel = Selection(base=base, changed=changed)
    py = [p for p in listed if p.endswith(".py") and (tree / p).is_file()]
    tests = [p for p in listed if is_test_file(p) and (tree / p).is_file()]
    pick = _Picks()
    for c in changed:
        if is_test_file(c) and (tree / c).exists():
            pick(c, "changed", RANK_CHANGED)
    sel.full = _by_conftest(changed, tests, pick, max_files)

    graph = TR.build(tree, py, changed)
    sel.unparsed.extend(graph.unparsed)
    hubs = graph.hubs(hub_importers)
    walked = TR.reach(graph, changed, hubs, max_hops)
    sel.cut_at = sorted(walked.cut_at)
    if touched := TR.hubs_touched(changed, hubs):
        sel.full = sel.full or f"a hub module changed: {', '.join(touched)}"
    for t, (why, hops) in walked.files.items():
        if t in tests:
            pick(t, why, RANK_DIRECT if hops == 1 else RANK_TWO_HOP)

    _by_name(changed, tests, pick)
    pick.many(_data_readers(tree, changed, tests), RANK_DECLARED)
    pick.many(governed(tree, changed, tests), RANK_DECLARED)
    if idents is None:
        idents = TI.changed_identifiers(tree, base, [c for c in changed if c in py])
    if membership is None:
        membership = _membership(tree, base)
        if membership is None:  # nobody looked is never "nothing was added"
            sel.full = (
                sel.full or f"git could not say which files were added or removed since {base}"
            )
    packages = TI.listed_packages(membership or [])
    if idents or packages:  # no changed name or listing, nothing to read the tests for
        texts = {t: _read(tree / t) for t in tests}
        pick.many(TI.spelling_tests(idents, texts, name_max_tests), RANK_DECLARED)
        pick.many(TI.listing_tests(packages, texts, name_max_tests), RANK_DECLARED)
    _bounded(sel, pick, max_files)
    return sel


def _membership(tree: Path, base: str) -> list[str] | None:
    """The paths ADDED or REMOVED since ``base`` (committed, staged, unstaged, untracked):
    the changes that alter what a package lists. None when git cannot say."""
    return CH.changed_paths(
        tree, base, include=("committed", "tracked", "untracked"), diff_filter="AD"
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _governs(source: str) -> list[str]:
    """The globs a test module's top-level ``GOVERNS = (...)`` lists; none if it has no such
    literal (or is not valid Python: the import graph reports that on its own)."""
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "GOVERNS" for t in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return []
            if isinstance(value, (tuple, list)):
                return [g for g in value if isinstance(g, str)]
    return []


def governed(tree: Path, changed: list[str], tests: list[str]) -> dict[str, str]:
    """Each test that declares (`GOVERNS`) it checks a path in ``changed``, with why."""
    out: dict[str, str] = {}
    for t in tests:
        if not t.endswith(".py"):
            continue
        try:
            source = (tree / t).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "GOVERNS" not in source:
            continue
        mine = _governs(source)
        hit = next((c for c in changed if any(globs.match(c, g) for g in mine)), "")
        if hit:
            out[t] = f"governs {hit}"
    return out


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
