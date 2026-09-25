"""`orchard help` — what this is, what it can do, and what the workflow is.

An agent connecting over MCP gets 58 tool descriptions and a state-aware handshake.
Neither answers "what IS this, and how am I supposed to use it": a tool description
explains one tool to someone who already chose it, and the handshake explains this
repository right now. A person typing `orchard` for the first time got an argparse
list of 43 subcommands in alphabetical order.

**Hybrid on purpose.** The narrative lives in templates under `templates/prompts/help/`,
so an operator can rewrite any of it without touching code — the same precedence every
other template has, `.orchard/prompts/help/<topic>.md` beating the shipped default. The
**inventory is generated** from the live registries, because a hand-kept command list in
a second place is the documentation-drift class and this project has already paid for
it twice.

What keeps the prose honest is `tests/test_help.py`: every command a topic names must
exist, and every topic the index offers must resolve. A help page that recommends a flag
that was renamed is worse than no help page — the reader who finds nothing reads the
code, and the reader who finds a wrong answer trusts it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .prompts import Template, TemplateError, builtin_dir, render

#: topic -> the one line the index prints beside it. The keys ARE the valid topics;
#: `tests/test_help.py` asserts each has a shipped file and that the index lists them
#: all, so adding a topic here without writing it fails loudly rather than 404s later.
TOPICS: dict[str, str] = {
    "workflow": "the loop: pick work, claim it, satisfy its gates, land it",
    "import": "adopting a project that already has history",
    "gates": "what a gate is, which are agent gates, and proving one can fail",
    "parallel": "several agents at once: worktrees, leases, conflict refusal",
    "memory": "what the project remembers, and how to ask it",
    "recovery": "crashes, salvage, and rebuilding from the log",
    "config": "knobs, where they live, and how to change them",
    "customise": "changing the workflow itself: pipelines, gates, prompts",
    "cli": "driving it from a terminal, a Makefile or CI, with no agent at all",
    "mcp": "driving it as an MCP server, and what to put in AGENTS.md / CLAUDE.md",
}

#: Tool-name prefix -> the group it is printed under. Ordered: the first match wins, so
#: `orchard_gate_verify` lands in Gates rather than in Health via a later prefix.
#:
#: Explicit rather than derived, and guarded rather than trusted: a tool matching no
#: prefix falls into "unmapped", and a test fails while that set is non-empty. A new
#: capability therefore has to be CLASSIFIED, instead of quietly vanishing from the
#: inventory that is supposed to be complete.
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Setting up", ("setup", "configure", "companions", "hooks", "prompts", "adopt")),
    ("The rules this project runs by", ("workflow",)),
    ("Shaping the work", ("phase", "task", "split", "update", "remove", "import")),
    ("Doing the work", ("next", "claim", "heartbeat", "release", "complete", "block", "abandon")),
    ("Gates and review", ("gate", "review", "reviewers")),
    ("Landing it", ("merge", "cleanup")),
    ("What the project remembers", ("recall", "lesson", "decision", "research", "bug", "session")),
    ("Looking at it", ("board", "show", "status", "brief", "progress", "render", "history")),
    ("When something is wrong", ("doctor", "recover", "loops", "rebuild", "replay", "cadence")),
    ("Help", ("help",)),
)

#: `orchard_gate_run` / `orchard gate run` / `orchard doctor` inside help prose, with
#: the SEPARATOR captured -- an underscore names an MCP tool and a space names a CLI
#: invocation, and they are checked against different registries. Conflating them let
#: `orchard gate check` pass the anti-rot test because `orchard_gate_run` exists.
COMMAND_MENTION = re.compile(r"\borchard([_ ])([a-z][a-z_]*(?: [a-z][a-z_]*){0,2})\b")


def help_dir() -> Path:
    return builtin_dir() / "help"


def _page(name: str, repo: Path | None) -> Template:
    """One help page by file name, project override first.

    Same precedence as `resolve_command`: `.orchard/prompts/help/<name>.md` beats the
    shipped default, so a project can rewrite its own onboarding without a code change.
    One function for the index and the topics, because they differ only in whether the
    name had to be a known topic -- and two copies of a file-resolution rule is how the
    override silently stops working for one of them.
    """
    if repo:
        local = Path(repo) / ".orchard" / "prompts" / "help" / f"{name}.md"
        if local.is_file():
            return Template(name, local.read_text("utf-8"), "project", local)
    path = help_dir() / f"{name}.md"
    if not path.is_file():
        raise TemplateError(f"shipped help page {name}.md is missing from the package")
    return Template(name, path.read_text("utf-8"), "builtin", path)


def resolve_topic(name: str, repo: Path | None = None) -> Template:
    if name not in TOPICS:
        raise TemplateError(
            f"unknown help topic {name!r}. Known: {', '.join(sorted(TOPICS))}. "
            f"`orchard help` on its own lists them with a line each."
        )
    return _page(name, repo)


def grouped_tools(tools: Iterable[str]) -> list[tuple[str, list[str]]]:
    """Every tool, in reading order, under the group it belongs to.

    Generated from the registry the CALLER passes in, so it cannot drift: a tool that
    exists appears here, and one that does not cannot.

    Taken as an argument rather than imported, because `services/` sits BELOW
    `surfaces/` and importing `surfaces.mcp` from here inverts that -- which
    `tests/test_layering.py` caught the first time this was written the easy way. The
    surface owns its registry; this renders whatever it is handed.
    """
    tools = list(tools)
    out: list[tuple[str, list[str]]] = []
    claimed: set[str] = set()
    for title, prefixes in _GROUPS:
        members = []
        for tool in sorted(tools):
            if tool in claimed:
                continue
            tail = tool.removeprefix("orchard_")
            if any(tail == p or tail.startswith(f"{p}_") for p in prefixes):
                members.append(tool)
                claimed.add(tool)
        if members:
            out.append((title, members))
    unmapped = sorted(set(tools) - claimed)
    if unmapped:
        out.append(("Unmapped — add a prefix to `_GROUPS`", unmapped))
    return out


def unmapped_tools(tools: Iterable[str]) -> list[str]:
    """Tools no group claims. Must stay empty; `tests/test_help.py` is the ratchet."""
    for title, members in grouped_tools(tools):
        if title.startswith("Unmapped"):
            return members
    return []


def render_index(repo: Path | None = None, *, tools: Iterable[str] = ()) -> str:
    """The front page: what this is, the loop, the topics, the inventory."""
    tools = list(tools)
    topics = "\n".join(f"  {name:<10} {desc}" for name, desc in TOPICS.items())
    inventory = "\n".join(
        f"{title}\n" + "\n".join(f"  {t}" for t in members)
        for title, members in grouped_tools(tools)
    )
    return render(_page("index", repo), topics=topics, inventory=inventory, tool_count=len(tools))


def render_topic(name: str, repo: Path | None = None) -> str:
    return render(resolve_topic(name, repo))
