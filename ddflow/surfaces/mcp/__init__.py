"""An MCP stdio server for ddflow — implemented in the standard library alone.

Why hand-rolled rather than `pip install mcp`: portability is the entire point of this
project. A workflow kernel that only installs where a package index is reachable is not
portable, and the MCP stdio transport is newline-delimited JSON-RPC 2.0 — a few hundred
lines, fully specified, and stable. Taking a dependency to avoid writing them would
trade the property we are optimising for against a convenience we do not need.

The tool surface is deliberately **the same functions the CLI calls**. MCP is a second
door onto one implementation, never a second implementation. That is what keeps an
agent driving ddflow over MCP and an agent driving it over a shell from diverging —
and it is why an agent with neither (a human, a CI job, a `Makefile`) loses nothing.

Notes on protocol handling:

* Which revisions are served and how each shapes a reply -- ``initialize`` for the legacy
  ones, per-request ``_meta`` for ``2026-07-28``, multi round-trip -- is
  ``mcp_protocol``, the one module that knows about revisions. This one routes methods
  to ddflow; neither it nor any tool sees which revision a request came in under.
* Every tool returns text content. Structured results are JSON *inside* that text,
  because ``structuredContent`` support is uneven across clients and a result an agent
  cannot read is worse than a verbose one it can.
* Errors are returned as ``isError: true`` results rather than JSON-RPC errors, which
  is what lets the model see the failure and correct, instead of the transport
  swallowing it.

The engine is now a package: ``_reply`` shapes an `Outcome` into an answer, ``_handlers``
and ``_server`` are the JSON-RPC dispatcher (`Server` inherits ``_Handlers`), ``_offload``
and ``_serve`` run and relay the long tools, and this ``__init__`` re-exports the same
names the single file did, so every import site and ``ddflow.surfaces.mcp.<name>`` keeps
working unchanged.
"""

from __future__ import annotations

# The registry lives in `surfaces/tools/` and the protocol engine in `mcp_protocol`; this
# package routes between them. Every name below is re-exported so imports of
# `ddflow.surfaces.mcp.<name>` keep working.
from .. import mcp_next as _NEXT  # noqa: F401
from .. import mcp_protocol as protocol  # noqa: F401
from .. import mcp_upgrade as _upgrade  # noqa: F401
from .. import projectkinds as PK  # noqa: F401
from .. import registry as _REGISTRY  # noqa: F401
from ..mcp_bound import BOUNDS as BOUNDS
from ..mcp_heartbeat import heartbeat as heartbeat
from ..mcp_protocol import CACHE_SCOPE as CACHE_SCOPE
from ..mcp_protocol import CACHE_TTL_MS as CACHE_TTL_MS
from ..mcp_protocol import INVALID_PARAMS as INVALID_PARAMS
from ..mcp_protocol import META_AGENT as META_AGENT
from ..mcp_protocol import META_CLIENT_CAPS as META_CLIENT_CAPS
from ..mcp_protocol import META_SERVER_INFO as META_SERVER_INFO
from ..mcp_protocol import META_VERSION as META_VERSION
from ..mcp_protocol import MODERN_PROTOCOLS as MODERN_PROTOCOLS
from ..mcp_protocol import SERVER_INFO as SERVER_INFO
from ..mcp_protocol import SUPPORTED_PROTOCOLS as SUPPORTED_PROTOCOLS
from ..mcp_protocol import UNSUPPORTED_PROTOCOL_VERSION as UNSUPPORTED_PROTOCOL_VERSION
from ..mcp_protocol import capabilities as _capabilities  # noqa: F401
from ..mcp_protocol import err as _err  # noqa: F401
from ..mcp_protocol import modern_check as _modern_check  # noqa: F401
from ..mcp_protocol import modernize as _modernize  # noqa: F401
from ..mcp_protocol import ok as _ok  # noqa: F401
from ..tools import ADD_TOOLS as ADD_TOOLS
from ..tools import DEDUPE_PROPERTIES as DEDUPE_PROPERTIES
from ..tools import TOOLS as TOOLS
from ..tools import family_schema as _FAMILIES  # noqa: F401
from ..tools import profiles as _PROFILES  # noqa: F401
from ..tools._common import _AGENT_KEYS as _AGENT_KEYS
from ..tools._common import MCP_WAIT_DEFAULT_S as MCP_WAIT_DEFAULT_S
from ..tools._common import MCP_WAIT_MAX_S as MCP_WAIT_MAX_S
from ..tools._common import _answer as _answer
from ..tools._common import _api as _api
from ..tools._common import _bisect as _bisect
from ..tools._common import _bug_reopen as _bug_reopen
from ..tools._common import _configure_reported as _configure_reported
from ..tools._common import _list_or_none as _list_or_none
from ..tools._common import _opt as _opt
from ..tools._common import _regression_tests as _regression_tests
from ..tools._common import _reopening as _reopening
from ..tools._common import _wait_timeout as _wait_timeout
from ..tools.aliases import alias_for as _alias_for  # noqa: F401
from ..tools.tiers import CORE_TOOLS as CORE_TOOLS
from ..tools.tiers import DEFAULT_TIER as DEFAULT_TIER
from ..tools.tiers import FULL_ONLY_TOOLS as FULL_ONLY_TOOLS
from ..tools.tiers import STANDARD_EXTRA_TOOLS as STANDARD_EXTRA_TOOLS
from ..tools.tiers import TIERS as TIERS
from ..tools.tiers import resolve_output_schemas as resolve_output_schemas
from ..tools.tiers import resolve_tier as resolve_tier
from ..tools.tiers import tier_note as tier_note
from ..tools.tiers import tier_tools as tier_tools
from ._core import (
    _AS_AGENT_SPEC,
    _FIRST_CALL_TRIES,
    _OPTIONAL_KEYS,
    _STALE_CHECK_EVERY_S,
    _STRUCTURED_SINCE,
    ALLOW_OLDER,
    AS_AGENT,
    OFFLOADED,
    RESOURCE_BY_URI,
    RESOURCES,
    Resource,
    _default_agent,
    _identity,
    _meta_agent,
    _modern_identify_note,
    _progress_token,
    _rendered,
    _surf,
    _waves_auto,
)
from ._handlers import _Call
from ._instructions import (
    _FILLERS,
    _VAR_DEFAULTS,
    _fill_pipeline,
    _instruction_vars,
    _instructions,
    _instructions_text,
    _obligation_footer,
    _upgrade_line,
)
from ._offload import (
    MAX_WORKERS,
    _frame_writer,
    _offload,
    _offloaded,
    _worker_main,
    _worker_stderr,
)
from ._reply import (
    _SORT_PROPERTY,
    _bounds,
    _finish_list_read,
    _outcome_result,
    _properties,
    _refusal_body,
    _refusal_said,
    _resolve_call,
    _result_declaration,
    _schema,
    _text,
    _text_with_next,
    _with_next,
    deprecation_note,
)
from ._serve import _reexec_ready, main, serve
from ._server import Server

__all__ = (
    "ALLOW_OLDER",
    "AS_AGENT",
    "MAX_WORKERS",
    "OFFLOADED",
    "RESOURCES",
    "RESOURCE_BY_URI",
    "_AS_AGENT_SPEC",
    "_FILLERS",
    "_FIRST_CALL_TRIES",
    "_OPTIONAL_KEYS",
    "_SORT_PROPERTY",
    "_STALE_CHECK_EVERY_S",
    "_STRUCTURED_SINCE",
    "_VAR_DEFAULTS",
    "Resource",
    "Server",
    "_Call",
    "_bounds",
    "_default_agent",
    "_fill_pipeline",
    "_finish_list_read",
    "_frame_writer",
    "_identity",
    "_instruction_vars",
    "_instructions",
    "_instructions_text",
    "_meta_agent",
    "_modern_identify_note",
    "_obligation_footer",
    "_offload",
    "_offloaded",
    "_outcome_result",
    "_progress_token",
    "_properties",
    "_reexec_ready",
    "_refusal_body",
    "_refusal_said",
    "_rendered",
    "_resolve_call",
    "_result_declaration",
    "_schema",
    "_surf",
    "_text",
    "_text_with_next",
    "_upgrade_line",
    "_waves_auto",
    "_with_next",
    "_worker_main",
    "_worker_stderr",
    "deprecation_note",
    "main",
    "serve",
)
