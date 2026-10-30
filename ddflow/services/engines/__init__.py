"""The closed set of descriptor engines E1..E8 (research driver-architecture 4.2).

A descriptor names an engine and gives it data; adding an agent whose difference is one of
these needs no Python. Each engine validates its data on load (``EngineError`` names the
field) and treats every runtime input as hostile (bounded, no shell, no network).
"""

from __future__ import annotations

from . import (
    emit,
    fieldmap,
    limit_detect,
    mcp_shape,
    probe,
    render_argv,
    stream_map,
    transcript_map,
)
from .safety import DEFAULT, EngineError, Limits, safe_regex

#: name -> module; this tuple IS the closed set
ENGINES = {
    "fieldmap": fieldmap,
    "emit": emit,
    "mcp_shape": mcp_shape,
    "render_argv": render_argv,
    "stream_map": stream_map,
    "limit_detect": limit_detect,
    "transcript_map": transcript_map,
    "probe": probe,
}

#: The functions a caller (the lane drivers) uses per engine: the stable surface, pinned by a test.
ENTRY_POINTS = {
    "fieldmap": ("from_table", "extract"),
    "emit": ("variant_for", "render"),
    "mcp_shape": ("container_path", "entry", "render_json"),
    "render_argv": ("render",),
    "stream_map": ("parse_rules", "map_line", "map_stream"),
    "limit_detect": ("parse", "detect"),
    "transcript_map": ("parse", "resolve_path", "map_lines"),
    "probe": ("parse", "run"),
}

__all__ = ["DEFAULT", "ENGINES", "ENTRY_POINTS", "EngineError", "Limits", "safe_regex", "safety"]
from . import safety  # noqa: E402
