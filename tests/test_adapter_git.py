"""The `Git` port: the same cases against `FakeGit` (scripted) and the real module
(`ddflow.adapters.git`).

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

from ddflow.adapters import git as real_git
from ddflow.adapters.fakes.git import FakeGit


def git_cases(io) -> None:
    r = io.run(".", "rev-parse", "--show-toplevel")
    assert hasattr(r, "ok") and hasattr(r, "text")
    paths = io.paths(".", "--cached")
    assert paths is None or isinstance(paths, list)
    unmerged = io.unmerged(".")
    assert unmerged is None or isinstance(unmerged, list)


CONFORMANCE = {
    "Git": {
        "real": lambda monkeypatch: real_git,
        "cases": git_cases,
        "fake": lambda cls: cls(script=[{}]),
    }
}


def test_the_fake_scripts_and_records():
    io = FakeGit(script=[{"run": (False, "nope")}])
    got = io.run(".")
    assert got.ok is False and got.text == "nope"
    assert io.calls == [("run", (".",))]
