"""Ratchet (D-unify 4): no NEW direct clock or environment reads in core and services.

`time.time()`, `time.monotonic()`, `os.environ` and `os.getenv` are counted with `ast` (so a
docstring or comment never counts) per file under ddflow/core and ddflow/services, however the
module was imported: `import time as t`, `from time import time as now`, `from os import
environ`. The baseline `tests/ratchets/clock_env.json` holds the count each file had when the
ports landed; it only SHRINKS: a file may not read more, and a file that reads fewer must lower
its number here (or drop its entry). Migrate a site by taking a `Clock` / `Env` argument
(ddflow/adapters/clock.py, env.py). Limits, by design: the counter is by name, so an assignment
alias (`_os = os`) or `x.time.monotonic()` on a non-module are not told apart (the second
over-counts, which only fails loud). After a migration, lower the baseline with
`uv run python -m tests.test_adapters_clock_env`; it refuses to raise any number.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "ratchets" / "clock_env.json"
LAYERS = ("ddflow/core", "ddflow/services")
#: module -> the attributes of it that are a direct read
READS = {"time": {"time", "monotonic"}, "os": {"environ", "getenv"}}


def _from_import(node: ast.ImportFrom, mods: dict[str, str], names: dict[str, str]) -> None:
    root = (node.module or "").split(".")[0]
    if node.level or root not in READS:
        return
    for a in node.names:
        if node.module != root:  # `from os.path import os` re-exports the module itself
            if a.name in READS:
                mods[a.asname or a.name] = a.name
        elif a.name == "*":  # `from time import *` binds every public name
            names.update({n: f"{root}.{n}" for n in READS[root]})
        elif a.name in READS[root]:
            names[a.asname or a.name] = f"{root}.{a.name}"


def _aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """(local name -> module it stands for, local name -> 'module.attr' it was imported as)."""
    mods: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]  # `import os.path` binds `os`
                if root in READS:
                    mods[a.asname or root] = root
        elif isinstance(node, ast.ImportFrom):
            _from_import(node, mods, names)
    return mods, names


def count_reads(tree: ast.AST) -> int:
    """Direct reads of the host clock or environment. A receiver counts when it is a name bound
    to the module (`time`, `os`, or an alias) or any `<x>.time` / `<x>.os` module reference
    (`_leases.time.time()`); an injected port stored as `self._time` does not."""
    mods, names = _aliases(tree)
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names and isinstance(node.ctx, ast.Load):
            n += 1
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            v = node.value
            if isinstance(v, ast.Name):
                mod = mods.get(v.id)
            else:
                mod = v.attr if isinstance(v, ast.Attribute) and v.attr in READS else None
            if mod and node.attr in READS[mod]:
                n += 1
    return n


def measure() -> dict[str, int]:
    found: dict[str, int] = {}
    for layer in LAYERS:
        for path in sorted((ROOT / layer).rglob("*.py")):
            n = count_reads(ast.parse(path.read_text(encoding="utf-8")))
            if n:
                found[path.relative_to(ROOT).as_posix()] = n
    return found


def test_the_counter_sees_calls_and_aliases_but_not_text_or_an_injected_port():
    src = (
        'import os, time\n"""time.time() os.environ"""\n# os.getenv\n'
        "x = time.time() + _t.time.monotonic()\ny = os.environ.get('A') or os.getenv('B')\n"
    )
    assert count_reads(ast.parse(src)) == 4
    aliased = "import time as mytime\nfrom time import time as now\nfrom os import environ\n"
    aliased += "def f(): return mytime.monotonic() + now() + environ['HOME']\n"
    assert count_reads(ast.parse(aliased)) == 3  # each from-import counts where it is used
    port = "class X:\n    def __init__(self, c): self._time = c\n    def f(self): return self._time.monotonic()\n"
    assert count_reads(ast.parse(port)) == 0
    own = "import os\nclass C:\n    def __init__(self): self.os = os\n    def f(self): return self.os.environ\n"
    assert count_reads(ast.parse(own)) == 1
    assert count_reads(ast.parse("from os import environ\nenviron['X']\n")) == 1
    assert count_reads(ast.parse("from os import environ\nenviron.get('X')\n")) == 1
    assert count_reads(ast.parse("from os.path import os\nos.environ\n")) == 1
    assert count_reads(ast.parse("from time import *\nx = time() + monotonic()\n")) == 2
    assert count_reads(ast.parse("import os.path\nos.environ['HOME']\n")) == 1
    assert count_reads(ast.parse("import time\ntime.time = lambda: 1\n")) == 0  # a store is no read


def test_no_file_reads_the_clock_or_environment_directly_more_than_its_baseline():
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    now = measure()
    grew = {f: (base.get(f, 0), n) for f, n in now.items() if n > base.get(f, 0)}
    assert not grew, (
        f"new direct time/os.environ reads (was, now): {grew}. Take a Clock or Env "
        "(ddflow/adapters/clock.py, env.py) instead; baselines only shrink."
    )


def test_the_baseline_is_tight_so_a_migration_must_lower_it():
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    now = measure()
    loose = {f: (n, now.get(f, 0)) for f, n in base.items() if now.get(f, 0) < n}
    assert not loose, (
        f"fewer direct reads than the baseline (baseline, now): {loose}. Lower them in "
        "tests/ratchets/clock_env.json (python -m tests.test_adapters_clock_env)."
    )


if __name__ == "__main__":
    import sys

    if not BASELINE.exists() and "--init" not in sys.argv:
        raise SystemExit("no baseline: refusing to invent one; pass --init to create it first time")
    old = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    new = measure()
    up = {f: (old.get(f, 0), n) for f, n in new.items() if n > old.get(f, 0) and BASELINE.exists()}
    if up:
        raise SystemExit(f"refusing to RAISE the baseline (was, now): {up}")
    BASELINE.parent.mkdir(exist_ok=True)
    BASELINE.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n", encoding="utf-8")
