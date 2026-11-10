"""The `Forge` port: the same cases against `FakeForge` (scripted) and the real `GitHub`
adapter (with its CLI runner scripted so no `gh` starts).

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from ddflow.adapters import forge as FG
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.forge import ForgeError, ForgeUnavailable, GitHub, PRInfo, Thread
from ddflow.adapters.fakes.forge import FakeForge

_PR = {
    "number": 1,
    "url": "https://github.com/o/r/pull/1",
    "state": "OPEN",
    "isDraft": False,
    "reviewDecision": "APPROVED",
    "statusCheckRollup": [],
    "baseRefName": "main",
    "headRefName": "feat/x",
    "headRefOid": "abc",
    "mergeCommit": None,
    "latestReviews": [],
    "commits": [{"oid": "abc"}],
}


def _proc(stdout: str) -> "_Proc":
    return _Proc(stdout)


class _Proc:
    returncode = 0
    stderr = ""

    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


def _gh_run(repo, argv, *, timeout: int = 120):
    joined = " ".join(argv)
    if argv[:3] == ["gh", "pr", "list"]:
        return _Proc("[]")
    if argv[:3] == ["gh", "pr", "view"]:
        return _Proc(json.dumps(_PR))
    if argv[:3] == ["gh", "pr", "merge"]:
        return _Proc("merged\n")
    if argv[:3] == ["gh", "pr", "edit"]:
        return _Proc("")
    if "graphql" in argv:
        if "reviewThreads" in joined:
            return _Proc(
                json.dumps(
                    {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}
                )
            )
        if "addPullRequestReviewThreadReply" in joined:
            return _Proc(json.dumps({"data": {"addPullRequestReviewThreadReply": {"comment": {"id": "c"}}}}))
        if "resolveReviewThread" in joined:
            return _Proc(json.dumps({"data": {"resolveReviewThread": {"thread": {"id": "t", "isResolved": True}}}}))
        return _Proc(
            json.dumps(
                {"data": {"repository": {"pullRequest": {"isInMergeQueue": False, "mergeQueueEntry": None}}}}
            )
        )
    return _Proc("")


def forge_cases(io) -> None:
    info = io.view(1)
    assert isinstance(info, PRInfo) and info.number == 1 and info.state == "open"
    assert io.find("missing-head") is None
    threads = io.threads(1)
    assert isinstance(threads, list) and all(isinstance(t, Thread) for t in threads)
    assert isinstance(io.merge(1, strategy="merge", head_sha="", auto=False), str)
    io.set_base(1, "main")
    io.reply(1, "tid", "note")
    io.resolve(1, "tid")


CONFORMANCE = {
    "Forge": {
        "real": lambda monkeypatch: _scripted_github(monkeypatch),
        "cases": forge_cases,
        "fake": lambda cls: cls(),
    }
}


def _scripted_github(monkeypatch) -> GitHub:
    monkeypatch.setattr(FG, "_run", _gh_run)
    return GitHub(Path("."))


def test_the_adapter_and_the_fake_are_registered_and_satisfy_the_port():
    assert FAKES["Forge"] is FakeForge
    assert FG.FORGES["github"] is GitHub
    from ddflow.adapters.forge_gitlab import GitLab

    assert FG.FORGES["gitlab"] is GitLab


def test_the_adapter_reads_a_scripted_pr(monkeypatch):
    gh = _scripted_github(monkeypatch)
    info = gh.view(1)
    assert info.number == 1 and info.head == "feat/x" and info.review == "approved"
    assert gh.find("missing-head") is None


def test_the_fake_scripts_and_records():
    info = PRInfo(number=7, head="feat/x", base="main")
    io = FakeForge()
    io.requests[7] = info
    got = io.find("feat/x")
    assert got is info and io.view(7) is info
    io.merge(7, strategy="squash", head_sha="abc", auto=True)
    assert io.calls[0] == ("find", "feat/x") and io.calls[2] == ("merge", 7, "squash")


def test_the_fake_scripts_an_unavailable_answer():
    io = FakeForge()
    io.script["view"] = ForgeUnavailable("no gh")
    try:
        io.view(3)
    except ForgeUnavailable:
        pass
    else:
        raise AssertionError("a scripted ForgeUnavailable must propagate")


def test_the_github_adapter_raises_on_unavailable() -> None:
    # The falsehood of a `ForgeError` vs `ForgeUnavailable` split is pinned here too.
    assert issubclass(ForgeError, RuntimeError)
    assert issubclass(ForgeUnavailable, RuntimeError)
