"""Ratchet (D-unify 4): no NEW direct clock or environment reads in core and services.

`time.time()`, `time.monotonic()`, `os.environ` and `os.getenv` are counted with `ast` (so a
docstring or comment never counts) per file under ddflow/core and ddflow/services. The
baseline `tests/ratchets/clock_env.json` holds the count each file had when the ports landed;
it only SHRINKS: a file may not read more, and a file that reads fewer must lower its number
here (or drop its entry). Migrate a site by taking a `Clock` / `Env` argument
(ddflow/adapters/clock.py, env.py). Regenerate after a migration with
`uv run python -m tests.test_adapters_clock_env` and review the diff: it must only go down.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "ratchets" / "clock_env.json"
LAYERS = ("ddflow/core", "ddflow/services")


def _tail(node: ast.expr) -> str:
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")


def count_reads(tree: ast.AST) -> int:
    """Direct reads: `time.time()`, `time.monotonic()` (any alias ending in `time`),
    `os.environ` and `os.getenv`."""
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = _tail(node.value)
        if node.attr in ("time", "monotonic") and base.lstrip("_") == "time":
            n += 1
        elif node.attr in ("environ", "getenv") and base == "os":
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


def test_the_counter_sees_calls_and_attributes_but_not_text():
    src = 'import os, time\n"""time.time() os.environ"""\n# os.getenv\nx = time.time() + _t.time.monotonic()\ny = os.environ.get("A") or os.getenv("B")\n'
    assert count_reads(ast.parse(src)) == 4


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
    BASELINE.parent.mkdir(exist_ok=True)
    BASELINE.write_text(json.dumps(measure(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
