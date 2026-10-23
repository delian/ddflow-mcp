"""The most rows a list, a search and a session list give (`limit 0` asks for it).

One place, read by the engines (`services.viewers`, `search`, `session_view`) and by the
MCP surface that words it in a tool description and a truncation note, which may not import
the engines (B-list-cap-hardcoded-and-search-note)."""

from __future__ import annotations

LIST_MAX_LIMIT = 1000
SEARCH_MAX_LIMIT = 200
SESSION_MAX_LIMIT = 1000
