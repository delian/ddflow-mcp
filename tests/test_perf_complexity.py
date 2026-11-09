"""The growth indicators (PM-complexity), on a synthetic import graph and a seeded log."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services.metrics.facts import collect
from ddflow.services.perf import complexity as CX


def write(package: Path, rel: str, body: str) -> None:
    py = package / rel
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(body, "utf-8")


def synthetic_package(tmp_path: Path) -> Path:
    package = tmp_path / "pkg"
    write(package, "a.py", "import b\nimport c\n")
    write(package, "b.py", "import c\n")
    write(package, "c.py", "x = 1\n")
    return package


def facts_of(log: Log):
    state = fold(list(log.events), strict=False)
    return collect(log.events, state, now=T0 + 10_000, cfg=Config())


def seeded() -> Log:
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    for tid, globs in (
        ("A", "src/x.py,tests/test_x.py"),
        ("B", "src/y.py,tests/test_x.py"),
        ("C", "src/x.py,tests/test_y.py"),
    ):
        log.add("task.added", tid, "lead", T0 - 1000, title=tid, body="", parent="P1")
        log.claim("a1", tid, T0, globs)
        log.add(
            "gate.passed",
            tid,
            "a1",
            T0 + 100,
            gate="rubber_duck",
            by="r1",
            evidence={"changed_lines": 10},
        )
        log.complete("a1", tid, T0 + 200)
        log.release("a1", tid, T0 + 201)
    return log


def test_import_edges_fan_in_and_depth_on_a_synthetic_graph(tmp_path):
    edges = CX.import_edges(synthetic_package(tmp_path))
    assert edges["a"] == {"b", "c"}
    assert CX.fan_in(edges)["c"] == 2  # a and b import c
    depths = CX.import_depth(edges)
    assert depths["a"] == 2  # a -> b -> c


def test_a_parse_error_misses_the_edge_not_the_module(tmp_path):
    package = tmp_path / "pkg"
    write(package, "ok.py", "import m\n")
    write(package, "bad.py", "def broken(:\n")  # SyntaxError: skipped
    edges = CX.import_edges(package)
    assert "ok" in edges and "bad" not in edges


def test_touched_splits_tests_from_source():
    rows = CX.touched(facts_of(seeded()))
    assert {r["item"]: (r["files"], r["tests"]) for r in rows} == {
        "A": (2, 1),
        "B": (2, 1),
        "C": (2, 1),
    }


def test_co_change_finds_glob_pairs_touched_together():
    pairs = CX.co_change(facts_of(seeded()))
    got = {(p["a"], p["b"]): p["n"] for p in pairs}
    # src/x.py co-changes with tests/test_x.py on A; with tests/test_y.py on C -- each once,
    # so the only pair reaching n>=2 needs the same two globs on two items. Add a 4th item:
    log = seeded()
    log.add("task.added", "D", "lead", T0 - 1000, title="D", body="", parent="P1")
    log.claim("a2", "D", T0 + 500, "src/x.py,tests/test_x.py")
    log.complete("a2", "D", T0 + 600)
    log.release("a2", "D", T0 + 601)
    pairs = CX.co_change(facts_of(log))
    got = {(p["a"], p["b"]): p["n"] for p in pairs}
    assert got[("src/x.py", "tests/test_x.py")] == 2  # A and D both touch the pair


def test_tests_per_changed_line_is_a_ratio_not_a_count():
    r = CX.tests_per_changed_line(facts_of(seeded()))
    assert r["test_files"] == 3 and r["source_files"] == 3
    assert r["tests_per_file"] == 1.0


def test_build_names_the_files_over_time_as_not_measured(tmp_path):
    out = CX.build(facts_of(seeded()), synthetic_package(tmp_path))
    assert "files_over_time" in out["not_measured"]
    assert out["tests_per_changed_line"]["tests_per_file"] == 1.0
