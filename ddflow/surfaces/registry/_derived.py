"""What the declarations say, derived (the parity test reads these).

The exemptions, routed paths, covering tools, declared words, flag exemptions and prose
reasons folded out of a tuple of `Command` declarations.
"""

from __future__ import annotations

from ._command import MAX_VIA, Command

# -- what the declarations say, derived (the parity test reads these) -----------------


def exempt_paths(commands: tuple[Command, ...]) -> dict[tuple[str, ...], str]:
    """CLI paths with no tool of their own, each with its reason. A command routed by a
    selector (``via`` of two) is served, not exempt, even when it also carries a reason."""
    # A one-word command WITH subcommands is a GROUP: its exemption is its WORD
    # (`declared_words`), never its path -- so it is not offered here as a leaf exemption.
    words_with_children = {c.path[0] for c in commands if len(c.path) > 1}
    return {
        c.path: c.reason
        for c in commands
        if c.path
        and c.reason
        and len(c.via) != MAX_VIA
        and (len(c.path) > 1 or c.path[0] not in words_with_children)
    }


def routed_paths(commands: tuple[Command, ...]) -> dict[tuple[str, ...], tuple[str, str]]:
    """CLI paths served by another tool's selector: ``path -> (tool, selector value)``."""
    return {c.path: (c.via[0], c.via[1]) for c in commands if c.path and len(c.via) == MAX_VIA}


def covering_tools(commands: tuple[Command, ...]) -> dict[str, tuple[str, ...]]:
    """One-word commands a differently named tool covers whole: ``init -> (ddflow_setup,)``."""
    return {c.path[0]: c.via for c in commands if len(c.path) == 1 and len(c.via) == 1}


def declared_words(commands: tuple[Command, ...]) -> set[str]:
    """One-word commands exempt from MCP outright (a reason, no tool covering them)."""
    return {c.path[0] for c in commands if len(c.path) == 1 and c.reason and not c.via}


def flag_exemptions(commands: tuple[Command, ...]) -> dict[tuple[str, str], str]:
    """``(tool, flag) -> reason`` for every flag a tool omits on purpose."""
    return {(c.tool, f): r for c in commands for f, r in c.flag_exempt.items()}


def prose_reasons(commands: tuple[Command, ...]) -> dict[str, str]:
    """``tool -> reason`` for every tool whose body is text."""
    return {c.tool: c.prose_reason for c in commands if c.tool and c.prose}
