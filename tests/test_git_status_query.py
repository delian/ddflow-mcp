"""One meaning per git query (D-unify, B-uni-git-queries.2-paths): the file listing, the
parsed porcelain status and the unmerged paths. Each answers None when git could not say."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import git_raw as _git

from ddflow.core import bookkeeping as BK
from ddflow.infra import git as G
from ddflow.infra import worktree as W
from ddflow.services import enforce, ports
from ddflow.services import gates as GT
from ddflow.services.gates import evidence as EV


@pytest.fixture
def work(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.org")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _commit(repo: Path, **files: str) -> None:
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c")


def _wt(repo: Path) -> W.Worktree:
    return W.Worktree(item="x", path=repo, branch="main", base="main")


# -- status ------------------------------------------------------------------------------


def test_status_names_every_kind_of_entry(work):
    _commit(work, **{"keep.txt": "a\n", "old name.txt": "1\n2\n3\n4\n5\n6\n"})
    (work / "keep.txt").write_text("b\n")
    _git(work, "mv", "old name.txt", "new name.txt")
    (work / "café.txt").write_text("n\n")
    got = {e.path: e for e in G.status(work, untracked="all")}
    assert got["keep.txt"].xy == " M"
    assert got["café.txt"].untracked and got["café.txt"].orig == ""
    renamed = got["new name.txt"]
    assert renamed.xy[0] == "R" and renamed.orig == "old name.txt"
    assert renamed.paths == ("new name.txt", "old name.txt")
    assert renamed.line() == "R  old name.txt -> new name.txt"
    assert got["keep.txt"].line() == " M keep.txt"


def test_status_keeps_the_first_entrys_leading_column(work):
    """The text runner strips its output, which ate the first line's leading space."""
    _commit(work, **{"a.txt": "a\n"})
    (work / "a.txt").write_text("b\n")
    assert [e.xy for e in G.status(work)] == [" M"]
    assert W.dirty(_wt(work)) == [" M a.txt"]


def test_status_modes_and_pathspec(work):
    _commit(work, **{"a.txt": "a\n"})
    (work / "a.txt").write_text("b\n")
    (work / "new.txt").write_text("n\n")
    assert [e.path for e in G.status(work, untracked="no")] == ["a.txt"]
    assert [e.path for e in G.status(work, "new.txt")] == ["new.txt"]
    assert W.dirty(_wt(work), untracked=False) == [" M a.txt"]


def test_status_is_none_when_git_cannot_say(tmp_path):
    assert G.status(tmp_path) is None
    assert G.unmerged(tmp_path) is None
    assert G.files(tmp_path, "all") is None
    lines = W.dirty(_wt(tmp_path))
    assert W.unreadable(lines) and "git status failed" in lines[0]


def test_unmerged_entries_are_flagged(work):
    _commit(work, **{"f.txt": "base\n"})
    _git(work, "checkout", "-qb", "side")
    _commit(work, **{"f.txt": "side\n"})
    _git(work, "checkout", "-q", "main")
    _commit(work, **{"f.txt": "main\n"})
    subprocess.run(["git", "-C", str(work), "merge", "side"], capture_output=True)
    assert [e.path for e in G.status(work) if e.unmerged] == ["f.txt"]
    assert G.unmerged(work) == ["f.txt"]
    assert W.merging(work) is True


def test_unmerged_is_empty_on_a_clean_tree(work):
    _commit(work, **{"f.txt": "x\n"})
    assert G.unmerged(work) == []


# -- files -------------------------------------------------------------------------------


def test_files_kinds(work):
    _commit(work, **{"tracked.txt": "t\n"})
    (work / "loose.txt").write_text("l\n")
    (work / ".gitignore").write_text("junk\n")
    (work / "junk").write_text("j\n")
    assert G.files(work, "tracked") == ["tracked.txt"]
    assert G.files(work, "untracked") == [".gitignore", "loose.txt"]
    assert G.files(work, "all") == [".gitignore", "loose.txt", "tracked.txt"]
    with pytest.raises(ValueError):
        G.files(work, "bogus")


def test_files_reads_exact_names_and_honours_exclude(work):
    (work / ".ddflow").mkdir()
    (work / ".ddflow" / "state").write_text("s\n")
    (work / "café x.txt").write_text("c\n")
    assert G.files(work, "untracked") == [".ddflow/state", "café x.txt"]
    assert G.files(work, "untracked", exclude=BK.STATE_EXCLUDE) == ["café x.txt"]
    assert G.files(work, "untracked", pathspec=("café x.txt",)) == ["café x.txt"]


def test_an_untracked_non_ascii_files_edit_moves_the_fingerprint(work):
    """Mutant: reading the untracked listing without -z. git C-quotes the name, the quoted
    spelling names no file, `hash-object` fails, and the fingerprint fell back to names."""
    _commit(work, **{"a.txt": "a\n"})
    (work / "café.txt").write_text("one\n")
    first = GT.tree_fingerprint(work)
    (work / "café.txt").write_text("two\n")
    assert GT.tree_fingerprint(work) != first


def test_untracked_files_git_cannot_list_are_not_the_same_tree_as_none(work, monkeypatch):
    _commit(work, **{"a.txt": "a\n"})
    clean = GT.tree_fingerprint(work)
    monkeypatch.setattr(EV.GIT, "files", lambda *a, **k: None)
    assert EV._untracked_digest(work) == EV.UNLISTED
    assert GT.tree_fingerprint(work) != clean
    assert EV._untracked_paths(work) == []


# -- one name per bookkeeping meaning ---------------------------------------------------


def test_each_bookkeeping_set_is_the_one_in_core():
    assert BK.STATE_EXCLUDE == (":(exclude).ddflow", ":(exclude).ddflow/**")
    assert EV.FINGERPRINT_EXCLUDE is BK.STATE_EXCLUDE
    assert enforce.SELF_MANAGED is BK.SELF_MANAGED
    assert BK.SELF_MANAGED == (
        ".ddflow/",
        "docs/ddflow/",
        "AGENTS.md",
        "CLAUDE.md",
        ".cursor/rules/",
    )
    assert BK.QUEUE_STATE == (".ddflow/events/", ".ddflow/local/", ".ddflow/index.db")
    assert BK.EVENTS_EXCLUDE == (":(exclude).ddflow/events",)
    assert EV.is_state is BK.is_state


@pytest.mark.parametrize(
    "path,ours",
    [
        (".ddflow", True),
        (".ddflow/events/a.jsonl", True),
        (".ddflowx/a", False),
        ("sub/.ddflow/a", False),
        ("docs/ddflow/x.md", False),
    ],
)
def test_is_state(path, ours):
    assert BK.is_state(path) is ours


def test_no_module_spells_a_bookkeeping_pathspec_itself():
    root = Path(__file__).resolve().parent.parent / "ddflow"
    spelled = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p.name != "bookkeeping.py" and "(exclude).ddflow" in p.read_text("utf-8")
    ]
    assert spelled == []


# -- a half-listed merge is never clean --------------------------------------------------


def test_a_port_whose_unmerged_paths_are_unknown_is_not_applied(monkeypatch):
    src = SimpleNamespace(id="S", landed_before="a", landed_after="b", merged_sha="b")
    ok = G.GitResult(0, "diff --git a/x b/x\n+y", "")
    monkeypatch.setattr(ports.W, "git", lambda *a, **k: ok)
    monkeypatch.setattr(ports.W, "apply_3way", lambda *a, **k: G.GitResult(0, "", ""))
    monkeypatch.setattr(ports.GIT, "unmerged", lambda *a, **k: None)
    out: dict = {}
    ports._cherry_pick(Path("."), "I", src, out)
    assert out["status"] == ports.FAILED and "could not list" in out["reason"]


def test_a_forward_merge_whose_unmerged_paths_are_unknown_is_aborted_not_called_clean(monkeypatch):
    src = SimpleNamespace(id="S", port_of="", landed_after="b", merged_sha="b")
    cfg = SimpleNamespace(flow=SimpleNamespace(integration="direct", remote="origin"))
    calls: list[tuple] = []

    def fake_git(tree, *args, **kw):
        calls.append(args)
        return G.GitResult(0, "", "")

    monkeypatch.setattr(ports.FS, "target", lambda *a, **k: "main")
    monkeypatch.setattr(ports.W, "git", fake_git)
    monkeypatch.setattr(ports.GIT, "unmerged", lambda *a, **k: None)
    out: dict = {}
    ports._forward_merge(Path("."), cfg, None, Path("."), "I", src, out)
    assert out["status"] == ports.FAILED and ("merge", "--abort") in calls


def test_diff_stat_counts_untracked_files(work):
    _commit(work, **{"a.txt": "a\n"})
    (work / "n1.txt").write_text("1\n2\n")
    (work / "café.txt").write_text("c\n")
    stat = EV.diff_stat(work)
    assert stat["untracked"] == 2 and stat["files"] == 2 and stat["insertions"] == 3


# -- the remaining porcelain readers ---------------------------------------------------


def test_ignored_files_are_listed_only_when_asked(work):
    _commit(work, **{".gitignore": "junk\n", "a.txt": "a\n"})
    (work / "junk").write_text("j\n")
    assert G.status(work) == []
    got = G.status(work, ignored="matching")
    assert [(e.path, e.ignored) for e in got] == [("junk", True)]


def test_event_shards_with_changes_are_named_exactly(work):
    from ddflow.services import eventcommit

    ev = work / ".ddflow" / "events"
    ev.mkdir(parents=True)
    (ev / "a.jsonl").write_text("1\n")
    _commit(work, **{"x.txt": "x\n"})
    (ev / "a.jsonl").write_text("1\n2\n")  # modified: " M", the first entry's leading space
    (ev / "café b.jsonl").write_text("3\n")
    (ev / "note.txt").write_text("n\n")
    assert eventcommit.uncommitted_shards(work) == [
        ".ddflow/events/a.jsonl",
        ".ddflow/events/café b.jsonl",
    ]


def test_event_shards_are_unknown_when_git_cannot_say(tmp_path):
    from ddflow.services import eventcommit

    assert eventcommit.uncommitted_shards(tmp_path) is None


def test_tree_work_separates_work_from_ignored_and_skips_caches(work):
    from ddflow.infra import worktree as W

    _commit(work, **{".gitignore": "keep.env\n__pycache__/\n", "a.txt": "a\n"})
    (work / "a.txt").write_text("b\n")
    (work / "keep.env").write_text("secret\n")
    (work / "__pycache__").mkdir()
    (work / "__pycache__" / "m.pyc").write_text("c\n")
    tw = W.tree_work(work)
    assert tw.readable and tw.work == ["a.txt"] and tw.ignored == ["keep.env"]
    assert W.tree_work(work / "nope").readable is False


def test_a_snapshot_backup_refuses_a_tree_whose_status_git_cannot_read(work, monkeypatch):
    from ddflow.services import backups

    _commit(work, **{"a.txt": "a\n"})
    backups._require_clean(work)  # a clean tree passes
    monkeypatch.setattr(backups.git, "status", lambda *a, **k: None)
    with pytest.raises(backups.SnapshotRefused):
        backups._require_clean(work)


def test_a_status_may_run_as_long_as_any_other_git_call():
    """Mutant: the listing timeout (60 s) on a call that used to get the git default (300 s)."""
    import inspect

    for fn in (G.status_run, G.status):
        assert inspect.signature(fn).parameters["timeout"].default == G.GIT_TIMEOUT


def test_ignored_matching_names_the_ignored_file_not_its_directory(work):
    """Mutant: `--ignored` without `=matching` (git then reports `dir/`, hiding which file)."""
    _commit(work, **{".gitignore": "*.log\n", "a.txt": "a\n"})
    (work / "dir").mkdir()
    (work / "dir" / "a.log").write_text("l\n")
    assert [e.path for e in G.status(work, ignored="matching")] == ["dir/a.log"]
    assert [e.path for e in G.status(work, ignored="traditional")] == ["dir/"]


def test_a_pathspec_that_looks_like_an_option_is_a_path(work):
    """Mutant: dropping the `--` before the pathspec (version_files and setup rely on it)."""
    _commit(work, **{"a.txt": "a\n"})
    (work / "-uno").write_text("x\n")
    (work / "b.txt").write_text("y\n")
    assert [e.path for e in G.status(work, "-uno")] == ["-uno"]
    assert [e.path for e in G.status(work, "a.txt")] == []
    assert G.parse_status(G.status_run(work, "-uno"))[0].path == "-uno"
    assert W.status is G.status
