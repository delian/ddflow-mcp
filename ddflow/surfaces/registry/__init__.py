"""The declarative command registry: one description of a command, both surfaces generated.

A `Command` names an operation once -- its CLI path, its MCP tool, its parameters, the
function that runs it and the shape both surfaces print -- and this package generates what
used to be written twice by hand: the argparse subparser (`add_commands`), the MCP tool's
JSON Schema (`input_schema`) and the tool-table entry the MCP engine dispatches on
(`Command.tool_entry`). D-unify 4 (strangler): commands migrate in slices; until one
does, the hand-written parser and `TOOLS` entry stay the source of truth, and
`tests/test_command_registry.py` proves that a `Command` declared to match one generates
the same argparse help and the same `tools/list` schema.

Standard library only. Nothing here imports a surface, so `mcp.py` and the parsers can
both depend on it.

The public API is split across the submodules below and re-exported here, so unchanged
call sites (and the ``from ddflow.surfaces import registry as R`` tests) keep working
exactly as before.
"""

from ._aliases import (
    KINDS,
    WORDS,
    Alias,
    AliasChoices,
    Notices,
    SuggestingParser,
    add_command_alias,
    closest,
    parsed_path,
    used_aliases,
)
from ._assemble import add_commands, by_tool
from ._command import (
    GROUP_DEPTH,
    MAX_VIA,
    CliPolicy,
    Command,
)
from ._command_names import CLI_COMMAND_NAMES, CLI_FLAG_COMMANDS, command_for_path
from ._derived import (
    covering_tools,
    declared_words,
    exempt_paths,
    flag_exemptions,
    prose_reasons,
    routed_paths,
)
from ._mcp_aliases import (
    rename_args,
    resolve_tool,
    unknown_arg_hint,
    unknown_tool_hint,
)
from ._param import (
    AS_AGENT,
    AS_AGENT_SPEC,
    OPTIONAL_NARGS,
    TYPES,
    Param,
    params_schema,
    properties_schema,
)
from ._schemas import (
    ARRAY_PAYLOADS,
    REFUSAL_KEY,
    SCHEMA_KEY,
    SCHEMA_VERSIONS,
    SHAPES,
    TOOL_PREFIX,
    array_schema,
    command_name,
    output_schema,
    payload_fields,
    result_schemas,
    result_shape,
    schema_tag,
    schema_version,
    tag_body,
)

__all__ = (
    "ARRAY_PAYLOADS",
    "AS_AGENT",
    "AS_AGENT_SPEC",
    "CLI_COMMAND_NAMES",
    "CLI_FLAG_COMMANDS",
    "GROUP_DEPTH",
    "KINDS",
    "MAX_VIA",
    "OPTIONAL_NARGS",
    "REFUSAL_KEY",
    "SCHEMA_KEY",
    "SCHEMA_VERSIONS",
    "SHAPES",
    "TOOL_PREFIX",
    "TYPES",
    "WORDS",
    "Alias",
    "AliasChoices",
    "CliPolicy",
    "Command",
    "Notices",
    "Param",
    "SuggestingParser",
    "add_command_alias",
    "add_commands",
    "array_schema",
    "by_tool",
    "closest",
    "command_for_path",
    "command_name",
    "covering_tools",
    "declared_words",
    "exempt_paths",
    "flag_exemptions",
    "output_schema",
    "params_schema",
    "parsed_path",
    "payload_fields",
    "properties_schema",
    "prose_reasons",
    "rename_args",
    "resolve_tool",
    "result_schemas",
    "result_shape",
    "routed_paths",
    "schema_tag",
    "schema_version",
    "tag_body",
    "unknown_arg_hint",
    "unknown_tool_hint",
    "used_aliases",
)
