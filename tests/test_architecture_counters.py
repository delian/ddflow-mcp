"""Tests of the ratchet counters and baseline loaders themselves (split from
tests/test_architecture_guards.py, r-small-files): a ratchet that miscounts holds nothing.
Helpers: architecture_guards_support.py.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

import architecture_guards_support as support
import pytest
from architecture_guards_support import (
    BASELINES,
    COUNTERS,
    PER_MODULE,
    ROOT,
    SPLIT_CONTRACTS,
    _allowlist,
    _baseline,
    _contracts,
    _dotted,
    _kept_key,
    _kept_unreferenced,
    _measure,
    _module_baselines,
    _modules,
    _parse_duplicates,
    _ratchet_problems,
    _Sites,
    _two_homes,
    _unreferenced,
)

#: What this checks, read by `ddflow tests --item`: see test_architecture_guards.py.
GOVERNS = ("ddflow/**", ".importlinter", "tests/guard_baselines/**")


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
    yield ("from pathlib import Path\nPath('a').replace('b')\n", "os_replace", 1)
    yield ("from pathlib import Path\nPath('a').replace('b').replace('c')\n", "os_replace", 2)
    yield ("from pathlib import Path\ntarget = Path('x')\ntarget.replace('y')\n", "os_replace", 1)
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
        f"importlinter-{c}.toml" for c in contracts if c not in SPLIT_CONTRACTS
    }
    split = {f"importlinter-{c}" for c in contracts if c in SPLIT_CONTRACTS}
    # `ports_without_fake` is a per-port directory checked by tests/test_ports_contract.py
    # (a sibling guard), not one of these counters; it lives in the same `guard_baselines`.
    other_guards = {"ports_without_fake"}
    present = {p.name for p in BASELINES.iterdir() if not p.name.startswith(".")}
    assert present - set(PER_MODULE) - split - other_guards == expected, (
        f"missing: {sorted(expected - present)}; "
        f"read by no guard: {sorted(present - expected - set(PER_MODULE) - split - other_guards)}"
    )
    modules = {_dotted(p) for p in _modules()}
    for kind in COUNTERS:
        if kind in PER_MODULE:
            baselines = _module_baselines(kind)
            stray = sorted(set(baselines) - modules)
            assert not stray, f"{kind}: a baseline file for a module that does not exist: {stray}"
            # A per-module counter keeps its directory exactly while some module still
            # has sites (a module with no sites has no file, and git tracks no empty
            # directory): deleting the directory would leave every site unbaselined, so
            # require it here for each counter rather than subtracting every PER_MODULE
            # name above and reading their absence as "nothing to check".
            sites = _measure(kind)
            assert bool(sites) == bool(baselines), (
                f"{kind} has {len(sites)} sites but {len(baselines)} baselined module(s); "
                "a per-module counter's directory exists exactly while some module still "
                "has sites"
            )
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
    monkeypatch.setattr(support, "ROOT", tmp_path)
    monkeypatch.setattr(support, "BASELINES", tmp_path / "guard_baselines")
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
    monkeypatch.setattr(support, "ROOT", tmp_path)
    monkeypatch.setattr(support, "BASELINES", tmp_path / "guard_baselines")
    (tmp_path / "guard_baselines").mkdir()
    assert _module_baselines("fcntl") == {}
