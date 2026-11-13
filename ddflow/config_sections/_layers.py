"""Where each config layer lives: the one definition.

The loader (`Config.load`), every overlay reader (`tomlcfg.config_paths`) and every writer
(`services/configwrite`) ask `layer_path`, so a layer cannot be in one place for the reader
and another for the writer (bug B-reviewers-write-committed). It sits in `config_sections`
because `config` imports nothing else and every layer above it reads the layers.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ..adapters.toml import TomlLibIo

#: The git-ignored machine-local directory (decision D-no-own-services-local-dir).
LOCAL_DIR = Path(".ddflow") / "local"

#: The two config layers, in precedence order (later wins): the committed `.ddflow/` files
#: ("file", the name `Config.sources` gives it) and the git-ignored `.ddflow/local/` ones.
LAYERS = ("file", "local")

#: The packaged defaults, the lowest layer (D-config-layer-order): shipped with the code, so
#: no writer targets it and it is not in `LAYERS`. `views/default_toml.py` generates it.
PACKAGED_DEFAULTS = Path(__file__).resolve().parent.parent / "templates" / "default.toml"

#: The same defaults as a drop-in directory, one generated file per section (CFG-defaults-d-loader).
#: The packaged layer reads this when it exists, else `PACKAGED_DEFAULTS`.
PACKAGED_DIR = Path(__file__).resolve().parent.parent / "templates" / "default.d"


def layer_path(root: Path, layer: str = "file", own: str = "config.toml") -> Path:
    """The file ``layer`` keeps ``own`` in: `<root>/.ddflow/<own>` for the committed layer,
    `<root>/.ddflow/local/<own>` for the machine-local one. Raises `ValueError` for another
    layer name."""
    if layer not in LAYERS:
        raise ValueError(f"unknown config layer {layer!r}; one of {', '.join(LAYERS)}")
    return Path(root) / (LOCAL_DIR if layer == "local" else Path(".ddflow")) / own


class ConfigConflict(Exception):
    """A config layer file holds git merge-conflict markers (B6b8d1f7a0f): refused (exit 3)
    with the file named, where it used to die as a raw `TOMLDecodeError`."""

    exit_code = 3  # REFUSED (`core.outcome.exit_for`)

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        parts = self.path.parts
        ddflow = len(parts) - 1 - parts[::-1].index(".ddflow") if ".ddflow" in parts else -1
        rel = "/".join(parts[ddflow:]) if ddflow >= 0 else str(path)
        super().__init__(f"resolve the merge conflict in {rel} (git status)")


#: Set by the CLI for the commands that still run over a conflicted layer (`doctor`, `help`):
#: such a file then reads as empty instead of refusing, and `doctor` reports it itself.
_tolerate: ContextVar[bool] = ContextVar("ddflow_tolerate_config_conflict", default=False)


@contextlib.contextmanager
def tolerating_conflicts(on: bool = True) -> Iterator[None]:
    """Within the block, `read_toml` reads a conflict-marked file as empty (when ``on``)."""
    token = _tolerate.set(on or _tolerate.get())
    try:
        yield
    finally:
        _tolerate.reset(token)


_OURS = re.compile(r"^<<<<<<< ", re.MULTILINE)
_SEP = re.compile(r"^=======\r?$", re.MULTILINE)
_THEIRS = re.compile(r"^>>>>>>> ", re.MULTILINE)


def has_conflict_markers(text: str) -> bool:
    """Whether ``text`` holds a git conflict: the opening, separator and closing lines all
    present (one marker-like line alone, e.g. quoted in prose, is not a conflict)."""
    return bool(_OURS.search(text) and _SEP.search(text) and _THEIRS.search(text))


def read_toml(path: Path) -> dict[str, Any]:
    """The TOML table in ``path``. Raises `ConfigConflict` when it holds merge-conflict
    markers (even where they sit inside a string and the text still parses), else whatever
    `TomlIo.loads` raises for a file that is merely malformed."""
    text = Path(path).read_text("utf-8")
    if has_conflict_markers(text):
        if _tolerate.get():
            return {}
        raise ConfigConflict(path)
    return TomlLibIo().loads(text)


class DropinError(ValueError):
    """A drop-in directory cannot be read as one table: a file that does not parse (named,
    with the line the TOML parser gives) or a table two files both define."""


def _defines(table: dict[str, Any]) -> bool:
    """Whether ``table`` holds a value of its own, so it is DEFINED (not a bare container of
    sub-tables such as `[gate]` over `[gate.a]` and `[gate.b]`)."""
    return any(not isinstance(v, dict) for v in table.values())


def _merge_into(
    into: dict[str, Any],
    new: dict[str, Any],
    owner: dict[str, str],
    file: str,
    path: str,
    same_table: str,
) -> None:
    for key, val in new.items():
        here = f"{path}.{key}" if path else key
        if key not in into:
            into[key] = val
            _own(owner, here, val, file)
        elif isinstance(val, dict) and isinstance(into[key], dict):
            if same_table == "error" and _defines(val) and _defines(into[key]):
                raise DropinError(
                    f"table [{here}] is defined in both {owner.get(here, '?')} and {file}"
                )
            _merge_into(into[key], val, owner, file, here, same_table)
        elif same_table == "merge":
            into[key] = val
        else:
            raise DropinError(f"key {here} is set in both {owner.get(here, '?')} and {file}")


def _own(owner: dict[str, str], path: str, val: Any, file: str) -> None:
    owner[path] = file
    if isinstance(val, dict):
        for k, v in val.items():
            _own(owner, f"{path}.{k}", v, file)


def dropin_files(directory: Path) -> list[Path]:
    """The regular `*.toml` files of ``directory`` in name order; `[]` when it is not there
    (a directory or dangling link named `x.toml` is no drop-in)."""
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.toml") if p.is_file())


def read_dropin_dir(directory: Path, *, same_table: str = "error") -> dict[str, Any]:
    """The merged tables of every `*.toml` in ``directory``, read in name order (the systemd
    `.d` model: the directory is the unit of ownership, the name orders it). A directory that
    is not there reads as empty.

    ``same_table="error"`` (the shipped layer): a table two files both define raises
    `DropinError` naming both; sub-tables may be spread over files (a table's own values in
    one file, its `[table.sub]` in another).
    ``"merge"`` (a project's `conf.d`): the later file wins key by key. A file that does not
    parse raises `DropinError` naming file and line; one holding merge-conflict markers raises
    `ConfigConflict`, as `read_toml` does.
    """
    if same_table not in ("error", "merge"):
        raise ValueError(f"same_table is 'error' or 'merge', not {same_table!r}")
    merged: dict[str, Any] = {}
    owner: dict[str, str] = {}
    for path in dropin_files(directory):
        try:
            table = read_toml(path)
        except ValueError as exc:  # TOMLDecodeError says "(at line N, column M)"
            raise DropinError(f"{path}: {exc}") from exc
        _merge_into(merged, table, owner, path.name, "", same_table)
    return merged
