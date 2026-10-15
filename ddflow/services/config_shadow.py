"""After a `config KEY VALUE` write: which layer actually wins (bug B65bbc8083c).

A write into the committed layer is silently inert when `.ddflow/local/config.toml` (or an
environment variable) sets the same key: the file changed, the effective value did not.
`shadow_report` reloads the layered config and says what is effective and where it comes
from, and, when the layer just written lost, the command that would set the winner.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config

_WINNER = {"local": "the machine-local layer (.ddflow/local/config.toml)"}


def shadow_report(repo: Path, key: str, layer: str) -> dict:
    """`effective`, `effective_source`, `shadowed` and a printable `shadow_note`."""
    try:
        cfg = Config.load(repo)
        sec, _, knob = key.partition(".")
        value = getattr(getattr(cfg, sec), knob)
        source = cfg.sources.get(key, "")
    except Exception:  # an unknown or dynamic key (gate.*, reviewers): nothing to report
        return {"effective": None, "effective_source": "", "shadowed": False, "shadow_note": ""}
    shadowed = bool(source) and source not in (layer, "default")
    note = ""
    if shadowed:
        who = _WINNER.get(source, f"the {source} setting")
        fix = f"ddflow config --local {key} VALUE" if source == "local" else "unset it there"
        note = (
            f"\n  warning: shadowed -- {who} wins; the effective value of {key} is "
            f"{value!r}, not what was just written.\n  To change the winning layer: {fix}"
        )
    return {
        "effective": value,
        "effective_source": source,
        "shadowed": shadowed,
        "shadow_note": note,
    }
