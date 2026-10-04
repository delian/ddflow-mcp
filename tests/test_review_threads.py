"""Review threads as data: read them, reply on one, resolve it (B176).

Feedback used to reach an agent as text only (bodies and line comments, clipped), so a
reviewer could never see which comments had been addressed. A thread is now a record with
the forge's own id, readable live and answerable from the agent.
"""

# ruff: noqa: F811  (fixtures imported from test_flow)
from __future__ import annotations

import json

from conftest import run_cli
from test_flow import AUTHOR, _work, pr_repo  # noqa: F401

from ddflow.api import flow as A


def _in_review(pr_repo):
    repo, forge, _remote = pr_repo
    _work(repo, "T1", "a.py", "a\n")
    assert run_cli(repo, "merge", "T1", "--model", AUTHOR)[0] == 0
    forge.add_thread(1, "PRRT_a", "rename this", path="a.py", line=3)
    forge.add_thread(1, "PRRT_b", "this leaks", path="a.py", line=9, resolved=True)
    return repo, forge


def test_threads_are_listed_with_their_forge_ids(pr_repo):
    repo, _forge = _in_review(pr_repo)
    out = A.pr_threads(repo, "T1")
    assert out.exit == 0, out.reason
    rows = {t["id"]: t for t in out.data["threads"]}
    assert rows["PRRT_a"]["body"] == "rename this" and rows["PRRT_a"]["line"] == 3
    assert rows["PRRT_a"]["resolved"] is False and rows["PRRT_b"]["resolved"] is True
    assert rows["PRRT_a"]["author"] == "alice" and rows["PRRT_a"]["path"] == "a.py"
    assert out.data["unresolved"] == 1


def test_reply_and_resolve_reach_the_forge(pr_repo):
    repo, forge = _in_review(pr_repo)
    out = A.pr_threads(repo, "T1", thread="PRRT_a", reply="renamed in abc123", resolve=True)
    assert out.exit == 0, out.reason
    assert (out.data["replied"], out.data["resolved"]) == (True, True)
    thread = forge.thread(1, "PRRT_a")
    assert thread["resolved"] is True
    assert thread["comments"][-1]["body"] == "renamed in abc123"
    after = {t["id"]: t for t in out.data["threads"]}
    assert after["PRRT_a"]["resolved"] is True and after["PRRT_a"]["replies"] == 1
    assert out.data["unresolved"] == 0, "the result must be read back, not assumed"


def test_a_reply_alone_leaves_the_thread_open(pr_repo):
    repo, forge = _in_review(pr_repo)
    out = A.pr_threads(repo, "T1", thread="PRRT_a", reply="working on it")
    assert out.exit == 0 and out.data["resolved"] is False
    assert forge.thread(1, "PRRT_a")["resolved"] is False


def test_refusals_say_why(pr_repo):
    repo, forge = _in_review(pr_repo)
    unknown = A.pr_threads(repo, "T1", thread="PRRT_zzz", reply="x")
    assert unknown.exit == 3 and "PRRT_a" in unknown.reason, unknown.reason
    assert A.pr_threads(repo, "T1", reply="no thread named").exit == 3
    assert A.pr_threads(repo, "T1", resolve=True).exit == 3
    assert A.pr_threads(repo, "NOPE").exit == 3
    assert not forge.thread(1, "PRRT_a")["resolved"], "a refused call must change nothing"


def test_an_unreachable_forge_is_exit_2_not_no_threads(pr_repo):
    repo, forge = _in_review(pr_repo)
    forge.set(offline=True)
    out = A.pr_threads(repo, "T1")
    assert out.exit == 2, (out.exit, out.reason)


# -- GitLab: the same three verbs over its discussions API --------------------------------


def _gitlab(tmp_path, monkeypatch):
    import fakeglab

    from ddflow.infra import forge as FG

    discussions = [
        {
            "id": "d1",
            "notes": [
                {
                    "body": "rename this",
                    "author": {"username": "alice"},
                    "resolvable": True,
                    "resolved": False,
                    "position": {"new_path": "a.py", "new_line": 3},
                }
            ],
        },
        {
            "id": "d2",
            "notes": [
                {"body": "plain comment", "author": {"username": "bob"}, "resolvable": False}
            ],
        },
        {"id": "d3", "notes": [{"body": "joined", "system": True, "resolvable": False}]},
    ]
    bindir, state = fakeglab.install(tmp_path, discussions)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setenv(fakeglab.STATE_ENV, str(state))
    return FG.GitLab(tmp_path), state


def test_gitlab_threads_are_the_resolvable_discussions(tmp_path, monkeypatch):
    forge, _state = _gitlab(tmp_path, monkeypatch)
    threads = forge.threads(1)
    assert [(t.id, t.path, t.line, t.resolved, t.author) for t in threads] == [
        ("d1", "a.py", 3, False, "alice")
    ], "plain comments and system notes are not threads"


def test_gitlab_reply_and_resolve(tmp_path, monkeypatch):
    forge, state = _gitlab(tmp_path, monkeypatch)
    forge.reply(1, "d1", "renamed in abc123")
    forge.resolve(1, "d1")
    after = forge.threads(1)[0]
    assert after.resolved is True and after.replies == 1
    saved = json.loads(state.read_text())
    assert saved["discussions"][0]["notes"][-1]["body"] == "renamed in abc123"


# -- through the CLI and the MCP twin -------------------------------------------------------


def test_the_cli_lists_replies_and_resolves(pr_repo):
    repo, forge = _in_review(pr_repo)
    code, out, err = run_cli(repo, "pr", "threads", "T1")
    assert code == 0, err
    assert "PRRT_a" in out and "OPEN" in out and "a.py:3" in out and "1 open of 2" in out, out
    code, out, err = run_cli(
        repo,
        "--json",
        "pr",
        "threads",
        "T1",
        "--thread",
        "PRRT_a",
        "--reply",
        "renamed in abc123",
        "--resolve",
    )
    assert code == 0, err
    body = json.loads(out)
    assert body["replied"] and body["resolved"] and body["unresolved"] == 0, body
    assert forge.thread(1, "PRRT_a")["resolved"] is True


def test_the_cli_refuses_with_exit_3_and_unreachable_is_2(pr_repo):
    repo, forge = _in_review(pr_repo)
    code, _out, err = run_cli(repo, "pr", "threads", "T1", "--resolve")
    assert code == 3 and "--thread" in err, err
    forge.set(offline=True)
    code, _out, _err = run_cli(repo, "pr", "threads", "T1")
    assert code == 2


def test_the_mcp_twin_replies_and_resolves(pr_repo):
    from ddflow.surfaces.mcp import Server

    repo, forge = _in_review(pr_repo)
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_pr_threads",
                "arguments": {"id": "T1", "thread": "PRRT_a", "reply": "done", "resolve": True},
            },
        }
    )
    assert not reply["result"].get("isError"), reply
    assert forge.thread(1, "PRRT_a")["resolved"] is True
    assert forge.thread(1, "PRRT_a")["comments"][-1]["body"] == "done"


def test_a_failure_after_the_reply_says_the_reply_was_posted(pr_repo, monkeypatch):
    """A reply is public and not idempotent: if the resolve then fails, the report must say
    the reply landed, not read as 'nothing happened' (so the agent does not post it twice)."""
    from ddflow.config import Config
    from ddflow.core.model import fold
    from ddflow.infra import forge as FG
    from ddflow.infra.log import EventLog
    from ddflow.services import flow as SF

    repo, _forge = _in_review(pr_repo)
    calls = []

    class Stub:
        def threads(self, number):
            return [
                FG.Thread(
                    id="PRRT_a",
                    path="a.py",
                    line=3,
                    resolved=False,
                    author="r",
                    body="x",
                    replies=0,
                )
            ]

        def reply(self, number, thread_id, body):
            calls.append(("reply", thread_id))

        def resolve(self, number, thread_id):
            raise FG.ForgeError("resolve failed: 502")

    monkeypatch.setattr(FG, "detect", lambda *a, **k: Stub())
    st = fold(EventLog(repo).read_all(), strict=False)
    rep = SF.review_threads(
        repo, Config.load(repo), st, "T1", thread="PRRT_a", reply="done", resolve=True
    )
    assert calls == [("reply", "PRRT_a")]
    assert rep.replied and not rep.resolved
    assert (
        "502" in rep.refused
        and "the reply was posted" in rep.refused
        and "do not repeat" in rep.refused
    )


def test_the_github_graphql_documents_are_brace_balanced():
    import ast

    from ddflow.infra import forge as FG

    docs = [FG._GH_THREADS_QUERY, FG._GH_REPLY_MUTATION, FG._GH_RESOLVE_MUTATION]
    for d in docs:
        assert d.count("{") == d.count("}") and "{" in d, d
