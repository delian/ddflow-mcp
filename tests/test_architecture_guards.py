"""Architecture guards as ratchets (D-unify 4: strangler with ratchets).

P-unify replaces many ad-hoc ways of doing one thing with one shared interface each. A
rule that only says "use the shared interface" decays the way the layer rule did before
tests/test_layering.py. So every pattern being retired is COUNTED here, the count is
compared with a committed baseline, and the baseline can only go down:

- a count ABOVE its baseline fails: new code used the old pattern. Use the shared
  interface instead (the failure names every site, so the new one is in the list);
- a count BELOW its baseline fails too, and says the number to write: the change that
  removed the sites lowers the baseline in the same commit, so nothing can quietly
  grow back into the room it left.

The import rules (layers, surfaces through api, one home each for subprocess, tempfile,
fcntl, hashlib and the optional extras) live in `.importlinter` and are checked by
import-linter here; their allowlists are ratchets of the same kind.

Every baseline is a file of its own under tests/guard_baselines/. A counter that finds
its sites module by module (every pattern counter, and complexity) keeps a directory,
`<counter>/<dotted module>.toml`, holding that module's number: a task that removes the
sites of `ddflow.services.enforce` lowers -- and declares in its globs -- only
`<counter>/ddflow.services.enforce.toml`, so lanes working in different modules never
touch the same file. A module with no sites has no file (a file reaching zero is deleted).
A counter whose last module reaches zero has no directory at all (git tracks no empty
directory), so absence means no baselines.
`<counter>.toml` holds the number of the other counters (`unreferenced_functions.toml`
also the functions kept unreferenced on purpose), and `importlinter-<contract>.toml` one
contract's `ignore_imports` allowlist, which this module joins back into the contract
before import-linter runs. This module changes only when a guard is added or what it
measures changes.

Counted on the source with `ast`, never by running anything, so the guard is the same on
every platform and takes seconds. Complexity is radon's grade; duplicate code is pylint's
similarity checker with the settings pinned below. Both are dev dependencies: a missing
one SKIPS its test with the reason (it is never counted as zero).
"""

from __future__ import annotations

import ast
import configparser
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

#: What this checks, read by `ddflow tests --item` (B2a1eaa259e): a change under one of
#: these paths selects this test, which imports nothing it governs.
GOVERNS = ("ddflow/**", ".importlinter", "tests/guard_baselines/**")

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ddflow"

#: The committed baselines, one file per guard. A counter's number is in
#: `<counter>.toml`: lower it in the same change that removes the sites (the failure
#: message prints the exact value), never raise it. The functions kept unreferenced on
#: purpose are in `unreferenced_functions.toml`, each contract's import allowlist in
#: `importlinter-<contract>.toml`.
BASELINES = ROOT / "tests" / "guard_baselines"

#: The one module each pattern belongs in. Sites there are the interface, not a violation.
HOMES: dict[str, frozenset[str]] = {
    "subprocess_calls": frozenset({"ddflow.infra.proc", "ddflow.infra.git"}),
    "git_argv": frozenset({"ddflow.infra.git"}),
    "write_text": frozenset({"ddflow.infra.fsio"}),
    "tempfile": frozenset({"ddflow.infra.fsio"}),
    "os_replace": frozenset({"ddflow.infra.fsio"}),
    "fcntl": frozenset({"ddflow.infra.fsio"}),
    "hashlib": frozenset({"ddflow.core.digest"}),
    "tomlkit": frozenset({"ddflow.infra.tomlcfg"}),
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
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        self.scopes.pop()

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
        if name == "os.replace":
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


def _allowlist(contract: str) -> list[str]:
    """The `ignore_imports` entries of one `.importlinter` contract."""
    entries = _read_baseline(f"importlinter-{contract}", {"ignore_imports"})["ignore_imports"]
    assert isinstance(entries, list) and all(isinstance(e, str) for e in entries), entries
    return entries


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


@pytest.mark.parametrize("kind", sorted(COUNTERS))
def test_ratchet(kind: str) -> None:
    sites = _measure(kind)
    if kind in PER_MODULE:
        above, below = _ratchet_problems(kind, sites, _module_baselines(kind))
        assert not above, "\n".join(above)
        assert not below, "\n".join(below)
        return
    baseline = _baseline(kind)
    count = len(sites)
    assert count <= baseline, (
        f"{kind}: {count} sites, the baseline is {baseline}. New code used a pattern "
        f"{_hint(kind)} Every current site:\n  " + "\n  ".join(sites)
    )
    assert count >= baseline, (
        f"{kind}: {count} sites, below the baseline of {baseline}. Good -- now lower it: "
        f"set baseline = {count} in {_baseline_path(kind).relative_to(ROOT)} in this "
        "same change, so the sites cannot grow back."
    )


#: The contracts `.importlinter` must hold: a missing section is a guard removed, and
#: import-linter itself reports success for a file with no contracts at all.
CONTRACTS = frozenset(
    {
        "layers",
        "surfaces-through-api",
        "subprocess-home",
        "fsio-home",
        "digest-home",
        "extras-one-adapter",
    }
)


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
    return (
        f".importlinter lists ignore_imports for {contract}: move them to "
        f"{_baseline_path(f'importlinter-{contract}').relative_to(ROOT)}, their one home"
    )


def _joined_importlinter(directory: Path) -> Path:
    """`.importlinter` with each contract's allowlist joined back from its baseline file,
    written into `directory` for import-linter to read."""
    ini = _importlinter()
    for contract, section in _contracts(ini).items():
        assert "ignore_imports" not in section, _two_homes(contract)
        entries = _allowlist(contract)
        if entries:
            section["ignore_imports"] = "\n" + "\n".join(entries)
    path = directory / ".importlinter"
    with path.open("w", encoding="utf-8") as out:
        ini.write(out)
    return path


def test_import_contracts(tmp_path: Path) -> None:
    """The contracts in `.importlinter` are all there, each one is kept, and none of their
    allowlist entries is stale (`unmatched_ignore_imports_alerting = error`)."""
    pytest.importorskip("importlinter", reason="import-linter (a dev dependency) is not installed")
    contracts = _contracts()
    assert CONTRACTS <= set(contracts), f"contracts missing: {sorted(CONTRACTS - set(contracts))}"
    config = _joined_importlinter(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from importlinter.cli import lint_imports; "
            f"sys.exit(lint_imports(config_filename={str(config)!r}, no_cache=True, no_logo=True))",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=300,
        check=False,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        "an architecture contract in .importlinter is broken. Fix the import, or -- when "
        "a refactor moved an allowed import -- move its ignore_imports entry in "
        "tests/guard_baselines/importlinter-<contract>.toml; never add one for new code.\n" + output
    )
    # Exit 0 already means "every contract it loaded was kept"; this says it loaded them all.
    assert f"Contracts: {len(contracts)} kept, 0 broken." in output, output


#: Extra -> the top-level import names its packages provide.
_EXTRA_MODULES: dict[str, list[str]] = {
    "search": ["rapidfuzz"],
    "watch": ["watchfiles"],
    "rag": ["model2vec", "sqlite_vec"],
    "mcp-sdk": ["mcp"],
}


def test_every_extra_has_a_contract() -> None:
    """Each optional extra pyproject declares is named in the one-adapter contract, so an
    extra cannot be added without its import being confined to one module."""
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"].get(
        "optional-dependencies", {}
    )
    assert set(extras) == set(_EXTRA_MODULES), (
        f"pyproject declares extras {sorted(extras)}; this test knows {sorted(_EXTRA_MODULES)}"
    )
    named = set(_contracts()["extras-one-adapter"]["forbidden_modules"].split())
    missing = {e: [m for m in mods if m not in named] for e, mods in _EXTRA_MODULES.items()}
    assert not any(missing.values()), f"extras whose import names are not forbidden: {missing}"


def _iter_counter_cases() -> Iterator[tuple[str, str, int]]:
    yield (
        "import subprocess\nsubprocess.run(['git', 'status'])\n",
        "subprocess_calls",
        1,
    )
    yield ("import subprocess as sp\nsp.Popen(['ls'])\n", "subprocess_calls", 1)
    yield ("from ..infra import proc as P\nP.run(['ls'])\n", "subprocess_calls", 1)
    yield ("from ..infra import proc as P\nP.run_shell('ls')\n", "subprocess_calls", 0)
    yield (
        "def f():\n    from ..services import prompts as P\n    P.run()\n"
        "def g():\n    from ..infra import proc as P\n    P.run()\n",
        "subprocess_calls",
        1,
    )
    yield ("x = ['git', 'log']\ny = ('git',)\nz = ['gitx']\n", "git_argv", 2)
    yield ("p.write_text('x')\np.read_text()\n", "write_text", 1)
    yield ("import os\nos.replace(a, b)\nos.rename(a, b)\n", "os_replace", 1)
    yield ("import hashlib\nhashlib.sha256(b'').hexdigest()\n", "hashlib", 1)
    yield ("from hashlib import sha256\nsha256(b'')\nsha256(b'x')\n", "hashlib", 2)
    yield ("import fcntl\nfcntl.flock(f, fcntl.LOCK_EX)\n", "fcntl", 2)
    yield ("import tempfile\ntempfile.mkdtemp()\ntempfile.mkstemp()\n", "tempfile", 2)
    yield ("def f():\n    import os\n    from . import x\nimport sys\n", "deferred_imports", 2)
    yield (
        "from datetime import date, datetime\n"
        "datetime.fromisoformat(a)\ndate.fromisoformat(b)\ndatetime.strptime(c, f)\n"
        "clock.parse_ts(a)\n",
        "time_parse",
        3,
    )
    yield (
        "from time import strptime\nfrom datetime import datetime as dt\n"
        "strptime(s, f)\ndt.fromisoformat(a)\nfromisoformat = 1\n",
        "time_parse",
        2,
    )


@pytest.mark.parametrize("source,kind,expected", list(_iter_counter_cases()))
def test_counter_counts_what_it_says(source: str, kind: str, expected: int) -> None:
    """The counters themselves: a ratchet that miscounts holds nothing."""
    visitor = _Sites("ddflow.services.example", False)
    visitor.visit(ast.parse(source))
    assert len(visitor.sites[kind]) == expected, visitor.sites[kind]


def test_duplicate_parser_refuses_a_run_that_measured_nothing() -> None:
    """A crashed pylint prints no R0801 lines; that is "could not run", never zero."""
    with pytest.raises(AssertionError, match="could not run"):
        _parse_duplicates(1, "astroid crashed")
    with pytest.raises(AssertionError, match="could not run"):
        _parse_duplicates(2, "")
    sample = (
        "x.py:1:0: R0801: Similar lines in 2 files\n==a.b:[1:9]\n==a.c:[4:12]\n    code\n"
        "y.py:1:0: R0801: Similar lines in 2 files\n==a.d:[1:9]\n==a.e:[2:10]\n"
    )
    assert _parse_duplicates(8, sample) == ["a.b:[1:9] ~ a.c:[4:12]", "a.d:[1:9] ~ a.e:[2:10]"]
    assert _parse_duplicates(0, "") == []


def test_kept_unreferenced_functions_are_still_defined_and_unreferenced() -> None:
    """An entry is a function that exists and nothing calls yet. Wired, it leaves the list
    (the ratchet would no longer see it, so a stale entry would hide the next one)."""
    found = {
        _kept_key(f)
        for f in _unreferenced({str(p.relative_to(ROOT)): p.read_text("utf-8") for p in _modules()})
    }
    stale = sorted(set(_kept_unreferenced()) - found)
    assert not stale, (
        "no longer unreferenced (wired or gone): remove from [kept] in "
        f"tests/guard_baselines/unreferenced_functions.toml: {stale}"
    )


def test_the_unreferenced_census_counts_what_it_says() -> None:
    sources = {
        "a.py": "def used(): pass\ndef dead(): pass\ndef by_attr(): pass\ndef __dunder__(): pass\n"
        "def in_string(): pass\nclass C:\n    def method_only(self): pass\n",
        "b.py": "from a import used\nimport a\na.by_attr()\nCMD = 'from a import in_string as m; m()'\n",
    }
    assert _unreferenced(sources) == ["a.py:2 dead"]


def test_every_guard_has_its_own_baseline_file_and_nothing_else_is_there() -> None:
    """One file per guard (a directory of files, one per module, for the PER_MODULE
    counters): each counter and each `.importlinter` contract has its baseline
    file, holding exactly its keys; `.importlinter` itself lists no allowlist (two homes
    for one list would drift); and no other file is there -- one left behind by a renamed
    or removed guard would be read by nothing while looking like it still guarded."""
    contracts = _contracts()
    # A per-module counter's directory exists while some module still has sites.
    expected = {f"{k}.toml" for k in COUNTERS if k not in PER_MODULE} | {
        f"importlinter-{c}.toml" for c in contracts
    }
    present = {p.name for p in BASELINES.iterdir() if not p.name.startswith(".")}
    assert present - set(PER_MODULE) == expected, (
        f"missing: {sorted(expected - present)}; "
        f"read by no guard: {sorted(present - expected - set(PER_MODULE))}"
    )
    modules = {_dotted(p) for p in _modules()}
    for kind in COUNTERS:
        if kind in PER_MODULE:
            stray = sorted(set(_module_baselines(kind)) - modules)
            assert not stray, f"{kind}: a baseline file for a module that does not exist: {stray}"
        else:
            _baseline(kind)
    _kept_unreferenced()
    for contract, section in contracts.items():
        _allowlist(contract)
        assert "ignore_imports" not in section, _two_homes(contract)


def test_a_per_module_ratchet_compares_each_module_with_its_own_file() -> None:
    """The comparison itself: above fails naming the module and its sites, below names the
    file to lower or delete, a module with no file has a baseline of zero, and a module
    that stays at its number passes."""
    sites = ["ddflow/services/a.py:3", "ddflow/services/a.py:9", "ddflow/infra/b.py:1"]
    above, below = _ratchet_problems("deferred_imports", sites, {"ddflow.services.a": 2})
    assert not below
    assert len(above) == 1 and "1 sites in ddflow.infra.b, the baseline is 0" in above[0]
    assert "ddflow/infra/b.py:1" in above[0]

    above, below = _ratchet_problems(
        "deferred_imports", sites[:1], {"ddflow.services.a": 2, "ddflow.core.gone": 1}
    )
    assert not above
    assert any("set baseline = 1" in m and "ddflow.services.a.toml" in m for m in below)
    assert any("delete" in m and "ddflow.core.gone.toml" in m for m in below)

    assert _ratchet_problems(
        "deferred_imports", sites, {"ddflow.services.a": 2, "ddflow.infra.b": 1}
    ) == ([], [])


def test_every_per_module_baseline_is_a_positive_count_in_its_own_file(
    tmp_path, monkeypatch
) -> None:
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "BASELINES", tmp_path / "guard_baselines")
    (tmp_path / "guard_baselines" / "git_argv").mkdir(parents=True)
    (tmp_path / "guard_baselines" / "git_argv" / "ddflow.services.a.toml").write_text(
        "baseline = 2\n"
    )
    assert _module_baselines("git_argv") == {"ddflow.services.a": 2}
    (tmp_path / "guard_baselines" / "git_argv" / "ddflow.services.b.toml").write_text(
        "baseline = 0\n"
    )
    with pytest.raises(AssertionError, match="delete it"):
        _module_baselines("git_argv")


def test_a_counter_whose_last_module_reached_zero_has_no_directory(tmp_path, monkeypatch) -> None:
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "BASELINES", tmp_path / "guard_baselines")
    (tmp_path / "guard_baselines").mkdir()
    assert _module_baselines("fcntl") == {}
