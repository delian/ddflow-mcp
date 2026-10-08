"""Where each config layer lives: the one definition.

The loader (`Config.load`), every overlay reader (`tomlcfg.config_paths`) and every writer
(`services/configwrite`) ask `layer_path`, so a layer cannot be in one place for the reader
and another for the writer (bug B-reviewers-write-committed). It sits in `config_sections`
because `config` imports nothing else and every layer above it reads the layers.
"""

from __future__ import annotations

from pathlib import Path

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
