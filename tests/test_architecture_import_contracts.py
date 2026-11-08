"""The `.importlinter` contract guards (split from tests/test_architecture_guards.py, r-small-files).

Every contract is present, each one is kept, no allowlist entry is stale, and each optional
extra is confined to one adapter. Helpers: architecture_guards_support.py.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import architecture_guards_support as support
import pytest
from architecture_guards_support import (
    ROOT,
    _allowlist,
    _contracts,
    _importlinter,
    _two_homes,
)

#: What this checks, read by `ddflow tests --item`: see test_architecture_guards.py.
GOVERNS = ("ddflow/**", ".importlinter", "tests/guard_baselines/**", "pyproject.toml")

#: The contracts `.importlinter` must hold: a missing section is a guard removed, and
#: import-linter itself reports success for a file with no contracts at all.
CONTRACTS = frozenset(
    {
        "layers",
        "surfaces-through-api",
        "subprocess-home",
        "fsio-home",
        "digest-home",
        "http-home",
        "toml-home",
        "extras-one-adapter",
    }
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
        "tests/guard_baselines/importlinter-<contract>[/<source module>].toml; never add one for new code.\n"
        + output
    )
    # Exit 0 already means "every contract it loaded was kept"; this says it loaded them all.
    assert f"Contracts: {len(contracts)} kept, 0 broken." in output, output


_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def _dist_name(req: str) -> str:
    """The PEP 508 distribution name an optional-dependency requirement declares: the
    leading name component (``rapidfuzz>=3.14.6`` -> ``rapidfuzz``, ``name@url`` ->
    ``name``). The PEP 508 name alphabet stops at any separator, not only the version
    operators, so an entry written ``rapidfuzz(>=3.14.6)`` still reads ``rapidfuzz``."""
    match = _NAME.match(req.strip())
    assert match, f"no distribution name in requirement {req!r}"
    return match.group(0)


#: Distribution name (as pyproject declares it) -> the top-level import name its package
#: provides. Explicit, never derived: no mechanical rule maps a distribution name to its
#: import name (``python-dateutil`` imports as ``dateutil``, ``PyYAML`` as ``yaml``).
_EXTRA_IMPORTS: dict[str, str] = {
    "rapidfuzz": "rapidfuzz",
    "watchfiles": "watchfiles",
    "model2vec": "model2vec",
    "sqlite-vec": "sqlite_vec",
    "mcp": "mcp",
}


def _unconfined(extras: dict[str, list[str]]) -> list[str]:
    """Why the declared extras are not each confined to one module: a package whose
    distribution name has no ``_EXTRA_IMPORTS`` entry, or whose import name is not in the
    one-adapter contract's ``forbidden_modules``, or a ``_EXTRA_IMPORTS`` entry that no
    extra still declares. Read from pyproject's package list, never a hard-coded module
    list, so a package added under an existing extra is checked too."""
    named = set(_contracts()["extras-one-adapter"]["forbidden_modules"].split())
    declared = {_dist_name(req) for reqs in extras.values() for req in reqs}
    problems: list[str] = []
    for reqs in extras.values():
        for req in reqs:
            dist = _dist_name(req)
            imp = _EXTRA_IMPORTS.get(dist)
            if imp is None:
                problems.append(f"{dist}: no _EXTRA_IMPORTS entry for its import name")
            elif imp not in named:
                problems.append(f"{imp}: not in forbidden_modules")
    stale = set(_EXTRA_IMPORTS) - declared
    if stale:
        problems.append(f"_EXTRA_IMPORTS entries not declared in pyproject: {sorted(stale)}")
    return problems


def test_every_extra_has_a_contract() -> None:
    """Each package an optional extra declares is named in the one-adapter contract, so an
    extra -- or a package added under an existing extra -- cannot exist without its import
    being confined to one module."""
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"].get(
        "optional-dependencies", {}
    )
    problems = _unconfined(extras)
    assert not problems, f"extras whose packages are unconfined: {problems}"


def test_package_added_under_existing_extra_is_checked(monkeypatch) -> None:
    """Regression for B-arch-guard-extras-hardcoded: a package added under an existing
    extra is reported both when its import name is unmapped and when it is mapped but its
    import name is not in ``forbidden_modules``. The old guard iterated a hard-coded module
    list, never pyproject's packages, so it missed such a package."""
    assert "acme-noval-pkg: no _EXTRA_IMPORTS entry for its import name" in _unconfined(
        {"rag": ["model2vec>=0.9.0", "acme-noval-pkg>=1.0"]}
    )
    monkeypatch.setitem(_EXTRA_IMPORTS, "acme-noval-pkg", "acme_noval")
    assert "acme_noval: not in forbidden_modules" in _unconfined({"rag": ["acme-noval-pkg>=1.0"]})


def test_dist_name_reads_the_pep508_name_component() -> None:
    """``_dist_name`` reads the leading PEP 508 name, however the requirement continues: a
    plain version, a parenthesized version, or a direct reference."""
    assert _dist_name("rapidfuzz>=3.14.6") == "rapidfuzz"
    assert _dist_name("rapidfuzz(>=3.14.6)") == "rapidfuzz"
    assert _dist_name("mcp@https://example.com/mcp.whl") == "mcp"
    assert _dist_name("sqlite-vec>=0.1.9") == "sqlite-vec"


def test_a_split_allowlist_joins_one_file_per_source_module(tmp_path, monkeypatch) -> None:
    """The loader reads `importlinter-<contract>/<module>.toml`, joins them, and refuses
    an entry filed under another module's name; a missing directory is an empty list."""
    monkeypatch.setattr(support, "ROOT", tmp_path)
    monkeypatch.setattr(support, "BASELINES", tmp_path / "guard_baselines")
    directory = tmp_path / "guard_baselines" / "importlinter-fsio-home"
    assert _allowlist("fsio-home") == []
    directory.mkdir(parents=True)
    (directory / "ddflow.a.toml").write_text('ignore_imports = ["ddflow.a -> tempfile"]\n')
    (directory / "ddflow.b.toml").write_text('ignore_imports = ["ddflow.b -> fcntl"]\n')
    assert _allowlist("fsio-home") == ["ddflow.a -> tempfile", "ddflow.b -> fcntl"]
    (directory / "ddflow.b.toml").write_text('ignore_imports = ["ddflow.c -> fcntl"]\n')
    with pytest.raises(AssertionError, match="other modules"):
        _allowlist("fsio-home")


def test_graph_internal_names_every_contract() -> None:
    """``graph-internal.md``'s Import-linter row names exactly the ``.importlinter``
    contracts in ``CONTRACTS`` -- no missing name, no extra, and a matching count."""
    text = (ROOT / "docs/ddflow/research/graph-internal.md").read_text("utf-8")
    row = next(line for line in text.splitlines() if "Import-linter contracts" in line)
    match = re.search(r"\((\d+) contracts: ([^)]+)\)", row)
    assert match is not None, f"Import-linter row has no '(N contracts: ...)' list: {row!r}"
    count, names = int(match.group(1)), match.group(2)
    listed = [name.strip() for name in names.split(",") if name.strip()]
    assert count == len(listed) == len(set(listed)), (
        f"Import-linter row: {count} contracts; listed {listed}"
    )
    assert set(listed) == set(CONTRACTS), (
        f"Import-linter row: {count} contracts {sorted(listed)}; CONTRACTS is {sorted(CONTRACTS)}"
    )
