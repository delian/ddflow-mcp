"""A new port edits NO shared file (ADP-modular-registry, r-small-files).

Four files used to be appended to by every adapter task (`ports.py`, `fakes/__init__.py`,
`tests/test_ports_contract.py`, `docs/ARCHITECTURE.md`), so one adapter task held them all and
the rest waited on its lease. Now a family is its own files: `ddflow/adapters/<family>.py`
(the port, marked `@port`), `ddflow/adapters/fakes/<family>.py` (the fake, marked `@fake`),
`tests/test_adapter_<family>.py` (the conformance cases) and `docs/ddflow/adapters/<family>.md`.
Discovery is by scanning those packages, sorted by module name, so none of them is listed.
This test is the dry run: a throwaway family in a temporary package is found with no edit.
"""

from __future__ import annotations

import importlib
import re
import sys
import textwrap
from pathlib import Path

import pytest

from ddflow.adapters import fakes as F
from ddflow.adapters import ports as P

#: A new family is a new module under `ddflow/adapters/` and is found by scanning that
#: package at run time (`ports.discover`), so no import or name change reaches this test;
#: it declares it governs the adapters tree instead (B-select-misses-adapter-discovery-tests).
GOVERNS = ("ddflow/adapters/**",)

ROOT = Path(__file__).resolve().parents[1]
#: The four files that used to take an edit per port.
FORMER_SHARED = (
    "ddflow/adapters/ports.py",
    "ddflow/adapters/fakes/__init__.py",
    "tests/test_ports_contract.py",
    "docs/ARCHITECTURE.md",
)
#: Ports that existed before the layout changed and still sit in the shrink-only baseline of
#: `tests/test_ports_contract.py`; that ceiling is the one place their names may remain.
LEGACY = {"Proc", "Git", "Fsio", "Toml", "Forge"}

WIDGET = '''
"""A throwaway family. Home: `ddflow.adapters.widget`."""
from typing import Protocol, runtime_checkable

from ddflow.adapters.ports import port


@port
@runtime_checkable
class Widget(Protocol):
    """Home: `ddflow.adapters.widget`."""

    def spin(self) -> int:
        ...
'''
FAKE_WIDGET = """
from ddflow.adapters.fakes import fake


@fake
class FakeWidget:
    def spin(self) -> int:
        return 1
"""


def _temp_package(root: Path, name: str) -> None:
    pkg = root / name
    (pkg / "fakes").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "fakes" / "__init__.py").write_text("")
    (pkg / "widget.py").write_text(textwrap.dedent(WIDGET))
    (pkg / "fakes" / "widget.py").write_text(textwrap.dedent(FAKE_WIDGET))
    (pkg / "zz_not_a_family.py").write_text("X = 1\n")


def test_a_new_family_is_found_with_no_edit_to_any_shared_file(tmp_path, monkeypatch):
    before = {f: (ROOT / f).read_bytes() for f in FORMER_SHARED}
    _temp_package(tmp_path, "adp_fixture_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        pkg = importlib.import_module("adp_fixture_pkg")
        fakes = importlib.import_module("adp_fixture_pkg.fakes")
        assert sorted(P.discover(pkg)) == ["Widget"]
        found = F.collect(fakes)
        assert sorted(found) == ["Widget"]
        assert found["Widget"]().spin() == 1
        assert isinstance(found["Widget"](), P.discover(pkg)["Widget"])
    finally:
        for name in [m for m in sys.modules if m.startswith("adp_fixture_pkg")]:
            del sys.modules[name]
    assert {f: (ROOT / f).read_bytes() for f in FORMER_SHARED} == before


def test_discovery_order_is_the_sorted_module_names(tmp_path, monkeypatch):
    _temp_package(tmp_path, "adp_fixture_order")
    (tmp_path / "adp_fixture_order" / "alpha.py").write_text(
        textwrap.dedent(WIDGET).replace("Widget", "Alpha")
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        pkg = importlib.import_module("adp_fixture_order")
        assert list(P.discover(pkg)) == ["Alpha", "Widget"]
        assert P.module_names(pkg) == ["alpha", "widget", "zz_not_a_family"]
    finally:
        for name in [m for m in sys.modules if m.startswith("adp_fixture_order")]:
            del sys.modules[name]


def _families_named(names: set[str], text: str) -> list[str]:
    """The names in `text`, each ending a word of an identifier: `Fs` is in `Fs,`, `FakeFs` and
    `FsPort`, never in `Fsio`, which is another family."""
    return sorted(n for n in names if re.search(rf"{re.escape(n)}(?![a-z0-9_])", text))


def test_the_shared_files_name_no_family_that_has_its_own_files():
    """A family named in a shared file is a family that had to edit it."""
    import ddflow.adapters as A

    every = set(P.discover(A)) | set(F.FAKES)
    assert every, "discovery found nothing"
    for rel in FORMER_SHARED:
        text = (ROOT / rel).read_text("utf-8")
        # the baseline ceiling names the legacy ports; in prose "Git" is also just a word
        names = every - LEGACY if rel.endswith((".md", "test_ports_contract.py")) else every
        named = _families_named(names, text)
        assert not named, f"{rel} names {named}: it is a per-family file now, not a shared list"


def test_a_family_named_in_a_shared_file_is_caught_and_a_longer_name_is_not():
    """The guard still bites on the real ceiling file, and only `Fsio` does not count as `Fs`."""
    text = (ROOT / "tests" / "test_ports_contract.py").read_text("utf-8")
    assert "Fsio" in text and _families_named({"Fs"}, text) == []
    for mention in ("Fs", "`Fs`", "from .fs import Fs\n", "FakeFs", "LocalFs", "FsPort", "(Fs)"):
        assert _families_named({"Fs"}, text + mention) == ["Fs"], mention


def test_every_family_has_its_four_per_family_homes():
    import ddflow.adapters as A

    for name, cls in P.discover(A).items():
        family = cls.__module__.rsplit(".", 1)[1]
        assert (ROOT / "docs" / "ddflow" / "adapters" / f"{family}.md").is_file(), (
            f"{name}: write docs/ddflow/adapters/{family}.md (what it is, home, how to fake it)"
        )
        have_fake = name in F.FAKES
        baseline = ROOT / "tests" / "guard_baselines" / "ports_without_fake" / f"{name}.txt"
        assert have_fake != baseline.is_file(), (
            f"{name}: exactly one of a fake in ddflow/adapters/fakes/ or a baseline file {baseline}"
        )
        if have_fake:
            assert (ROOT / "tests" / f"test_adapter_{family}.py").is_file(), (
                f"{name}: its conformance cases belong in tests/test_adapter_{family}.py"
            )


def test_two_families_with_one_port_name_are_refused_not_overwritten(tmp_path, monkeypatch):
    _temp_package(tmp_path, "adp_fixture_dup")
    (tmp_path / "adp_fixture_dup" / "other.py").write_text(textwrap.dedent(WIDGET))
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        pkg = importlib.import_module("adp_fixture_dup")
        with pytest.raises(ValueError, match="two ports named Widget"):
            P.discover(pkg)
    finally:
        for name in [m for m in sys.modules if m.startswith("adp_fixture_dup")]:
            del sys.modules[name]


def test_an_alias_of_a_port_or_a_fake_is_not_a_second_one(tmp_path, monkeypatch):
    _temp_package(tmp_path, "adp_fixture_alias")
    pkg = tmp_path / "adp_fixture_alias"
    (pkg / "widget.py").write_text(textwrap.dedent(WIDGET) + "\nAlias = Widget\n")
    (pkg / "fakes" / "widget.py").write_text(
        textwrap.dedent(FAKE_WIDGET) + "\nAlias = FakeWidget\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        assert sorted(P.discover(importlib.import_module("adp_fixture_alias"))) == ["Widget"]
        assert sorted(F.collect(importlib.import_module("adp_fixture_alias.fakes"))) == ["Widget"]
    finally:
        for name in [m for m in sys.modules if m.startswith("adp_fixture_alias")]:
            del sys.modules[name]
