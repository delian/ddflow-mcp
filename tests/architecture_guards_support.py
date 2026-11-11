"""Shared measurements and baseline readers for the tests/test_architecture_*.py guards.

Split from tests/test_architecture_guards.py (rule r-small-files): the counters (`_Sites`
and the census functions), the committed-baseline readers and the ratchet comparison live
here; the guards that use them are in test_architecture_guards.py (the ratchet),
test_architecture_import_contracts.py and test_architecture_counters.py. The how and why
of the ratchets is in the docstring of test_architecture_guards.py.

RENAME MAP (pytest node ids; every id keeps its name and parametrization, only the module changes):
  unchanged in tests/test_architecture_guards.py: test_ratchet
  tests/test_architecture_guards.py::<name> -> tests/test_architecture_import_contracts.py::<name> for: test_import_contracts, test_every_extra_has_a_contract, test_a_split_allowlist_joins_one_file_per_source_module
  tests/test_architecture_guards.py::<name> -> tests/test_architecture_counters.py::<name> for: test_counter_counts_what_it_says, test_duplicate_parser_refuses_a_run_that_measured_nothing, test_kept_unreferenced_functions_are_still_defined_and_unreferenced, test_the_unreferenced_census_counts_what_it_says, test_every_guard_has_its_own_baseline_file_and_nothing_else_is_there, test_a_per_module_ratchet_compares_each_module_with_its_own_file, test_every_per_module_baseline_is_a_positive_count_in_its_own_file, test_a_counter_whose_last_module_reached_zero_has_no_directory
"""

from __future__ import annotations

import ast
import configparser
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ddflow"

#: The committed baselines, one file per guard. A counter's number is in
#: `<counter>.toml`: lower it in the same change that removes the sites (the failure
#: message prints the exact value), never raise it. The functions kept unreferenced on
#: purpose are in `unreferenced_functions.toml`, each contract's import allowlist in
#: `importlinter-<contract>.toml` (a directory of one file per source module for the
#: SPLIT_CONTRACTS).
BASELINES = ROOT / "tests" / "guard_baselines"

#: The one module each pattern belongs in. Sites there are the interface, not a violation.
HOMES: dict[str, frozenset[str]] = {
    "subprocess_calls": frozenset({"ddflow.adapters.proc", "ddflow.adapters.proc_server"}),
    "git_argv": frozenset({"ddflow.adapters.git"}),
    "write_text": frozenset({"ddflow.infra.fsio"}),
    "tempfile": frozenset({"ddflow.adapters.fsio"}),
    "os_replace": frozenset({"ddflow.adapters.fsio", "ddflow.adapters.fs"}),
    "fcntl": frozenset({"ddflow.adapters.fsio"}),
    "hashlib": frozenset({"ddflow.adapters.digest"}),
    "tomlkit": frozenset({"ddflow.adapters.toml"}),
    "time_parse": frozenset({"ddflow.core.clock"}),
}

#: Every counter test_ratchet holds; each has its baseline in BASELINES/<counter>.toml, or
#: -- for the PER_MODULE ones -- in BASELINES/<counter>/<dotted module>.toml.
COUNTERS: tuple[str, ...] = (
    *HOMES,
    "deferred_imports",
    "complexity_d_or_worse",
    "duplicate_code_clusters",
    "unreferenced_functions",
)

#: The counters whose sites sit in one module each, so their baseline is one file per
#: module. (A duplicate-code cluster spans modules and an unreferenced function list is
#: short: those two keep a single file.)
PER_MODULE: tuple[str, ...] = (*HOMES, "deferred_imports", "complexity_d_or_worse")

#: Pinned so the count means the same thing on every machine: no rc file is read, no
#: stats are persisted under the home directory, and six lines is the smallest cluster.
DUPLICATE_ARGS = [
    "--rcfile=/dev/null",
    "--persistent=n",
    "--score=n",
    "--disable=all",
    "--enable=duplicate-code",
    "--min-similarity-lines=6",
    "--ignore-comments=y",
    "--ignore-docstrings=y",
    "--ignore-imports=y",
    "--ignore-signatures=y",
]

_PROCESS_CALLS = frozenset(
    {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "os.system",
        "os.popen",
        "ddflow.infra.proc.run",
        "ddflow.infra.proc.popen",
    }
)

#: pathlib calls whose RESULT is a `Path` -- the constructor and the methods that return
#: one, deterministically. `_resolve` types such a call `pathlib.Path`, so the `os_replace`
#: counter sees `.replace` on it (and on a chain like ``p.replace(a).replace(b)``, where
#: every atomic rename is counted, not just the first). `.rename` and the rest return a
#: Path but are not atomic renames, so they surface for chaining only, not as a counted
#: site.
_PATH_RESULT = frozenset(
    {
        "pathlib.Path",
        "pathlib.Path.replace",
        "pathlib.Path.rename",
        "pathlib.Path.with_name",
        "pathlib.Path.with_suffix",
        "pathlib.Path.with_stem",
        "pathlib.Path.parent",
        "pathlib.Path.resolve",
        "pathlib.Path.absolute",
        "pathlib.Path.expanduser",
        "pathlib.Path.joinpath",
    }
)


#: Methods that parse a timestamp; counted outside `core.clock` as `time_parse`.
_TIME_PARSERS = frozenset({"fromisoformat", "strptime"})

#: Modules whose every use is counted under their own name.
_MODULE_USES = ("tempfile", "fcntl", "hashlib", "tomlkit")


def _modules() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _dotted(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _Sites(ast.NodeVisitor):
    """Collect the sites of each counted pattern in one module, resolving names through
    the module's imports scope by scope (`P` is `infra.proc` in one function of
    surfaces/mcp.py and `services.prompts` in the next)."""

    def __init__(self, module: str, is_package: bool) -> None:
        self.module = module
        self.package = module if is_package else module.rpartition(".")[0]
        self.scopes: list[dict[str, str]] = [{}]
        self.depth = 0  # function nesting
        self.sites: dict[str, list[int]] = {k: [] for k in HOMES}
        self.sites["deferred_imports"] = []

    # -- name resolution ------------------------------------------------------------
    def _absolute(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        base = self.package.split(".")
        base = base[: len(base) - (node.level - 1)]
        return ".".join(base + ([node.module] if node.module else []))

    def _bind(self, name: str, target: str) -> None:
        self.scopes[-1][name] = target

    def _resolve(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            for scope in reversed(self.scopes):
                if node.id in scope:
                    return scope[node.id]
            return None
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value)
            return f"{base}.{node.attr}" if base else None
        if isinstance(node, ast.Call):
            return "pathlib.Path" if self._resolve(node.func) in _PATH_RESULT else None
        return None

    # -- imports --------------------------------------------------------------------
    # An import statement binds a name; it is not itself a use. `import hashlib` and two
    # calls are two sites, as are `from hashlib import sha256` and two calls.
    def visit_Import(self, node: ast.Import) -> None:
        if self.depth:
            self.sites["deferred_imports"].append(node.lineno)
        for alias in node.names:
            if alias.asname:
                self._bind(alias.asname, alias.name)
            else:
                top = alias.name.split(".")[0]
                self._bind(top, top)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.depth:
            self.sites["deferred_imports"].append(node.lineno)
        mod = self._absolute(node)
        for alias in node.names:
            self._bind(alias.asname or alias.name, f"{mod}.{alias.name}")

    # -- scopes ---------------------------------------------------------------------
    def _function(self, node: ast.AST) -> None:
        self.scopes.append({})
        for arg in getattr(node.args, "args", ()):
            if (
                isinstance(arg, ast.arg)
                and isinstance(arg.annotation, ast.Name)
                and self._resolve(arg.annotation) == "pathlib.Path"
            ):
                self._bind(arg.arg, "pathlib.Path")
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        self.scopes.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            isinstance(node.target, ast.Name)
            and isinstance(node.annotation, ast.Name)
            and self._resolve(node.annotation) == "pathlib.Path"
        ):
            self._bind(node.target.id, "pathlib.Path")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._resolve(node.value) == "pathlib.Path":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._bind(target.id, "pathlib.Path")
        self.generic_visit(node)

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function
    visit_Lambda = _function

    # -- uses -----------------------------------------------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):  # tempfile.mkstemp, hashlib.sha256, ...
            base = self._resolve(node.value)
            if base in _MODULE_USES:
                self.sites[base].append(node.lineno)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # sha256 after `from hashlib import`
        name = self._resolve(node)
        if name and "." in name and name.split(".")[0] in _MODULE_USES:
            self.sites[name.split(".")[0]].append(node.lineno)

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve(node.func)
        if name in _PROCESS_CALLS:
            self.sites["subprocess_calls"].append(node.lineno)
        if name in ("os.replace", "pathlib.Path.replace"):
            self.sites["os_replace"].append(node.lineno)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "write_text":
            self.sites["write_text"].append(node.lineno)
        # `datetime.fromisoformat`, `date.fromisoformat`, `datetime.strptime`: a timestamp
        # parsed outside `core.clock` (B-uni-clock), by any receiver -- or by a name bound
        # to one (`from time import strptime`).
        if (isinstance(node.func, ast.Attribute) and node.func.attr in _TIME_PARSERS) or (
            isinstance(node.func, ast.Name)
            and (name or "").rpartition(".")[2] in _TIME_PARSERS
            and "." in (name or "")
        ):
            self.sites["time_parse"].append(node.lineno)
        self.generic_visit(node)

    def _sequence(self, node: ast.List | ast.Tuple) -> None:
        first = node.elts[0] if node.elts else None
        if isinstance(first, ast.Constant) and first.value == "git":
            self.sites["git_argv"].append(node.lineno)
        self.generic_visit(node)

    visit_List = _sequence
    visit_Tuple = _sequence


def _ast_sites() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: [] for k in (*HOMES, "deferred_imports")}
    for path in _modules():
        module = _dotted(path)
        visitor = _Sites(module, path.name == "__init__.py")
        visitor.visit(ast.parse(path.read_text("utf-8"), str(path)))
        rel = path.relative_to(ROOT)
        for kind, lines in visitor.sites.items():
            if module in HOMES.get(kind, ()):
                continue
            out[kind].extend(f"{rel}:{line}" for line in sorted(lines))
    return out


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _unreferenced(sources: dict[str, str]) -> list[str]:
    """`path:line name` of each module-level function whose name appears nowhere in
    `sources` (path -> text) but its own `def`: not as a name, an attribute, an import,
    or an identifier inside a string (`python -c "from m import f"`, a dispatch table).
    By name, not by binding: a name used anywhere counts as used everywhere, so this
    under-reports and never calls live code dead."""
    defs: list[tuple[str, int, str]] = []
    used: set[str] = set()
    for path, text in sources.items():
        tree = ast.parse(text, path)
        defs += [
            (path, n.lineno, n.name)
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                used.add(n.id)
            elif isinstance(n, ast.Attribute):
                used.add(n.attr)
            elif isinstance(n, ast.alias):
                used.update(filter(None, (n.name.rpartition(".")[2], n.asname)))
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                used.update(_IDENTIFIER.findall(n.value))
    return [
        f"{path}:{line} {name}"
        for path, line, name in defs
        if name not in used and not name.startswith("__")
    ]


def _unreferenced_functions() -> list[str]:
    found = _unreferenced({str(p.relative_to(ROOT)): p.read_text("utf-8") for p in _modules()})
    kept = _kept_unreferenced()
    return [f for f in found if _kept_key(f) not in kept]


def _kept_key(site: str) -> str:
    where, _, name = site.partition(" ")
    return f"{where.rpartition(':')[0]}:{name}"


_CACHE: dict[str, list[str]] = {}


def _sites(kind: str) -> list[str]:
    if not _CACHE:
        _CACHE.update(_ast_sites())
    return _CACHE[kind]


def _complex_functions() -> list[str]:
    cc = pytest.importorskip("radon.complexity", reason="radon (a dev dependency) is not installed")
    out = []
    for path in _modules():
        rel = path.relative_to(ROOT)
        # cc_visit lists every function and method once, plus a block per class whose
        # methods are those same entries again: count functions only, as `radon cc` does.
        for fn in cc.cc_visit(path.read_text("utf-8")):
            if hasattr(fn, "methods"):
                continue
            if fn.complexity > 20:  # radon's grade D starts at 21
                out.append(f"{rel}:{fn.lineno} {fn.name} CC {fn.complexity}")
    return out


def _duplicate_clusters() -> list[str]:
    pytest.importorskip("pylint", reason="pylint (a dev dependency) is not installed")
    proc = subprocess.run(
        [sys.executable, "-m", "pylint", *DUPLICATE_ARGS, "ddflow"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=300,
        check=False,
    )
    return _parse_duplicates(proc.returncode, proc.stdout + proc.stderr)


def _parse_duplicates(returncode: int, output: str) -> list[str]:
    """The R0801 clusters in pylint's text output, each as its `module:[lines]` members.

    pylint's exit status is a bit mask: 1 fatal, 2 error, 32 usage error; 4, 8 and 16
    only say which message categories were emitted. A run with any of the first three
    did not measure anything, and its empty output must not read as "no clusters" (the
    ratchet would then ask for a baseline of 0 and stop guarding)."""
    assert not returncode & (1 | 2 | 32), f"pylint could not run (exit {returncode}):\n{output}"
    clusters: list[list[str]] = []
    current: list[str] | None = None
    for line in output.splitlines():
        if "R0801" in line:
            current = []
            clusters.append(current)
        elif current is not None and line.startswith("=="):
            current.append(line[2:])
        else:
            current = None
    assert all(clusters), f"an R0801 message without its members:\n{output}"
    return [" ~ ".join(c) for c in clusters]


def _measure(kind: str) -> list[str]:
    if kind == "complexity_d_or_worse":
        return _complex_functions()
    if kind == "duplicate_code_clusters":
        return _duplicate_clusters()
    if kind == "unreferenced_functions":
        return _unreferenced_functions()
    return _sites(kind)


def _baseline_path(name: str) -> Path:
    return BASELINES / f"{name}.toml"


def _module_baseline_path(kind: str, module: str) -> Path:
    return BASELINES / kind / f"{module}.toml"


def _module_of(site: str) -> str:
    """The dotted module a counted site (`ddflow/x/y.py:12 ...`) is in."""
    return _dotted(ROOT / site.split(":", 1)[0])


def _by_module(sites: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for site in sites:
        out.setdefault(_module_of(site), []).append(site)
    return out


def _module_baselines(kind: str) -> dict[str, int]:
    """{module: baseline} from `<kind>/*.toml`; a module with no file has none."""
    out: dict[str, int] = {}
    directory = BASELINES / kind  # absent once its last module reached zero: git keeps no empty dir
    for path in sorted(directory.glob("*.toml")) if directory.is_dir() else ():
        rel = path.relative_to(ROOT)
        data = tomllib.loads(path.read_text("utf-8"))
        assert set(data) == {"baseline"}, (
            f"{rel} holds {sorted(data)}; it must hold exactly baseline"
        )
        value = data["baseline"]
        assert type(value) is int and value > 0, (
            f"{rel}: baseline must be a count above zero, not {value!r} "
            "(a module with no sites has no file: delete it)"
        )
        out[path.stem] = value
    return out


def _read_baseline(name: str, keys: set[str]) -> dict:
    """The baseline file `name`, which must hold exactly `keys` (a misspelt key would
    otherwise read as a missing baseline, or be ignored)."""
    path = _baseline_path(name)
    rel = path.relative_to(ROOT)
    assert path.is_file(), f"{rel} is missing: every guard reads its baseline from its own file"
    data = tomllib.loads(path.read_text("utf-8"))
    assert set(data) == keys, f"{rel} holds {sorted(data)}; it must hold exactly {sorted(keys)}"
    return data


def _baseline(kind: str) -> int:
    keys = {"baseline", "kept"} if kind == "unreferenced_functions" else {"baseline"}
    value = _read_baseline(kind, keys)["baseline"]
    assert type(value) is int and value >= 0, (
        f"{_baseline_path(kind).relative_to(ROOT)}: baseline must be a count, not {value!r}"
    )
    return value


def _kept_unreferenced() -> dict[str, str]:
    """Unreferenced on purpose, each with the task that wires it in."""
    kept = _read_baseline("unreferenced_functions", {"baseline", "kept"})["kept"]
    assert isinstance(kept, dict) and all(isinstance(v, str) for v in kept.values()), kept
    return kept


#: The contracts whose allowlist is a directory `importlinter-<contract>/<dotted source
#: module>.toml`, so tasks shrinking different modules never edit one file. The others
#: stay one file, `importlinter-<contract>.toml`.
SPLIT_CONTRACTS = frozenset({"surfaces-through-api", "subprocess-home", "fsio-home"})


def _entries(path: Path) -> list[str]:
    rel = path.relative_to(ROOT)
    assert path.is_file(), f"{rel} is missing: every guard reads its baseline from its own file"
    data = tomllib.loads(path.read_text("utf-8"))
    assert set(data) == {"ignore_imports"}, (
        f"{rel} holds {sorted(data)}; it must hold exactly ignore_imports"
    )
    entries = data["ignore_imports"]
    assert isinstance(entries, list) and all(isinstance(e, str) for e in entries), entries
    return entries


def _allowlist(contract: str) -> list[str]:
    """The `ignore_imports` entries of one `.importlinter` contract. A split contract's
    come from one file per source module (absent once the module reached zero)."""
    if contract not in SPLIT_CONTRACTS:
        return _entries(_baseline_path(f"importlinter-{contract}"))
    directory = BASELINES / f"importlinter-{contract}"
    out: list[str] = []
    for path in sorted(directory.glob("*.toml")) if directory.is_dir() else ():
        entries = _entries(path)
        assert entries, f"{path.relative_to(ROOT)} is empty: a module with no entries has no file"
        wrong = [e for e in entries if e.split(" -> ")[0] != path.stem]
        assert not wrong, f"{path.relative_to(ROOT)} holds entries of other modules: {wrong}"
        out += entries
    return out


def _hint(kind: str) -> str:
    return (
        "P-unify is retiring; use the shared interface"
        + (f" ({', '.join(sorted(HOMES[kind]))})" if kind in HOMES else "")
        + " instead."
    )


def _ratchet_problems(
    kind: str, sites: list[str], baselines: dict[str, int]
) -> tuple[list[str], list[str]]:
    """(above, below): what a module-by-module comparison finds wrong, as messages."""
    found = _by_module(sites)
    above: list[str] = []
    below: list[str] = []
    for module in sorted(found.keys() | baselines.keys()):
        count, baseline = len(found.get(module, ())), baselines.get(module, 0)
        path = _module_baseline_path(kind, module).relative_to(ROOT)
        if count > baseline:
            above.append(
                f"{kind}: {count} sites in {module}, the baseline is {baseline}. New code used "
                f"a pattern {_hint(kind)} Every current site there:\n  "
                + "\n  ".join(found[module])
            )
        elif count < baseline:
            below.append(
                f"{kind}: {count} sites in {module}, below the baseline of {baseline}. Good -- "
                + (
                    f"now delete {path} in this same change, so the sites cannot grow back."
                    if not count
                    else f"now lower it: set baseline = {count} in {path} in this same change, "
                    "so the sites cannot grow back."
                )
            )
    return above, below


def _importlinter() -> configparser.ConfigParser:
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(ROOT / ".importlinter", encoding="utf-8")
    return ini


def _contracts(
    ini: configparser.ConfigParser | None = None,
) -> dict[str, configparser.SectionProxy]:
    if ini is None:
        ini = _importlinter()
    prefix = "importlinter:contract:"
    return {name[len(prefix) :]: ini[name] for name in ini.sections() if name.startswith(prefix)}


def _two_homes(contract: str) -> str:
    home = f"tests/guard_baselines/importlinter-{contract}"
    home += "/<source module>.toml" if contract in SPLIT_CONTRACTS else ".toml"
    return f".importlinter lists ignore_imports for {contract}: move them to {home}, their one home"
