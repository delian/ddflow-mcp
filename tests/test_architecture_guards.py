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
import-linter here; that file's allowlists are ratchets of the same kind.

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

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ddflow"

#: The committed baseline. Lower a number in the same change that removes the sites; the
#: failure message prints the exact value. Never raise one.
BASELINE: dict[str, int] = {
    # subprocess.run/Popen/call/check_*/getoutput, os.system/popen, and the thin
    # infra.proc.run/popen wrappers, outside ddflow.infra.proc and ddflow.infra.git
    "subprocess_calls": 42,
    # a list or tuple literal starting with "git" (a git argv) outside ddflow.infra.git
    "git_argv": 26,
    # Path.write_text outside ddflow.infra.fsio
    "write_text": 40,
    # any use of the tempfile module outside ddflow.infra.fsio
    "tempfile": 12,
    # os.replace outside ddflow.infra.fsio
    "os_replace": 1,
    # any use of the fcntl module outside ddflow.infra.fsio
    "fcntl": 0,
    # any use of the hashlib module outside ddflow.core.digest
    "hashlib": 23,
    # an import statement inside a function body
    "deferred_imports": 620,
    # functions and methods of radon cyclomatic-complexity grade D or worse (CC > 20)
    "complexity_d_or_worse": 112,
    # pylint duplicate-code (R0801) clusters, with DUPLICATE_ARGS below
    "duplicate_code_clusters": 6,
    # module-level functions whose name nothing in ddflow/ mentions, less KEPT_UNREFERENCED
    "unreferenced_functions": 6,
}

#: Unreferenced on purpose: the work that wires each in is planned (B-uni-dead-code). An
#: entry leaves this list in the change that gives it a caller -- a test holds that -- and a
#: new unreferenced function is either wired, deleted, or listed here with its task.
#: (Not counted at all, so not listed: argparse `Action.__call__`'s `option_string`, which
#: vulture reports at cli.py and is the protocol's signature, not dead code.)
KEPT_UNREFERENCED: dict[str, str] = {
    # Claude Code's server approval (B-onboard-harness): the per-descriptor approval step
    # of B-hx-onboard-generic is its caller; the onboard prompt does it by hand until then.
    "ddflow/services/harness.py:enable_project_servers": "B-hx-onboard-generic",
    # Adaptive flow (D-adaptive-flow): the admission target; the quota signal is the
    # remaining wiring. (The doctor's too-little-history notes are wired: Bc6784dab3c.)
    "ddflow/core/flowcontrol.py:decide_admission": "B-af-quota-signal",
    # Usage quotas (D-quotas): the api awaits its surfaces.
    "ddflow/api/quota.py:quota_declare": "BL-quotas",
    "ddflow/api/quota.py:quota_show": "BL-quotas",
    "ddflow/api/quota.py:quota_list": "BL-quotas",
    "ddflow/api/quota.py:quota_forget": "BL-quotas",
}

#: The one module each pattern belongs in. Sites there are the interface, not a violation.
HOMES: dict[str, frozenset[str]] = {
    "subprocess_calls": frozenset({"ddflow.infra.proc", "ddflow.infra.git"}),
    "git_argv": frozenset({"ddflow.infra.git"}),
    "write_text": frozenset({"ddflow.infra.fsio"}),
    "tempfile": frozenset({"ddflow.infra.fsio"}),
    "os_replace": frozenset({"ddflow.infra.fsio"}),
    "fcntl": frozenset({"ddflow.infra.fsio"}),
    "hashlib": frozenset({"ddflow.core.digest"}),
}

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


#: Modules whose every use is counted under their own name.
_MODULE_USES = ("tempfile", "fcntl", "hashlib")


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
    return [f for f in found if _kept_key(f) not in KEPT_UNREFERENCED]


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


@pytest.mark.parametrize("kind", sorted(BASELINE))
def test_ratchet(kind: str) -> None:
    sites = _measure(kind)
    count, baseline = len(sites), BASELINE[kind]
    assert count <= baseline, (
        f"{kind}: {count} sites, the baseline is {baseline}. New code used a pattern "
        f"P-unify is retiring; use the shared interface"
        + (f" ({', '.join(sorted(HOMES[kind]))})" if kind in HOMES else "")
        + " instead. Every current site:\n  "
        + "\n  ".join(sites)
    )
    assert count >= baseline, (
        f"{kind}: {count} sites, below the baseline of {baseline}. Good -- now lower it: "
        f'set BASELINE["{kind}"] = {count} in tests/test_architecture_guards.py in this '
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


def _contracts() -> dict[str, configparser.SectionProxy]:
    ini = configparser.ConfigParser()
    ini.read(ROOT / ".importlinter", encoding="utf-8")
    prefix = "importlinter:contract:"
    return {name[len(prefix) :]: ini[name] for name in ini.sections() if name.startswith(prefix)}


def test_import_contracts() -> None:
    """The contracts in `.importlinter` are all there, each one is kept, and none of their
    allowlist entries is stale (`unmatched_ignore_imports_alerting = error`)."""
    pytest.importorskip("importlinter", reason="import-linter (a dev dependency) is not installed")
    contracts = _contracts()
    assert CONTRACTS <= set(contracts), f"contracts missing: {sorted(CONTRACTS - set(contracts))}"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from importlinter.cli import lint_imports; "
            "sys.exit(lint_imports(config_filename='.importlinter', no_cache=True, no_logo=True))",
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
        "a refactor moved an allowed import -- move its ignore_imports entry; never add "
        "one for new code.\n" + output
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
    stale = sorted(set(KEPT_UNREFERENCED) - found)
    assert not stale, (
        f"no longer unreferenced (wired or gone): remove from KEPT_UNREFERENCED: {stale}"
    )


def test_the_unreferenced_census_counts_what_it_says() -> None:
    sources = {
        "a.py": "def used(): pass\ndef dead(): pass\ndef by_attr(): pass\ndef __dunder__(): pass\n"
        "def in_string(): pass\nclass C:\n    def method_only(self): pass\n",
        "b.py": "from a import used\nimport a\na.by_attr()\nCMD = 'from a import in_string as m; m()'\n",
    }
    assert _unreferenced(sources) == ["a.py:2 dead"]
