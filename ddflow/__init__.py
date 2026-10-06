"""ddflow — a portable, agent-agnostic work-queue kernel for AI coding agents.

The whole system rests on one decision: **the append-only event log is the source of
truth**, and every other artefact (the SQLite index, the markdown boards, the lessons
search index, the session replay bundle) is a *derived projection* that can be thrown
away and re-folded from the log at any time.

Read `docs/ARCHITECTURE.md` for why, and `docs/RESEARCH.md` for the probes that
decided each choice.
"""

#: THE one place the version is declared, and the only line a bump edits (`scripts/bump.sh`
#: does it, then renders server.json). pyproject.toml reads this file (hatch `dynamic`
#: version), `surfaces/mcp.py` builds SERVER_INFO from it, and server.json is generated
#: from server.template.json. Keep it a plain `__version__ = "X.Y.Z"` literal: hatch and
#: scripts/render_server_json.py both read it as text.
__version__ = "0.1.18"

#: The on-disk format level (docs/ddflow/compatibility.md): raised by any change an older
#: ddflow cannot read or write safely, never by an additive one. Separate from `__version__`,
#: which changes with every release whether or not anything on disk did.
FORMAT_LEVEL = 1
