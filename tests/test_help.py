"""`ddflow help` — and the ratchets that stop it becoming wrong.

A help page that recommends a flag which was renamed is worse than no help page: the
reader who finds nothing reads the code, and the reader who finds a wrong answer trusts
it. So the prose is allowed to be prose, and everything checkable about it is checked —
every command a page names must exist, every topic offered must resolve, and the
capability inventory is generated from the live registry rather than typed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import help as H
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.mcp import TOOLS

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _cli_leaves() -> set[str]:
    """Every RUNNABLE command path as a space-joined string: `gate run`, `doctor`.

    A parent with subcommands counts too when it is runnable on its own —
    `ddflow workflow` shows the workflow, while `ddflow gate` alone is an argparse
    error. The tell is whether the parent itself carries an `fn` default.
    """
    out: set[str] = set()

    def walk(parser, prefix: tuple[str, ...]) -> None:
        subs = [a for a in parser._actions if getattr(a, "choices", None)]
        subs = [a for a in subs if hasattr(a, "_name_parser_map")]
        if prefix and (not subs or "fn" in getattr(parser, "_defaults", {})):
            out.add(" ".join(prefix))
        for action in subs:
            for name, sub in action.choices.items():
                walk(sub, (*prefix, name))

    walk(build_parser(), ())
    return out


# -- it answers the question -----------------------------------------------------------


def test_the_overview_explains_the_loop_not_just_the_commands(repo):
    """argparse already lists 43 subcommands alphabetically. What it never said is
    which to reach for first, or why any of them refuses."""
    code, out, _ = run_cli(repo, "help")
    assert code == OK, out
    for must in ("ddflow next", "ddflow claim", "ddflow complete", "ddflow merge"):
        assert must in out, f"the loop never mentions {must!r}"
    assert "exit" in out.lower(), "the exit-code vocabulary is half the interface"


def test_every_topic_the_overview_offers_actually_resolves(repo):
    """The classic broken-index failure: a menu offering a page nobody wrote."""
    _code, out, _ = run_cli(repo, "help")
    for topic in H.TOPICS:
        assert topic in out, f"{topic} is a topic but the overview never lists it"
        code, body, err = run_cli(repo, "help", topic)
        assert code == OK, f"{topic}: {err}"
        assert len(body.strip()) > 200, f"{topic} resolved to almost nothing"


def test_an_unknown_topic_names_the_known_ones(repo):
    code, _out, err = run_cli(repo, "help", "nosuchthing")
    assert code == FAIL
    for topic in H.TOPICS:
        assert topic in err, err


# -- the inventory cannot drift --------------------------------------------------------


def test_the_inventory_is_generated_from_the_live_registry(repo):
    """Every tool appears, exactly once. A hand-written command list in a second place
    is the documentation-drift class, and this project has paid for it twice."""
    _code, out, _ = run_cli(repo, "help")
    listed = [t for t in TOOLS if t in out]
    assert sorted(listed) == sorted(TOOLS), sorted(set(TOOLS) - set(listed))
    for tool in TOOLS:
        assert out.count(tool) >= 1


def test_every_tool_is_classified(repo):
    """Unmapped tools fall into a visible bucket rather than vanishing from an
    inventory that claims to be complete — and this keeps that bucket empty, so a new
    capability has to be given a group instead of silently disappearing."""
    assert H.unmapped_tools(TOOLS) == [], (
        f"these tools belong to no group, so `ddflow help` files them under "
        f"'Unmapped': {H.unmapped_tools(TOOLS)}. Add a prefix to `_GROUPS`."
    )


def test_a_new_tool_changes_the_inventory(monkeypatch):
    """Mutation-proof that the inventory is derived and not a transcript of it."""
    monkeypatch.setitem(TOOLS, "ddflow_doctor_zzz", {"description": "", "properties": {}})
    assert any("ddflow_doctor_zzz" in members for _title, members in H.grouped_tools(TOOLS)), (
        "a tool was added and the generated inventory did not notice"
    )


# -- the prose cannot rot --------------------------------------------------------------


def test_every_command_a_help_page_names_exists():
    """The rot that matters. A page recommending `ddflow gate check` — which never
    existed — costs a reader more than no page at all, because they believe it.

    Checked against BOTH registries: the argparse tree and the MCP tool table.
    """
    leaves = _cli_leaves()
    tools = {t.removeprefix("ddflow_") for t in TOOLS}
    tops = {leaf.split()[0] for leaf in leaves}
    #: A top-level command that HAS subcommands. Naming one without a valid subcommand
    #: is the failure this catches -- `ddflow gate check` reads as real and is not.
    parents = {t for t in tops if any(leaf.startswith(f"{t} ") for leaf in leaves)}

    unknown: list[tuple[str, str]] = []
    pages = {"index": H.render_index(tools=TOOLS), **{t: H.render_topic(t) for t in H.TOPICS}}
    for page, text in pages.items():
        for sep, mention in H.command_mentions(text):
            if sep == "_":
                # An MCP tool name. Checked against the tool table, not the parser:
                # `ddflow_import_verify` is one tool and `ddflow import --verify` is
                # a flag, and neither registry knows about the other's spelling.
                if mention not in tools:
                    unknown.append((page, f"ddflow_{mention}"))
                continue
            if mention in leaves:
                continue  # an exactly-runnable path, parent or leaf
            words = mention.split()
            head = words[0]
            if head not in tops:
                unknown.append((page, mention))
            elif head in parents and (len(words) < 2 or f"{head} {words[1]}" not in leaves):
                unknown.append((page, mention))
    assert not unknown, f"help pages name commands that do not exist: {unknown}"


def test_every_topic_has_a_shipped_page():
    for topic in H.TOPICS:
        assert (H.help_dir() / f"{topic}.md").is_file(), topic
    assert (H.help_dir() / "index.md").is_file()


# -- an operator can rewrite it --------------------------------------------------------


def test_a_project_override_wins(repo):
    """Same precedence as every other template: help is operator-tunable text, not
    code."""
    d = repo / ".ddflow" / "prompts" / "help"
    d.mkdir(parents=True)
    (d / "workflow.md").write_text("# Our workflow\n\nAsk Dana first.\n")
    _code, out, _ = run_cli(repo, "help", "workflow")
    assert "Ask Dana first" in out, out


def test_the_overview_can_be_overridden_too(repo):
    d = repo / ".ddflow" / "prompts" / "help"
    d.mkdir(parents=True)
    (d / "index.md").write_text("# Ours\n\n{{ topics }}\n{{ inventory }}\n{{ tool_count }}\n")
    _code, out, _ = run_cli(repo, "help")
    assert out.startswith("# Ours"), out[:80]
    assert "ddflow_doctor" in out, "the generated inventory must still be injected"


# -- both surfaces ---------------------------------------------------------------------


def test_the_json_surface_carries_the_text_and_the_topics(repo):
    _code, out, _ = run_cli(repo, "--json", "help")
    data = json.loads(out)
    assert data["topic"] == "index"
    assert "ddflow" in data["text"]
    assert set(data["topics"]) == set(H.TOPICS)


def test_it_is_reachable_over_mcp(repo):
    assert "ddflow_help" in TOOLS
    assert TOOLS["ddflow_help"]["argv"]({}) == ["--json", "help"]
    assert TOOLS["ddflow_help"]["argv"]({"topic": "gates"}) == ["--json", "help", "gates"]
