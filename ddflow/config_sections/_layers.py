"""Where each config layer lives: the one definition.

The loader (`Config.load`), every overlay reader (`tomlcfg.config_paths`) and every writer
(`services/configwrite`) ask `layer_path`, so a layer cannot be in one place for the reader
and another for the writer (bug B-reviewers-write-committed). It sits in `config_sections`
because `config` imports nothing else and every layer above it reads the layers.
"""

from __future__ import annotations

import contextlib
import re
import tomllib
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

#: The git-ignored machine-local directory (decision D-no-own-services-local-dir).
LOCAL_DIR = Path(".ddflow") / "local"

#: The two config layers, in precedence order (later wins): the committed `.ddflow/` files
#: ("file", the name `Config.sources` gives it) and the git-ignored `.ddflow/local/` ones.
LAYERS = ("file", "local")


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
        rel = (
            ".ddflow" + str(self.path).partition("/.ddflow")[2]
            if "/.ddflow" in str(path)
            else str(path)
        )
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


_MARKER = re.compile(r"^(<<<<<<< |>>>>>>> )", re.MULTILINE)


def read_toml(path: Path) -> dict[str, Any]:
    """The TOML table in ``path``. Raises `ConfigConflict` when it holds merge-conflict
    markers, else whatever `tomllib` raises for a file that is merely malformed."""
    text = Path(path).read_text("utf-8")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        if _MARKER.search(text):
            if _tolerate.get():
                return {}
            raise ConfigConflict(path) from None
        raise
