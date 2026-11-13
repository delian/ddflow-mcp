"""The event kinds the add-time duplicate check does not cover, and why, for
`tests/test_coherence_coverage.py`. The table is DERIVED from the event-kind declarations
(`exempt` rows in `ddflow/templates/events/<owner>.toml`) -- a new kind's exemption is
declared in its own file, so this module stays a one-liner.
"""

from __future__ import annotations

from ddflow.core import eventkinds as EK

#: Why a kind files no checkable text, for `CLI_VERBS`/`MCP_TOOLS` whose authoring surface
#: is config, not the log: the same words those kinds carry live in `ddflow config`.
_CONFIG = "who changed a setting or approved a tool; the setting itself lives in config"

#: Every other kind, and why it is not duplicate-checked. The gate RECORD kinds of an item
#: (`GATE_RECORDS`, state) are exempt by name in `test_coherence_coverage.py`.
EXEMPT: dict[str, str] = dict(EK.exempt(EK.EVENT_KINDS.values_list()))
