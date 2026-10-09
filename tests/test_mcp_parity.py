"""Every CLI command must be reachable over MCP.

The requirement: an operator in a chat window — possibly driving a remote agent — must
be able to check status, inspect history and run the workflow without a shell. Any CLI
command with no MCP equivalent is a capability that silently does not exist for them,
and the gap is invisible from either side: the CLI works, the tool list looks full.

This is a ratchet. The exemption list may only SHRINK, and each entry carries the
reason it is not a gap.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.surfaces import exemptions as X
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.mcp import TOOLS

#: EXEMPTION: the `[mcp].tools` tier (core | standard | all) changes what `tools/list`
#: ADVERTISES, never what exists. Every ratchet below reads `TOOLS`, the whole registry,
#: so parity is checked against all tools whatever tier a server runs at; a tool a tier
#: hides is still callable by name. `tests/test_mcp_tool_tiers.py` pins that.

#: The exemptions are FIELDS of the command registry (`surfaces/exemptions.py`: `reason`, `via`,
#: `flag_exempt`, `prose_reason` on a `Command`), and this test reads the tables derived from
#: them there. It keeps none of its own (`test_no_test_keeps_its_own_exemption_table`).


def cli_commands() -> list[str]:
    parser = build_parser()
    action = parser._subparsers._group_actions[0]
    return sorted(action.choices)


def covered(cmd: str) -> bool:
    if cmd in X.EXEMPT_WORDS or (cmd,) in X.ROUTED_PATHS:
        return True
    for alias in X.COVERING_TOOLS.get(cmd, ()):
        if alias in TOOLS:
            return True
    return any(t == f"ddflow_{cmd}" or t.startswith(f"ddflow_{cmd}_") for t in TOOLS)


def cli_leaves() -> list[tuple[str, ...]]:
    """Every terminal subcommand PATH, e.g. ('gate', 'skip'), not just ('gate',).

    The command-level check passed for years while `ddflow gate skip` had no tool at
    all — because `gate` was "covered" by `ddflow_gate_run`. Coverage of a parent says
    nothing about its children, and the child is where the capability lives: `gate
    skip` is the documented escape hatch for `gates.require_outcome`, so an agent
    driving over MCP had no way to drop a single step and had to force past all of them.
    """
    out: list[tuple[str, ...]] = []

    def walk(parser, prefix: tuple[str, ...]) -> None:
        subs = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        subs = [a for a in subs if hasattr(a, "_name_parser_map") or hasattr(a, "add_parser")]
        if not subs:
            out.append(prefix)
            return
        for action in subs:
            for name, sub in action.choices.items():
                walk(sub, (*prefix, name))

    root = build_parser()
    action = root._subparsers._group_actions[0]
    for name, sub in action.choices.items():
        walk(sub, (name,))
    return sorted(out)


def _tool_stem(path: tuple[str, ...]) -> str:
    """A CLI path as a tool-name stem: `bug file-tasks` -> `bug_file_tasks`. ONE rule for
    both detectors (roborev, job 1296: normalising one and not the other left the flag
    check blind to the hyphenated command)."""
    return "_".join(path).replace("-", "_")


def leaf_covered(path: tuple[str, ...]) -> bool:
    if path in X.EXEMPT_PATHS or path in X.ROUTED_PATHS:
        return True
    joined = _tool_stem(path)
    return f"ddflow_{joined}" in TOOLS or any(t.startswith(f"ddflow_{joined}_") for t in TOOLS)


def test_every_cli_SUBCOMMAND_is_reachable_over_mcp():
    missing = [p for p in cli_leaves() if not leaf_covered(p)]
    assert not missing, (
        "CLI subcommands with no MCP tool: "
        + ", ".join("`ddflow " + " ".join(p) + "`" for p in missing)
        + ". Add a tool, or declare the leaf on a Command with a `reason=` (surfaces/exemptions.py)."
    )


def test_the_leaf_exemptions_are_real_and_reasoned():
    live = set(cli_leaves())
    stale = [p for p in X.EXEMPT_PATHS if p not in live]
    assert not stale, f"exemptions for subcommands that no longer exist: {stale}"
    for path, reason in X.EXEMPT_PATHS.items():
        assert len(reason) > 20, f"{path}: the exemption needs a real reason"


def test_the_routed_leaves_are_real():
    live = set(cli_leaves())
    stale = [p for p in X.ROUTED_PATHS if p not in live]
    assert not stale, f"routes declared for subcommands that no longer exist: {stale}"


def test_the_leaf_detector_can_fail():
    assert not leaf_covered(("definitely", "not", "a", "tool"))


def test_every_cli_command_is_reachable_over_mcp():
    missing = [c for c in cli_commands() if not covered(c)]
    assert not missing, (
        f"CLI commands with no MCP tool: {missing}. An operator in a chat window "
        f"cannot reach these at all. Add a tool, or declare it with a `reason=` on its "
        f"Command."
    )


def test_the_exemption_list_only_shrinks():
    stale = [c for c in X.EXEMPT_WORDS if c not in cli_commands()]
    assert not stale, f"exemptions for commands that no longer exist: {stale}"
    for c in X.DECLARATIONS:
        if len(c.path) == 1 and c.reason:
            assert len(c.reason) > 20, f"{c.path}: the exemption needs a real reason"
        if c.path and not c.tool and not (c.reason or c.via):
            raise AssertionError(f"{c.path}: a command with no tool, no reason and no route")


def test_the_aliases_all_resolve():
    for cmd, tools in X.COVERING_TOOLS.items():
        assert cmd in cli_commands(), f"alias for a command that does not exist: {cmd}"
        for t in tools:
            assert t in TOOLS, f"{cmd} aliases {t}, which is not a tool"


def test_the_detector_can_fail():
    """Planted bad input: a command with no tool must be reported."""
    assert not covered("definitely_not_a_tool_xyzzy")


def test_the_status_and_recall_tools_exist_and_say_what_they_are_for():
    """These two are what an operator asks for in words — 'what is the status', 'have
    we done this before' — so their descriptions have to be recognisable as answers to
    those questions, not as API docs."""
    for name, must in (
        ("ddflow_status", "status of this project"),
        ("ddflow_recall", "HAVE WE BEEN HERE BEFORE"),
    ):
        assert name in TOOLS, name
        assert must.lower() in TOOLS[name]["description"].lower(), name


def test_every_tool_description_is_substantial():
    """The description is the ONLY thing a model sees when deciding whether to call a
    tool. A thin one is a tool that does not get used, or gets used wrongly."""
    thin = [n for n, spec in TOOLS.items() if len(spec["description"]) < 60]
    assert not thin, f"tool descriptions too thin to choose by: {thin}"


# -- flags, not just commands ---------------------------------------------------------


def _cli_flags(argv: list[str]) -> set[str]:
    """Every long option the CLI accepts for one subcommand path."""
    import re as _re
    import subprocess as _sp
    import sys as _sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    p = _sp.run(
        [_sys.executable, "-m", "ddflow", *argv, "--help"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(root), "PATH": os.environ.get("PATH", "")},
        timeout=120,
    )
    return set(_re.findall(r"(--[a-z][a-z0-9-]+)", p.stdout)) - {
        "--help",
        "--json",
        "--repo",
        "--agent",
    }


def _tool_for(path: tuple[str, ...]) -> str | None:
    if path in X.ROUTED_PATHS:
        return X.ROUTED_PATHS[path][0]
    joined = _tool_stem(path)
    return f"ddflow_{joined}" if f"ddflow_{joined}" in TOOLS else None


def _pairs() -> list[tuple[str, tuple[str, ...]]]:
    """Every (tool, CLI path) pair, DERIVED rather than listed.

    The first version of this ratchet carried a hand-written list of eleven tools, and
    so was blind to `ddflow remove --force` — which the scenario needed and which
    failed silently until the argument checker started rejecting unknown ones. A
    ratchet with a manually curated input has exactly the coverage someone remembered
    to give it.
    """
    out = []
    for path in cli_leaves():
        if path in X.EXEMPT_PATHS:
            continue
        tool = _tool_for(path)
        if tool:
            out.append((tool, path))
    return out


@pytest.mark.parametrize("tool,argv", _pairs(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_cli_flag_is_reachable_from_its_mcp_tool(tool, argv):
    """Command-level parity is not parity.

    The existing ratchet proved every CLI *command* has a tool, and passed while
    `ddflow_phase_add` had no `globs` at all — so over MCP a phase could not declare
    what it writes, the conflict detector had nothing to compare at phase level, and
    (once phase dependencies became real) every task inside it inherited a dependency
    the operator could not scope. An agent driving over MCP had a strictly weaker tool
    than the same agent driving a shell, with nothing saying so.
    """
    from ddflow.surfaces.mcp import TOOLS

    flags = _cli_flags(argv)
    props = set(TOOLS[tool]["properties"])
    missing = sorted(
        f
        for f in flags
        if f.lstrip("-").replace("-", "_") not in props and (tool, f) not in X.FLAG_EXEMPT
    )
    assert not missing, (
        f"{tool} cannot reach `ddflow {' '.join(argv)}` flag(s) {missing}. "
        f"Add the propert{'y' if len(missing) == 1 else 'ies'} and the argv entry, or "
        f"record the omission as `flag_exempt` on the Command, with its reason."
    )


# -- JSON or prose, but decided rather than accidental --------------------------------

# Tools that deliberately return PROSE rather than JSON are declared with a `prose_reason`
# (`X.PROSE_REASONS`). The distinction is real: some tools hand the model an *instruction* or a
# document, and JSON-encoding a paragraph helps nobody. But it must be a decision, never an
# accident (`decision_add` returned JSON while `task_add` returned prose for no reason either
# could state). A tool prose for SOME arguments (`render --show`, `prompts show`) is declared
# too: prose is a shape it has.


def test_every_tool_is_explicitly_json_or_explicitly_prose():
    """Prose is a DECISION, recorded with its reason, never an accident.

    The stub argument dict this used to build is gone with the argv inspection it fed:
    `_returns_prose` reads the declaration instead, which is both cheaper and the thing
    that stayed true when ten tools changed dispatch mechanism.
    """
    undeclared = []
    for name, spec in sorted(TOOLS.items()):
        # Only the string path can be ambiguous about this. A tool on the typed path
        # returns an `Outcome`, whose `data` is structured by construction, and an
        # `identify` tool mutates the connection and returns a sentence -- neither has
        # an argv to inspect, and asking "does its argv say --json" of them would be a
        # KeyError dressed up as a parity finding.
        if "identify" in spec:
            continue
        if not _returns_prose(name) or name in X.PROSE_REASONS:
            continue
        undeclared.append(name)
    assert not undeclared, (
        f"{undeclared} return prose but have no `prose_reason` on their Command. Either add `--json` to "
        f"the argv — which is right for anything a caller parses — or record WHY prose "
        f"is the useful form here. An agent cannot tell which it will get."
    )


def _returns_prose(name: str) -> bool:
    """Whether a tool's body is a DOCUMENT, whichever way it is dispatched.

    Two mechanisms express the same fact now: a string-path tool omits `--json` from its
    argv, and a typed tool declares `text`. This test asserted the first only, so a prose
    tool became invisible to it the moment it migrated — with a `KeyError: 'argv'`, which
    at least failed loudly. Asking the question once, here, is what keeps the allowlist
    meaningful across the migration rather than only before it.
    """
    spec = TOOLS[name]
    if "argv" in spec:
        return "--json" not in spec["argv"]({"id": "X", "gate": "g"})
    wants_text = spec.get("text", False)
    if callable(wants_text):
        # A tool that is prose for SOME arguments — `render --show` returns a document,
        # `render` alone returns a file list. Prose is a shape it has.
        return True
    return bool(wants_text)


def test_the_prose_list_only_describes_tools_that_exist():
    stale = [n for n in X.PROSE_REASONS if n not in TOOLS]
    assert not stale, f"a prose_reason names tools that are gone: {stale}"
    for name, reason in X.PROSE_REASONS.items():
        assert len(reason) > 20, f"{name}: the reason has to say something"
        assert _returns_prose(name), f"{name} now emits JSON; drop its `prose_reason`"


def test_every_viewer_leaf_names_a_kind_ddflow_list_accepts():
    from ddflow.api.viewers import READ_KINDS

    for path, (tool, kind) in X.ROUTED_PATHS.items():
        assert tool in TOOLS, path
        if tool == "ddflow_list":
            assert kind in READ_KINDS, path
        else:  # a mode of another tool: `kind` names the argument that selects it
            assert kind in TOOLS[tool]["properties"], path


#: The names the exemption tables had when they lived in the tests.
_RETIRED_TABLES = frozenset(
    {
        "NOT_EXPOSED",
        "LEAF_NOT_EXPOSED",
        "LEAF_VIA",
        "ALIASES",
        "FLAG_EXEMPTIONS",
        "PROSE_TOOLS",
        "EXEMPT_WORDS",
    }
)


def test_no_test_keeps_its_own_exemption_table():
    """An exemption is a field of a Command (`reason`, `via`, `flag_exempt`, `prose_reason`),
    declared beside it; a table of them in a test file would be a second place to forget."""
    import ast

    kept = []
    for path in sorted(Path(__file__).parent.rglob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            targets = (
                [node.target] if isinstance(node, ast.AnnAssign) else
                node.targets if isinstance(node, ast.Assign) else []
            )  # fmt: skip
            kept += [
                f"{path.name}: {t.id}"
                for t in targets
                if isinstance(t, ast.Name) and t.id in _RETIRED_TABLES
            ]
    assert not kept, f"exemption tables belong on the Command registry: {kept}"
