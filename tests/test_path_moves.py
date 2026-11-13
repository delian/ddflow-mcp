"""A merge records each rename, move and package split as a `path.moved` record (MV-path-moves).

The detector is `services.drift.moves_between`: git's own ``diff --name-status -M`` for a
rename (a plain move, a move plus edit, one edge of a many-into-one merge), plus the shape of
a package split (``X`` deleted, ``X/__init__.py`` added) git's content link can miss. Nothing is
stored outside the log: each move is a self-contained `path.moved` event that the fold projects
into ``State.moves``, so replay rebuilds the list from history alone.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services.drift import moves_between


def _git(repo: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return p.returncode, p.stdout


def _commit(repo: Path, _msg: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", _msg], check=True)


def _sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _claimed(repo: Path, item: str, glob: str, agent: str) -> None:
    assert run_cli(repo, "init", agent=agent)[0] == 0
    code, out, err = run_cli(repo, "task", "add", item, "--title", "t", "--globs", glob, agent=agent)
    assert code == 0, out + err
    (repo / "ddflow").mkdir(exist_ok=True)
    (repo / glob).write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", glob], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
    code, out, err = run_cli(repo, "claim", item, agent=agent)
    assert code == 0, out + err


def test_moves_between_detects_a_rename(repo):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("def f():\n    return 1\n")
    _commit(repo, "seed")
    before = _sha(repo)
    subprocess.run(["git", "-C", str(repo), "mv", "src/a.py", "src/b.py"], check=True)
    _commit(repo, "move")
    after = _sha(repo)
    moves = moves_between(repo, before, after)
    assert moves is not None
    news = [m.new for m in moves]
    assert "src/b.py" in news, moves


def test_moves_between_detects_a_package_split(repo):
    (repo / "mod.py").write_text("def g():\n    return 2\n")
    _commit(repo, "seed")
    before = _sha(repo)
    (repo / "modpkg").mkdir()
    (repo / "modpkg" / "__init__.py").write_text("all new content, nothing like before\n")
    (repo / "mod.py").unlink()
    _commit(repo, "split")
    after = _sha(repo)
    moves = moves_between(repo, before, after)
    assert moves is not None
    olds = [m.old for m in moves]
    assert any(m.old == "mod.py" and "modpkg/__init__.py" in m.new for m in moves), moves


def test_merge_records_a_path_moved_event(repo):
    _claimed(repo, "T1", "src/a.py", "agent-a")
    wt = repo / ".ddflow/worktrees/T1"
    subprocess.run(["git", "-C", str(wt), "mv", "src/a.py", "src/b.py"], check=True)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-qm", "move"], check=True)
    code, out, err = run_cli(repo, "merge", "T1", agent="agent-a")
    assert code == 0, (code, out, err)
    st = fold(EventLog(repo, "reader").read_all(), strict=False)
    assert st.moves, "no path.moved recorded"
    assert any(m.get("new") == "src/b.py" for m in st.moves), st.moves
