"""`ddflow help` — and the ratchets that stop it becoming wrong.

A help page that recommends a flag which was renamed is worse than no help page: the
reader who finds nothing reads the code, and the reader who finds a wrong answer trusts
it. So the prose is allowed to be prose, and everything checkable about it is checked —
every command a page names must exist, every topic offered must resolve, and the
capability inventory is generated from the live registry rather than typed.
"""

from __future__ import annotations

import json
import re
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


#: The command pattern with hyphenated words captured whole. A plain one stops at a
#: hyphen, so `ddflow hooks check-msg` reached the checker as `hooks check` -- and no
#: rule over the truncated word can tell it from a literal `ddflow hooks check`
#: (Bf3819cdb35). The checks judge what was really written through this. A word must still START with a
#: letter, so `ddflow cleanup --apply` stays `cleanup`.
HYPHENATED_MENTION = re.compile(r"\bddflow([_ ])([a-z][a-z_-]*(?: [a-z][a-z_-]*){0,2})\b")


#: Text between single backticks.
_CODE_SPAN = re.compile(r"`([^`\n]+)`")


def mentions(text: str) -> list[tuple[str, str]]:
    """Every `(separator, command)` a page NAMES, from code context only, with hyphenated
    words kept whole.

    Only backticked spans, indented lines and fenced blocks count. Scanning bare prose as
    well was always slightly wrong ("ddflow runs it and the exit code is the evidence"
    would claim `ddflow runs it and` is a command). The convention holds in every shipped
    page: a command appears in backticks or in an indented block, and prose starts at
    column zero. A fenced block sits at column ZERO, so all three contexts are named.
    """
    out: list[tuple[str, str]] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        fragments = [line] if fenced or line[:1].isspace() else _CODE_SPAN.findall(line)
        for fragment in fragments:
            out.extend(HYPHENATED_MENTION.findall(fragment))
    return out


def unmapped_tools(tools) -> list[str]:
    """Tools no group claims. Must stay empty."""
    for title, members in H.grouped_tools(tools):
        if title.startswith("Unmapped"):
            return members
    return []


def unknown_cli_mentions(mentions: list[str], leaves: set[str] | None = None) -> list[str]:
    """The CLI mentions (`gate run`, `lease release`) that do not resolve in argparse.

    Shared with the remedy-text ratchet, so help pages and printed advice are held to
    one definition of "names a real command" rather than two that can drift.
    """
    leaves = _cli_leaves() if leaves is None else leaves
    tops = {leaf.split()[0] for leaf in leaves}
    #: A top-level command that HAS subcommands. Naming one without a valid subcommand
    #: is the failure this catches -- `ddflow gate check` reads as real and is not.
    parents = {t for t in tops if any(leaf.startswith(f"{t} ") for leaf in leaves)}
    unknown: list[str] = []
    for mention in mentions:
        if mention in leaves:
            continue  # an exactly-runnable path, parent or leaf
        words = mention.split()
        head = words[0]
        if head not in tops or (
            head in parents and (len(words) < 2 or f"{head} {words[1]}" not in leaves)
        ):
            unknown.append(mention)
    return unknown


def unknown_mentions(mentions: list[tuple[str, str]], leaves: set[str] | None = None) -> list[str]:
    """Which `(separator, command)` pairs from `mentions` name nothing real.

    Checked against BOTH registries. `_` is an MCP tool, looked up in the tool table,
    not the parser: `ddflow_import_verify` is one tool and `ddflow import --verify` is
    a flag, and neither registry knows the other's spelling. A tool name has no
    spaces, so only its first word counts, and is what is reported --
    `ddflow_cleanup with apply=true` names `ddflow_cleanup`. ` ` is a CLI path, looked
    up in argparse.
    """
    tools = {t.removeprefix("ddflow_") for t in TOOLS}
    names = [m.split()[0] for sep, m in mentions if sep == "_"]
    bad = [f"ddflow_{name}" for name in names if name not in tools]
    return bad + unknown_cli_mentions([m for sep, m in mentions if sep == " "], leaves)


def test_a_hyphenated_command_is_judged_as_written():
    """`hooks check-msg` exists; `hooks check` does not. The capture stopped at the
    hyphen, and an escape that accepted any leaf extending the truncated word with
    `-` then passed a literal `ddflow hooks check` (critic, Bf3819cdb35)."""
    assert unknown_mentions(mentions("run `ddflow hooks check` now")) == ["hooks check"]
    assert unknown_mentions(mentions('add `ddflow hooks check-msg "$1"` there')) == []
    # A flag after a command is not part of it.
    assert unknown_mentions(mentions("`ddflow cleanup --apply`")) == []


def test_a_missing_tool_is_reported_by_its_name_alone():
    """Only the first word of a `ddflow_` mention is checked, so only it is reported:
    one missing tool is one entry, however the sentence around it continues."""
    assert unknown_mentions(mentions("`ddflow_nosuch with apply=true`")) == ["ddflow_nosuch"]


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
    assert unmapped_tools(TOOLS) == [], (
        f"these tools belong to no group, so `ddflow help` files them under "
        f"'Unmapped': {unmapped_tools(TOOLS)}. Add a prefix to `_GROUPS`."
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
    unknown: list[tuple[str, str]] = []
    pages = {"index": H.render_index(tools=TOOLS), **{t: H.render_topic(t) for t in H.TOPICS}}
    for page, text in pages.items():
        unknown.extend((page, m) for m in unknown_mentions(mentions(text), leaves))
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
    import json as _json

    from ddflow.surfaces.mcp import Server

    assert "ddflow_help" in TOOLS

    def call(arguments):
        reply = Server(repo).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_help", "arguments": arguments},
            }
        )["result"]
        assert reply["isError"] is False, reply
        return _json.loads(reply["content"][0]["text"])

    # By CALLING it. This compared two argv lists, which checks the dispatch mechanism and
    # broke with `KeyError: 'argv'` the moment the tool went typed — having never once
    # verified that `help` produces help, or that a topic changes the answer.
    overview = call({})
    assert overview["topic"] == "index"
    assert overview["text"].strip(), "the overview is empty"
    assert "gates" in overview["topics"], overview["topics"]

    topic = call({"topic": "gates"})
    assert topic["topic"] == "gates"
    assert topic["text"] != overview["text"], "the topic returned the overview"
