"""Registering an MCP server in an agent's project config: ONE writer for every caller.

`adopt` (ddflow itself) and `companions add` (a third-party server) each had their own:
adopt decided a TOML config already registered ddflow by finding the header as TEXT -- a
stale launch was never refreshed and a commented-out header counted (Ba4cc85bdbc) --
while the companions writer parsed the file, refreshed a stale entry and could preview
the write. `register` is that writer, for both: where servers live in each agent's file
(`SHAPE_*`, `place_server`), what counts as already registered, and what is refused.

What differs per caller is passed in, not re-implemented: `same` (does a stored TOML
entry launch this server -- a companion compares launches, ddflow compares entries) and
`elsewhere` (the message when the server already runs under another name).
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..infra.fsio import Unreadable, read_json, replace_text
from ..infra.tomlcfg import value as toml_value

#: How a server entry nests inside an agent's config file. A VOCABULARY rather than a
#: chain of `if key == ...`: the writer used to special-case Copilot inline, which worked
#: for exactly two shapes and could not express a third. Each value is checked against
#: that agent's own documentation — see `docs/RESEARCH.md` R15.
SHAPE_MCP_SERVERS = "mcpServers"  #: {"mcpServers": {"ddflow": {command, args}}} -- common
SHAPE_SERVERS = "servers"  #: {"servers": {"ddflow": {type: "stdio", command, args}}} -- VS Code
SHAPE_COPILOT = "copilot"  #: {"mcpServers": {"ddflow": {type: "local", ..., tools}}} -- Copilot CLI
SHAPE_MCP_DOT_SERVERS = "mcp.servers"  #: {"mcp": {"servers": {...}}} -- ZCode (GLM)
SHAPE_OPENCODE = "opencode"  #: {"mcp": {"ddflow": {type: "local", command: [...]}}}
SHAPE_TOML = "toml.mcp_servers"  #: [mcp_servers.ddflow] in TOML -- Codex
#: No project-level MCP config file EXISTS for this agent: it is configured in an IDE
#: panel, a web UI, or a user-level file outside the repository. The delta doc and
#: `AGENTS.md` still apply, and those are the parts that make the workflow portable — so
#: the agent is SUPPORTED, and the honest record is that one step is manual. Inventing a
#: plausible path would be worse than admitting it: ddflow would write a file the agent
#: never reads and the operator would believe it was wired up.
SHAPE_NONE = "none"


def server_entry_for(shape: str, entry: dict) -> dict:
    """``entry`` rewritten the way THIS agent's config must store it.

    Only opencode (and Kilo, its fork) differs, and it differs in a way that fails silently: `command` is one
    ARRAY including the arguments, the transport is named rather than inferred, and
    `enabled` is explicit. Handing it the common `{"command": str, "args": [...]}` form
    produces valid JSON that starts nothing.
    """
    if shape == SHAPE_OPENCODE:
        out: dict = {
            "type": "local",
            "command": [entry["command"], *entry.get("args", [])],
            "enabled": True,
        }
        if entry.get("env"):
            out["environment"] = entry["env"]
        return out
    if shape == SHAPE_SERVERS:
        # VS Code names the transport in the entry; its own documented example carries
        # `"type": "stdio"`, so that is what is written rather than relying on it being
        # inferred from the presence of `command`.
        return {"type": "stdio", **entry}
    if shape == SHAPE_COPILOT:
        # Copilot CLI calls a stdio server "local", and `tools` is its allowlist -- absent,
        # a server's tools are not offered. `["*"]` means "all of ddflow's tools", which is
        # the only useful setting for a queue the agent is supposed to drive.
        return {"type": "local", **entry, "tools": ["*"]}
    return dict(entry)


def _server_container(data: dict, shape: str, *, create: bool) -> dict | None:
    """The dict inside ``data`` that maps server NAME -> entry, for this shape.

    One place that knows where servers live in each file. There used to be three -- a
    ternary in the adopter, `_json_field` in the companions writer and a
    `SERVERS_FIELD_AGENTS` set beside it -- so adding an agent meant editing three
    things that no test tied together, and only one of them could express nesting.
    """
    if shape == SHAPE_OPENCODE:
        path: tuple[str, ...] = ("mcp",)
    elif shape == SHAPE_MCP_DOT_SERVERS:
        path = ("mcp", "servers")
    elif shape in (SHAPE_MCP_SERVERS, SHAPE_COPILOT):
        path = ("mcpServers",)
    elif shape == SHAPE_SERVERS:
        path = ("servers",)
    else:
        raise ValueError(f"unknown MCP config shape {shape!r}")
    node = data
    for part in path:
        if create:
            node = node.setdefault(part, {}) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                return None
        else:
            node = (node.get(part) if isinstance(node, dict) else None) or {}
            if not isinstance(node, dict):
                return None
    return node


class UnplaceableConfig(ValueError):
    """The operator's file is valid JSON but holds something other than an object where
    servers live -- `{"mcp": null}`, `{"mcp": ["x"]}`, a top-level list. Replacing it
    would destroy their data; guessing a merge would be worse. The caller says SKIPPED."""


def place_server(data: dict, shape: str, name: str, entry: dict) -> None:
    """Put one server into ``data`` where ``shape`` says it belongs.

    Mutates in place and preserves every sibling: these files hold the operator's other
    servers, and a tool that stomps them is a tool nobody runs twice. Raises
    `UnplaceableConfig` rather than crash (it used to be an `assert`, and a TypeError for
    a list) when the file holds a non-object where servers go.
    """
    container = _server_container(data, shape, create=True)
    if container is None:
        raise UnplaceableConfig(f"not a JSON object where {shape!r} servers belong")
    container[name] = server_entry_for(shape, entry)


def get_server(data: dict, shape: str, name: str) -> Any:
    """What ``data`` currently stores for ``name``, or None. The read half of `place_server`."""
    container = _server_container(data, shape, create=False)
    return (container or {}).get(name)


def get_servers(data: dict, shape: str) -> dict:
    """Every server ``data`` stores, by name: the container `get_server` reads, whole."""
    return dict(_server_container(data, shape, create=False) or {})


def _launch_of(entry: object) -> tuple[str, list[str]] | None:
    """``(command, args)`` of one stored server entry, whatever the agent's shape.

    opencode/Kilo keep one `command` ARRAY holding the arguments; a few configs write the
    whole launch as one `command` string. A remote server (`url`) has no launch.
    """
    if not isinstance(entry, dict):
        return None
    cmd, args = entry.get("command"), entry.get("args") or []
    if not isinstance(args, list):
        return None  # checked BEFORE the array form merges it: `*5` raised, `*"a b"` split
    if isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd):
        # Already tokenised: an element holding a space is ONE token (`/Apps/My App/x`).
        return (cmd[0], [*cmd[1:], *map(str, args)]) if cmd[0].strip() else None
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    # A whole launch written as one string is split -- unless the string names a file
    # that exists, which is a path with a space in it, not a command line.
    if not args and any(ch.isspace() for ch in cmd.strip()) and not Path(cmd).exists():
        try:
            cmd, *args = shlex.split(cmd)
        except ValueError:
            return None
    return cmd, [str(a) for a in args]


def _load_toml(text: str) -> dict | None:
    """Parsed TOML, or None when it does not parse. ONE guard for the readers.

    `companions._servers_in`, `_toml_without`, `_register_toml`'s stale-entry check and
    `_toml_present` each wrapped `tomllib.loads` by hand; one that forgot the guard would
    let an unparseable config read as an empty one -- the recurring failure here. What
    `None` then MEANS stays at each call site, because it differs: a reader treats it as
    unreadable, `_toml_present` as a refusal that names the file.
    """
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


#: The keys agents use for a REMOTE server's address: Claude/Cursor/VS Code `url`, Gemini
#: CLI `httpUrl`, Windsurf `serverUrl`. A remote server has no launch and is still one.
_REMOTE_KEYS = ("url", "httpUrl", "serverUrl")


def _serves(entry: object) -> bool:
    """Does one stored entry start or reach SOME server -- a launch, or a remote address?

    What it serves is not asked: an operator's own wrapper under the companion's id is
    theirs. Only an entry that can serve nothing -- `{}`, `"x"`, `5`, `[]`, a `null`
    placeholder -- is refused, because counting it reported the gate as covered while no
    agent could reach the tool, and `register` would have written over it (B768503a43a).
    """
    if _launch_of(entry) is not None:
        return True
    return isinstance(entry, dict) and any(
        isinstance(entry.get(k), str) and entry[k].strip() for k in _REMOTE_KEYS
    )


def _declared(servers: dict, cid: str) -> bool:
    """Is ``cid`` declared AND does its entry serve something (`_serves`)?

    The reader (`companions._registered_name`), the writer (`_toml_present`) and the
    stale-entry check in `_register_toml` must agree on what "already registered" means; written once, so
    one of them cannot quietly count a `{}` placeholder the others refuse (B768503a43a).
    """
    return cid in servers and _serves(servers[cid])


def _toml_without(text: str, cid: str) -> str | None:
    """``text`` minus every ``[mcp_servers.<cid>]`` table and sub-table, or None.

    Textual, since `tomllib` cannot write. The cut is trusted only when the result still
    parses and differs from the original by exactly that server: an inline table, a dotted
    key, or a header inside a multi-line string fails the check and is left alone.
    """
    name = "(?:{0}|\"{0}\"|'{0}')".format(re.escape(cid))
    header = re.compile(rf"^\s*\[\s*mcp_servers\s*\.\s*{name}\s*(?:\.[^\]]*)?\]\s*(?:#.*)?$")
    kept: list[str] = []
    cut: list[str] = []  # the lines of the table being dropped
    for line in text.splitlines(keepends=True):
        if header.match(line):
            cut.append(line)
        elif cut and line.lstrip().startswith("["):
            # Comments and blank lines just above the NEXT header describe it, not the
            # table that is going: keep them.
            tail = []
            while cut[-1].strip() == "" or cut[-1].lstrip().startswith("#"):
                tail.insert(0, cut.pop())
            kept.extend(tail)
            cut = []
            kept.append(line)
        elif cut:
            cut.append(line)
        else:
            kept.append(line)
    out = "".join(kept)
    before, after = _load_toml(text), _load_toml(out)
    if before is None or after is None:
        return None
    srv = before.get("mcp_servers")
    if not isinstance(srv, dict):
        return None
    expect = {**before, "mcp_servers": {k: v for k, v in srv.items() if k != cid}}
    # A bare `[mcp_servers]` header is an empty table on both sides, or on neither.
    for d in (expect, after):
        if d.get("mcp_servers") == {}:
            d.pop("mcp_servers")
    return out if after == expect else None


def register(
    path: Path,
    shape: str,
    name: str,
    entry: dict,
    *,
    dry_run: bool = False,
    rel: str = "",
    manual: str = "",
    ours: str = "the registry's launch",
    same: Callable[[Any], bool] | None = None,
    elsewhere: Callable[[dict], str] | None = None,
) -> tuple[str, str]:
    """Put server ``name`` with launch ``entry`` into the agent config at ``path``.

    Returns ``(status, message)``, status one of ``written`` / ``unchanged`` /
    ``refused``; a refusal writes nothing. ``dry_run`` reports what WOULD be written and
    touches nothing, and is decided by the same checks as the write, so a preview never
    promises a write the real call would decline.

    ``rel`` names the file in messages (default: ``path``); ``manual`` is what a refusal
    tells the operator to add by hand (default: ``name``); ``ours`` names the launch a
    stale TOML table differs from. ``same(stored)`` says whether a stored TOML entry is
    already this launch (default: equal to ``entry``); a JSON entry counts only when it
    is IDENTICAL to what would be written. ``elsewhere(servers)`` returns the message
    when the config already launches this server under another name, or "".
    """
    rel = rel or str(path)
    manual = manual or name
    if shape == SHAPE_TOML:
        return _register_toml(
            path, rel, name, entry, dry_run, manual, ours, same or _equal_to(entry), elsewhere
        )
    data: Any = {}
    if path.exists():
        # Checked BEFORE the dry run reports, so a preview never promises a write that
        # the real call would decline.
        read = read_json(path)
        if isinstance(read, Unreadable) and read.kind == "invalid":
            return "refused", f"SKIPPED {rel}: it is not valid JSON; add {manual} by hand"
        if isinstance(read, Unreadable) and read.kind == "unreadable":
            return "refused", (
                f"SKIPPED {rel}: it could not be read ({read.detail}); add {manual} by hand"
            )
        # Not an object: the placement below refuses it in its own words, as it always has.
        data = read.value if isinstance(read, Unreadable) else read
    if get_server(data, shape, name) == server_entry_for(shape, entry):
        # IDENTICAL, not merely present: an entry whose launch changed must be refreshed,
        # and re-running the command is the obvious remedy.
        return "unchanged", f"{rel} already registers {name} with the same launch command"
    # Checked even when the name holds a STALE entry: refreshing it would start the
    # server twice, once under each name. The other name already serves it.
    if elsewhere and (other := elsewhere(get_servers(data, shape))):
        return "unchanged", other
    try:
        place_server(data, shape, name, entry)
    except UnplaceableConfig as exc:
        return "refused", f"SKIPPED {rel}: {exc}; add {manual} by hand"
    if dry_run:
        # The MERGED result, not a lone entry: the write merges into a file holding the
        # operator's other servers, and a preview showing only the addition misleads in
        # the one way that matters.
        return "written", f"WOULD add to {rel}:\n{json.dumps(data, indent=2)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_text(path, json.dumps(data, indent=2) + "\n")
    return "written", f"registered {name} in {rel}"


def _equal_to(entry: dict) -> Callable[[Any], bool]:
    """The default `same`: the stored TOML table is exactly this launch."""
    want = dict(entry)
    return lambda stored: stored == want


def _toml_present(
    text: str,
    new_text: str,
    name: str,
    rel: str,
    manual: str,
    same: Callable[[Any], bool],
    elsewhere: Callable[[dict], str] | None,
) -> tuple[str, str] | None:
    """What a TOML config already says about ``name``, or None when ``new_text`` may be
    written.

    An entry under the name counts only when it launches something (`_declared`) and is
    this launch (`same`); a launch under another name counts too (`elsewhere`). Anything
    else is appended -- but only when the result still PARSES. A table under the name that
    launches nothing, or an `mcp_servers` that is not a table, would turn the append into
    a file the agent rejects whole; that is refused by name, never reported as already
    registered (B768503a43a). Nor is anything appended to a file that does not parse.
    """
    data = _load_toml(text)
    if data is None:
        return "refused", f"SKIPPED {rel}: it is not valid TOML; add {manual} by hand"
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        # `mcp_servers = 5`, or `[[mcp_servers]]`: an appended header would either break
        # the file or land inside the last array element, where no agent reads it.
        return "refused", f"SKIPPED {rel}: its `mcp_servers` is not a table; add {manual} by hand"
    if _declared(servers, name) and same(servers[name]):
        return "unchanged", f"{rel} already registers {name}"
    if elsewhere and (other := elsewhere(servers)):
        return "unchanged", other
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        what = (
            f"[mcp_servers.{name}] is there but launches nothing"
            if name in servers
            else f"adding [mcp_servers.{name}] would not parse ({exc})"
        )
        return "refused", f"SKIPPED {rel}: {what}; fix it by hand"
    return None


def _register_toml(
    path: Path,
    rel: str,
    name: str,
    entry: dict,
    dry_run: bool,
    manual: str,
    ours: str,
    same: Callable[[Any], bool],
    elsewhere: Callable[[dict], str] | None,
) -> tuple[str, str]:
    """The TOML (codex) half of `register`."""
    try:
        text = path.read_text("utf-8") if path.exists() else ""
    except (UnicodeDecodeError, OSError) as exc:  # B26e804cd45, the TOML half
        return "refused", f"SKIPPED {rel}: it could not be read ({exc}); add {manual} by hand"
    # tomlcfg.value, not '"{a}"': a quote or backslash in a path wrote an agent config no
    # TOML parser reads, taking every server in it down (Bb11e7a8186).
    block = (
        f"\n[mcp_servers.{name}]\ncommand = {toml_value(entry['command'])}\n"
        f"args = {toml_value(list(entry.get('args', [])))}\n"
    )
    # The env too: a source-checkout launch carries PYTHONPATH, and without it the
    # server cannot import its package.
    if entry.get("env"):
        block += f"env = {toml_value(dict(entry['env']))}\n"
    replaced = False
    data = _load_toml(text)
    servers = data.get("mcp_servers", {}) if data is not None else {}
    if isinstance(servers, dict) and _declared(servers, name) and not same(servers[name]):
        # A stale table is refreshed like the JSON path (B662a1ace82): the old table is
        # cut out and this launch appended -- unless the same launch already runs under
        # another name (a second copy), or the table is not one that can be cut out.
        if elsewhere and (other := elsewhere(servers)):
            return "unchanged", other
        if (cut := _toml_without(text, name)) is None:
            return "refused", (
                f"SKIPPED {rel}: [mcp_servers.{name}] launches something other than "
                f"{ours} and is not a plain table that can be rewritten; replace it by hand"
            )
        text, replaced = cut, True
    new_text = text.rstrip() + "\n" + block if text.strip() else block.lstrip()
    if verdict := _toml_present(text, new_text, name, rel, manual, same, elsewhere):
        return verdict
    if dry_run and replaced:
        return "written", f"WOULD replace in {rel}:\n{block.lstrip()}"
    if dry_run:
        # The block as it will be APPENDED, minus the leading blank line that only
        # separates it from what is above; the write puts this text in the file verbatim.
        return "written", f"WOULD add to {rel}:\n{block.lstrip()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_text(path, new_text)
    return "written", f"{'refreshed' if replaced else 'registered'} {name} in {rel}"
