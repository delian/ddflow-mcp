"""The commit-msg hook's 'merge in progress' probe: a git that cannot answer is not a merge.

`G.run` reports a timeout or a missing git as a result (code -1), not an exception. Read as
"not an ancestor" (-1 != 0) it exempted an ordinary commit from the required Item trailer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ddflow.api import setup as S
from ddflow.config import Config
from ddflow.infra import git as G


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "a@b"], ["config", "user.name", "n"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, stdin=subprocess.DEVNULL)
    (repo / "f").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "m"], check=True)
    return repo


def test_a_merge_base_that_could_not_run_does_not_exempt_the_commit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    cfg = Config()
    cfg.enforce.require_item_trailer = True
    msg = tmp_path / "msg"
    msg.write_text("no trailer\n")
    real = G.run

    def fake(where, *args, **kw):
        if args[:1] == ("rev-parse",) and "MERGE_HEAD" in args:
            return G.GitResult(0, "a" * 40, "")
        if args[:1] == ("merge-base",):
            return G.GitResult(G.UNAVAILABLE, "", "timed out", unavailable=True, timed_out=True)
        return real(where, *args, **kw)

    monkeypatch.setattr(G, "run", fake)
    monkeypatch.chdir(repo)
    out = S._check_msg(repo, cfg, str(msg))
    assert out.exit != 0, "a merge-base that timed out read as 'a merge is in progress'"
