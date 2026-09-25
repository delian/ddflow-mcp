"""Who is calling — the question the MCP surface could not answer.

Every attribution in the queue rests on agent identity: who holds a lease, who ran a
gate, and whether a review was performed by someone other than the author. Identity was
derived from the working tree, which is exactly right for one agent per worktree.

It is wrong, silently, for the case this tool exists to serve. Several agents or
subagents working ONE tree each spawn their own stdio server, every one resolves the
same cwd to the same identity, and their events merge into a single indistinguishable
stream. Nothing errors. `brief` then reports a sibling's item as "what you were doing",
and reviewer-independence compares an agent against itself and is satisfied — a review
gate passing on the strength of the author reviewing their own work, which is the
vacuous-pass class at the level of a whole reviewer.

`log.py:96` already said so in a docstring: *"a harness running several agents inside
ONE tree must set it — there is no signal that can distinguish them otherwise."* There
was no way to set it over MCP: the env var is process-wide, and the process is shared
with nothing that varies per connection. So it is DECLARED, and these tests are about
the declaration being real rather than decorative.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import TOOLS, Server


def _call(srv: Server, name: str, **args) -> dict:
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    assert reply is not None
    return reply["result"]


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _agents_in_log(repo: Path) -> set[str]:
    return {e.agent for e in EventLog(repo).read_all()}


# -- the declaration ---------------------------------------------------------------


def test_identify_is_reachable_as_a_tool(repo):
    """A capability an agent cannot discover is one that does not exist. It has to be
    in `tools/list` with a description that says WHEN to reach for it."""
    assert "ddflow_identify" in TOOLS
    desc = TOOLS["ddflow_identify"]["description"]
    assert "more than one agent" in desc or "subagent" in desc
    assert len(desc) > 40


def test_declaring_an_identity_reports_back_which_one_took_effect(repo):
    run_cli(repo, "init")
    srv = Server(repo)
    out = _text(_call(srv, "ddflow_identify", agent="reviewer-2"))
    assert "reviewer-2" in out
    assert srv.agent == "reviewer-2"


def test_not_declaring_one_says_SO_rather_than_reporting_a_name(repo):
    """The derived default is not wrong — it is UNDECLARED, and an agent that cannot
    tell the difference cannot know it is sharing an identity with its siblings."""
    run_cli(repo, "init")
    srv = Server(repo)
    out = _text(_call(srv, "ddflow_identify"))
    assert "not declared" in out or "default" in out


def test_a_name_that_could_not_be_a_shard_filename_is_refused_at_declaration(repo):
    """It becomes a log shard filename. A name with a path separator writes outside the
    events directory; one with a newline corrupts a line-oriented log. Refusing here
    means the caller reads the reason; refusing at the first write means it already
    believes it is identified."""
    run_cli(repo, "init")
    srv = Server(repo)
    for bad in ("../escape", "has space", "new\nline", "a" * 65, "sub/dir"):
        result = _call(srv, "ddflow_identify", agent=bad)
        assert result.get("isError"), f"{bad!r} was accepted as an agent name"
        assert srv.agent == "", "a refused name was stored anyway"


# -- and it has to reach the log ----------------------------------------------------


def test_two_connections_in_ONE_tree_write_as_two_different_agents(repo):
    """The whole point. Same repo, same cwd, same process-derived default — and the
    events must still be told apart."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")

    a, b = Server(repo), Server(repo)
    _call(a, "ddflow_identify", agent="agent-a")
    _call(b, "ddflow_identify", agent="agent-b")
    _call(a, "ddflow_claim", id="T1")
    _call(b, "ddflow_claim", id="T2")

    st = fold(EventLog(repo).read_all(), strict=False)
    holders = {i: st.items[i].lease.holder for i in ("T1", "T2")}
    assert holders["T1"] == "agent-a", holders
    assert holders["T2"] == "agent-b", holders
    assert holders["T1"] != holders["T2"], "two agents in one tree collapsed into one"


def test_the_typed_path_carries_the_identity_too(repo):
    """Two dispatch paths means two chances to drop it, and a silently dropped identity
    is indistinguishable from a correct one: the event is attributed to the process
    default and looks entirely plausible."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    srv = Server(repo)
    _call(srv, "ddflow_identify", agent="typed-writer")
    _call(srv, "ddflow_update", id="T1", title="renamed")

    authors = {e.agent for e in EventLog(repo).read_all() if e.kind.endswith(".updated")}
    assert authors == {"typed-writer"}, f"the typed path wrote as {authors}"


def test_an_undeclared_connection_still_works_exactly_as_before(repo):
    """The default must not change. Every existing single-agent deployment relies on
    the tree-derived identity, and breaking it to fix a multi-agent case would trade
    one silent wrong answer for a loud wrong one."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    before = _agents_in_log(repo)
    _call(Server(repo), "ddflow_claim", id="T1")
    assert _agents_in_log(repo) == before, "an undeclared connection invented a new identity"


def test_identity_persists_across_calls_on_one_connection(repo):
    """Declared once, not once per call — a per-call declaration would be forgotten
    exactly when a long session stopped thinking about it."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")
    srv = Server(repo)
    _call(srv, "ddflow_identify", agent="persistent")
    _call(srv, "ddflow_claim", id="T1")
    _call(srv, "ddflow_claim", id="T2")
    st = fold(EventLog(repo).read_all(), strict=False)
    assert {st.items[i].lease.holder for i in ("T1", "T2")} == {"persistent"}


def test_redeclaring_corrects_the_identity_rather_than_erroring(repo):
    """An agent that picks a name and then learns a better one should be able to say
    so. Idempotent, as the description promises."""
    run_cli(repo, "init")
    srv = Server(repo)
    _call(srv, "ddflow_identify", agent="first")
    _call(srv, "ddflow_identify", agent="second")
    assert srv.agent == "second"


# -- the handshake has to mention it -------------------------------------------------


def test_initialize_records_the_client_label_without_using_it_as_an_identity(repo):
    """`clientInfo.name` is the harness, not the agent: every subagent of one harness
    reports the same string. Using it as an identity would reproduce the exact collapse
    this file is about, while looking specific enough that nobody checked."""
    run_cli(repo, "init")
    srv = Server(repo)
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "some-harness", "version": "1.0"},
            },
        }
    )
    assert srv.client_info.get("name") == "some-harness"
    assert srv.agent == "", "the client label was promoted to an identity"


def test_a_malformed_clientInfo_does_not_break_the_handshake(repo):
    """Untrusted input from the other end of a pipe. A client sending a string where
    the spec says object must not take the server down before it serves one tool."""
    run_cli(repo, "init")
    srv = Server(repo)
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "clientInfo": "not-an-object"},
        }
    )
    assert reply is not None and "result" in reply
    assert srv.client_info == {}


# -- the four layers, and the two paths that have to agree on them ---------------------


def test_both_dispatch_paths_honour_DDFLOW_AGENT_on_an_UNDECLARED_connection(repo, monkeypatch):
    """The split the first version of this feature shipped.

    Identity was threaded through both paths only for a DECLARED name. Undeclared, the
    argv path went through `cli.Ctx`, which resolves `--agent` → `DDFLOW_AGENT` →
    `[agent].id` → tree; the typed path called `EventLog(repo, "")`, which falls
    straight to the tree-derived default and reads neither the env var nor the config.

    So with `DDFLOW_AGENT` set — which the demo harnesses do — `ddflow_claim` wrote as
    `alpha` and `ddflow_update` wrote as the directory name, on the same connection,
    into different shards. Two encodings of one precedence, which is the
    duplicate-then-drift shape; there is now one, in `infra.log.effective_agent_id`.

    *Found by roborev on 031313a, CONFIRMED, reproduced before this test was written.*
    """
    monkeypatch.setenv("DDFLOW_AGENT", "alpha")
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")

    srv = Server(repo)  # deliberately NOT identified
    _call(srv, "ddflow_claim", id="T1")  # argv path
    _call(srv, "ddflow_update", id="T1", title="renamed")  # typed path

    by_kind = {
        e.kind: e.agent
        for e in EventLog(repo).read_all()
        if e.kind in ("lease.acquired", "task.updated")
    }
    assert by_kind.get("lease.acquired") == "alpha", by_kind
    assert by_kind.get("task.updated") == "alpha", (
        f"the typed path ignored DDFLOW_AGENT and wrote as "
        f"{by_kind.get('task.updated')!r}: {by_kind}"
    )


def test_identify_reports_the_identity_actually_in_force_not_the_tree_name(repo, monkeypatch):
    """The tool whose job is to make identity visible must not misreport it.

    With `DDFLOW_AGENT=alpha` it answered with the tree-derived name — so an agent
    checking who it was got a different answer from the one its own writes carried.
    """
    monkeypatch.setenv("DDFLOW_AGENT", "alpha")
    run_cli(repo, "init")
    out = _text(_call(Server(repo), "ddflow_identify"))
    assert "alpha" in out, out


def test_identify_names_WHERE_the_default_came_from(repo, monkeypatch):
    """ "You are alpha because something set DDFLOW_AGENT" and "you are alpha because
    that is this directory's name" call for different reactions — the second is a guess
    every sibling in the tree makes identically."""
    run_cli(repo, "init")
    monkeypatch.setenv("DDFLOW_AGENT", "alpha")
    assert "DDFLOW_AGENT" in _text(_call(Server(repo), "ddflow_identify"))
    monkeypatch.delenv("DDFLOW_AGENT")
    out = _text(_call(Server(repo), "ddflow_identify"))
    assert "working tree" in out
    assert "several" in out, "the tree-derived case does not warn about siblings"


def test_a_declared_name_still_beats_the_environment(repo, monkeypatch):
    """Innermost wins, as the README promises. An env var set by a harness must not
    override an agent that has said who it is."""
    monkeypatch.setenv("DDFLOW_AGENT", "alpha")
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    srv = Server(repo)
    _call(srv, "ddflow_identify", agent="declared-one")
    _call(srv, "ddflow_claim", id="T1")
    _call(srv, "ddflow_update", id="T1", title="x")
    # Only what THIS CONNECTION wrote. The `task add` above is a separate CLI process
    # that legitimately ran as `alpha`, and sweeping it in would make the assertion
    # about the fixture rather than about precedence.
    agents = {
        e.agent for e in EventLog(repo).read_all() if e.kind in ("lease.acquired", "task.updated")
    }
    assert agents == {"declared-one"}, agents
