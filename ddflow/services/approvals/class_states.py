"""The canonical state digests of the hook/lease-blocked classes (config.set, pins.regenerate),
as state providers for `ddflow approvals request` (registered with the other providers).

The door at each site computes the same digest when the agent re-runs the command, so an
approval filed by hand and one filed at the door agree byte for byte."""

from __future__ import annotations

import json

from ...core.digest import content_digest

#: The fields the `config.set` approval binds: what is set, to what, in which layer.
CONFIG_SET_FIELDS = ("key", "value", "layer")


def config_set_state(key: str, value: str, layer: str) -> str:
    """The digest a `config.set` approval binds: `ddflow config <key> <value> [--local]`."""
    record = {k: str(v) for k, v in zip(CONFIG_SET_FIELDS, (key, value, layer), strict=False)}
    return content_digest(json.dumps(record, sort_keys=True, separators=(",", ":")))


def config_set_provider(st, item: str, target: str, args: dict[str, str]) -> str:
    """`ddflow approvals request config.set <key> <value>`: it cannot mean the `--local`
    layer (that write is filed at the door), so the layer is always the file layer."""
    return config_set_state(item, target, "file")


def pins_state(before: dict[str, str | None]) -> str:
    """The digest a `pins.regenerate` approval binds: the pin files as recorded before."""
    flat = {k: (v or "") for k, v in sorted(before.items())}
    return content_digest(json.dumps(flat, sort_keys=True, separators=(",", ":")))
