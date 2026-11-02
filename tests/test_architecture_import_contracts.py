"""The `.importlinter` contract guards (split from tests/test_architecture_guards.py, r-small-files).

Every contract is present, each one is kept, no allowlist entry is stale, and each optional
extra is confined to one adapter. Helpers: architecture_guards_support.py.
"""

from __future__ import annotations

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
