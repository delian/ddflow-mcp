"""Fast id completion: sqlite index, import-light, never an error. Synthetic fixtures."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ddflow import completion_fast as CF  # noqa: E402
from ddflow.services import completion_ids as CI  # noqa: E402


def _index(d: Path, name="index.db-aaaaaaaa", mtime=None) -> Path:
    (d / ".ddflow").mkdir(parents=True, exist_ok=True)
    p = d / ".ddflow" / name
    con = sqlite3.connect(p)
    con.executescript("""
    create table items(id text, kind text, title text, state text);
    create table bugs(id text, fixed_at text, invalid_at text);
    create table widgets(id text, status text);
    create table notes(note text);
    create virtual table notes_fts using fts5(note);
    """)
    con.executemany(
        "insert into items values(?,?,?,?)",
        [
            ("COMP-a", "task", "t", "open"),
            ("COMP-b", "task", "t", "done"),
            ("P-comp", "phase", "p", "open"),
            ("X_y", "task", "t", "open"),
            ("X%z", "task", "t", "open"),
            ("Xqq", "task", "t", "open"),
        ],
    )
    con.execute("insert into bugs values('B1',null,null)")
    con.execute("insert into bugs values('B2','t',null)")
    con.execute("insert into widgets values('W1','live')")
    con.commit()
    con.close()
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def test_prefix_by_kind_and_state(tmp_path):
    p = str(_index(tmp_path))
    assert CI.complete_ids("COMP", index=p) == ["COMP-a", "COMP-b"]
    assert CI.complete_ids("", "phase", index=p) == ["P-comp"]
    assert CI.complete_ids("COMP", "task", "done", index=p) == ["COMP-b"]
    assert CI.complete_ids("", "bug", "fixed", index=p) == ["B2"]
    assert CI.complete_ids("", "bugs", "open", index=p) == ["B1"]
    assert CI.phases("P", index=p) == ["P-comp"]


def test_new_kind_completes_without_a_list(tmp_path):
    p = str(_index(tmp_path))
    assert CI.complete_ids("W", "widgets", "live", index=p) == ["W1"]
    assert {"widgets", "bugs", "task", "phase"} <= set(CI.kinds(index=p))
    assert "items" not in CI.kinds(index=p)  # its rows are reached by their kind values
    assert "notes" not in CI.kinds(index=p) and "notes_fts" not in CI.kinds(index=p)
    assert {"live", "fixed", "open", "done"} <= set(CI.states(index=p))


def test_newest_index_wins_and_sidecars_ignored(tmp_path):
    old = _index(tmp_path, "index.db-aaaaaaaa", 1_000_000)
    new = _index(tmp_path, "index.db-bbbbbbbb", 2_000_000)
    new_con = sqlite3.connect(new)
    new_con.execute("delete from items where id like 'COMP%'")
    new_con.commit()
    new_con.close()
    os.utime(new, (2_000_000, 2_000_000))
    for side in ("-wal", "-shm", "-lock"):
        s = Path(str(new) + side)
        s.write_text("")
        os.utime(s, (3_000_000, 3_000_000))
    assert CI.newest_index(str(tmp_path / ".ddflow")) == str(new)
    assert CI.find_index(str(tmp_path)) == str(new)
    assert old != new


def test_missing_index_gives_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ddflow").mkdir()
    assert CI.find_index(str(tmp_path)) is None
    assert CI.complete_ids("x", index=str(tmp_path / "nope")) == []
    assert not (tmp_path / "nope").exists()  # read-only open never creates a database
    assert CF.complete(["ids", "x"]) == []
    assert CF.complete([]) == [] and CF.complete(["bogus"]) == []


def test_corrupt_database_gives_nothing(tmp_path):
    p = tmp_path / "index.db-cccccccc"
    p.write_bytes(b"this is not sqlite" * 500)
    assert CI.complete_ids("", index=str(p)) == []
    assert CI.kinds(index=str(p)) == []


def test_locked_database_gives_nothing_fast(tmp_path, monkeypatch):
    monkeypatch.setenv(CI.LOCK_MS_ENV, "10")
    p = _index(tmp_path)
    con = sqlite3.connect(p, isolation_level=None)
    con.execute("pragma journal_mode=delete")
    con.execute("begin exclusive")
    t = time.monotonic()
    assert CI.complete_ids("", index=str(p)) == []
    # a 200 ms wait (the old, ignored-knob behaviour) would fail this; a 10 ms one does not
    assert time.monotonic() - t < 0.1
    con.execute("rollback")
    con.close()


def test_wildcards_and_injection_are_literal(tmp_path):
    p = str(_index(tmp_path))
    assert CI.complete_ids("X_", index=p) == ["X_y"]
    assert CI.complete_ids("X%", index=p) == ["X%z"]
    assert CI.complete_ids("%", index=p) == []  # not a wildcard
    assert CI.complete_ids("\\", index=p) == []
    for evil in ("'; drop table items; --", "' or '1'='1", "a\x00b"):
        assert CI.complete_ids(evil, evil, evil, index=p) == []
    assert CI.complete_ids("COMP", index=p) == ["COMP-a", "COMP-b"]


def test_worktree_falls_back_to_primary_index(tmp_path):
    primary = tmp_path / "primary"
    _index(primary)
    (primary / ".git" / "worktrees" / "w").mkdir(parents=True)
    (primary / ".git" / "worktrees" / "w" / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    (wt / ".ddflow").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {primary}/.git/worktrees/w\n")
    sub = wt / "a" / "b"
    sub.mkdir(parents=True)
    assert CI.find_index(str(sub)) == str(primary / ".ddflow" / "index.db-aaaaaaaa")


def test_entry_prints_lines_and_exits_zero(tmp_path):
    _index(tmp_path)
    r = subprocess.run(
        [sys.executable, "-m", "ddflow.completion_fast", "ids", "--kind", "task", "COMP"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0 and r.stdout.split() == ["COMP-a", "COMP-b"], r.stderr


def _imports(code: str, tmp: Path) -> str:
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=True,
    )
    return r.stdout


_PROBE = (
    "import sys, ddflow.completion_fast as m; "
    "print(' '.join(sorted(k for k in sys.modules if k.startswith('ddflow'))))"
)


def _heavy(mods: set[str]) -> list[str]:
    """The modules that break the import budget: anything of ddflow beyond the two light ones."""
    light = {
        "ddflow",
        "ddflow.completion_fast",
        "ddflow.services",
        "ddflow.services.completion_ids",
    }
    return sorted(m for m in mods if m.startswith("ddflow") and m not in light)


def test_import_budget_no_heavy_modules(tmp_path):
    assert _heavy(set(_imports(_PROBE, tmp_path).split())) == []


def test_planted_slow_import_fails_the_budget(tmp_path):
    """The same check the budget test applies reports a heavy import planted in the path."""
    (tmp_path / "ddflow_slow.py").write_text("import ddflow.api\n")
    code = _PROBE.replace("import sys,", "import sys, ddflow_slow,")
    heavy = _heavy(set(_imports(code, tmp_path).split()))
    assert "ddflow.api" in heavy  # the budget test would fail on this


def test_lookup_latency_budget(tmp_path):
    """Import + lookup inside a fresh interpreter fit the 100 ms budget (startup excluded)."""
    _index(tmp_path)
    # runpy executes the module as `python -m` would, `__main__` block included
    code = (
        "import runpy, sys, time, io; t = time.perf_counter(); sys.argv = ['x', 'ids', 'COMP']; "
        "buf = io.StringIO(); sys.stdout = buf; "
        "\ntry:\n runpy.run_module('ddflow.completion_fast', run_name='__main__')"
        "\nexcept SystemExit:\n pass\n"
        "sys.stdout = sys.__stdout__; print(len(buf.getvalue().split()), time.perf_counter() - t)"
    )
    n, took = _imports(code, tmp_path).split()
    assert n == "2" and float(took) < 0.1


def test_bad_arguments_print_nothing(tmp_path, monkeypatch):
    _index(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert CF.complete(["ids", "COMP"]) == ["COMP-a", "COMP-b"]
    assert CF.complete(["ids", "COMP", "--kind"]) == []
    assert CF.complete(["ids", "COMP", "COMP-b"]) == []
    assert CF.complete(["phases", "--kind", "bug"]) == []
    assert CF.complete(["kinds", "--state", "x"]) == []
    assert CF.complete(["ids", "--kind", "items"]) == []  # a multi-kind table is no kind


def test_irregular_plurals_and_lock_knob(tmp_path, monkeypatch):
    p = tmp_path / "index.db-dddddddd"
    con = sqlite3.connect(p)
    con.executescript(
        "create table memories(id text); create table classes(id text);"
        "insert into memories values('mem-1'); insert into classes values('c1');"
    )
    con.commit()
    con.close()
    assert CI.complete_ids("", "memory", index=str(p)) == ["mem-1"]
    assert CI.complete_ids("", "class", index=str(p)) == ["c1"]
    assert CI.complete_ids("", "memories", index=str(p)) == ["mem-1"]
    monkeypatch.setenv(CI.LOCK_MS_ENV, "7")
    assert CI._lock_ms() == 7
    monkeypatch.setenv(CI.LOCK_MS_ENV, "junk")
    assert CI._lock_ms() == CI.LOCK_MS_DEFAULT


def test_in_process_lookup_meets_the_100ms_budget(tmp_path):
    p = str(_index(tmp_path))
    CI.complete_ids("COMP", index=p)  # warm the page cache
    t = time.perf_counter()
    assert CI.complete_ids("COMP", index=p) == ["COMP-a", "COMP-b"]
    assert time.perf_counter() - t < 0.1


def test_directory_named_like_an_index_is_not_chosen(tmp_path):
    good = _index(tmp_path, "index.db-aaaaaaaa", 1_000_000)
    (tmp_path / ".ddflow" / "index.db-ffffffff").mkdir()
    assert CI.newest_index(str(tmp_path / ".ddflow")) == str(good)
