"""Export writer (B-export-write): whole / region / append modes, check, diff, safety."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from ddflow.services.export import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError, frame
from ddflow.services.export import write as W


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".ddflow").mkdir()
    return tmp_path


def doc(body: str = "# Roadmap\n\n- one\n", kind: str = "roadmap") -> str:
    return frame.frame(body, kind, "0.1.9")


# -- whole file ---------------------------------------------------------------------------


def test_whole_create_update_unchanged(repo):
    r = W.write_whole(repo, "ROADMAP.md", doc())
    assert r.action == "created" and (repo / "ROADMAP.md").read_text() == doc()
    assert W.write_whole(repo, "ROADMAP.md", doc()).action == "unchanged"
    r = W.write_whole(repo, "ROADMAP.md", doc("# Roadmap\n\n- two\n"))
    assert r.action == "updated" and "+- two" in r.diff and "-- one" in r.diff
    assert "- two" in (repo / "ROADMAP.md").read_text()


def test_whole_creates_missing_parent_directories(repo):
    W.write_whole(repo, "docs/out/ROADMAP.md", doc())
    assert (repo / "docs" / "out" / "ROADMAP.md").is_file()


def test_whole_check_exactness(repo):
    assert W.write_whole(repo, "R.md", doc(), check=True).code == 1  # absent
    assert not (repo / "R.md").exists()  # check never writes
    W.write_whole(repo, "R.md", doc())
    fresh = W.write_whole(repo, "R.md", doc(), check=True)
    assert (fresh.code, fresh.action) == (0, "fresh")
    stale = W.write_whole(repo, "R.md", doc("# Roadmap\n\n- changed\n"), check=True)
    assert (stale.code, stale.action) == (1, "stale") and "changed" in stale.diff
    (repo / "R.md").write_text((repo / "R.md").read_text().replace("one", "ONE"))  # hand edit
    assert W.write_whole(repo, "R.md", doc(), check=True).code == 1
    (repo / "R.md").write_text("# not ours\n")
    assert W.write_whole(repo, "R.md", doc(), check=True).code == 1


def test_whole_check_unreadable_is_could_not_run(repo):
    (repo / "R.md").write_bytes(b"\xff\xfe\x00 not utf-8")
    with pytest.raises(ExportError) as e:
        W.write_whole(repo, "R.md", doc(), check=True)
    assert e.value.code == EXIT_UNAVAILABLE


def test_whole_diff_writes_nothing(repo):
    W.write_whole(repo, "R.md", doc())
    before = (repo / "R.md").read_text()
    r = W.write_whole(repo, "R.md", doc("# Roadmap\n\n- new\n"), diff=True)
    assert r.action == "diff" and "+- new" in r.diff and (repo / "R.md").read_text() == before


def test_whole_refuses_a_hand_edit_with_the_diff_and_force_overrides(repo):
    W.write_whole(repo, "R.md", doc())
    p = repo / "R.md"
    p.write_text(p.read_text().replace("- one", "- one (my note)"))
    with pytest.raises(W.Refused) as e:
        W.write_whole(repo, "R.md", doc("# Roadmap\n\n- two\n"))
    assert e.value.code == EXIT_REFUSED and "edited by hand" in str(e.value)
    assert "my note" in e.value.diff and "my note" in p.read_text()  # untouched
    W.write_whole(repo, "R.md", doc("# Roadmap\n\n- two\n"), force=True)
    assert "my note" not in p.read_text()


def test_whole_never_overwrites_an_unmarked_file_without_force(repo):
    p = repo / "CHANGELOG.md"
    p.write_text("# Changelog\n\nhand written\n")
    with pytest.raises(W.Refused) as e:
        W.write_whole(repo, "CHANGELOG.md", doc(kind="changelog"))
    assert "no ddflow header" in str(e.value) and p.read_text().endswith("hand written\n")
    W.write_whole(repo, "CHANGELOG.md", doc(kind="changelog"), force=True)
    assert frame.split(p.read_text())[0] is not None


def test_whole_refuses_another_kinds_file(repo):
    W.write_whole(repo, "X.md", doc(kind="roadmap"))
    with pytest.raises(W.Refused, match="generated as 'roadmap'"):
        W.write_whole(repo, "X.md", doc(kind="status"))


def test_whole_keeps_file_mode(repo):
    p = repo / "R.md"
    W.write_whole(repo, "R.md", doc())
    p.chmod(0o640)
    W.write_whole(repo, "R.md", doc("# Roadmap\n\n- two\n"))
    assert p.stat().st_mode & 0o777 == 0o640


def test_whole_needs_a_framed_document(repo):
    with pytest.raises(ExportError):
        W.write_whole(repo, "R.md", "no header\n")


# -- marker regions ---------------------------------------------------------------------


PROSE_TOP = "# My README\n\nHand written intro.\n\n"
PROSE_BOTTOM = "\n## License\n\nMIT, written by hand.\n"


def _readme(repo, body="- a\n"):
    p = repo / "README.md"
    p.write_text(PROSE_TOP + W.region_text("status", body) + PROSE_BOTTOM)
    return p


def test_region_round_trip_keeps_prose_byte_for_byte(repo):
    p = _readme(repo)
    r = W.write_region(repo, "README.md", "status", "- b\n- c\n")
    text = p.read_text()
    assert r.action == "updated" and text.startswith(PROSE_TOP) and text.endswith(PROSE_BOTTOM)
    assert "- b\n- c\n" in text and "- a\n" not in text
    assert W.write_region(repo, "README.md", "status", "- b\n- c\n").action == "unchanged"
    # and the digest in the begin marker tracks the body, so the next write is clean
    W.write_region(repo, "README.md", "status", "- d\n")
    assert p.read_text().startswith(PROSE_TOP) and p.read_text().endswith(PROSE_BOTTOM)


def test_region_check_and_diff(repo):
    p = _readme(repo)
    before = p.read_text()
    assert W.write_region(repo, "README.md", "status", "- a\n", check=True).code == 0
    assert W.write_region(repo, "README.md", "status", "- z\n", check=True).code == 1
    d = W.write_region(repo, "README.md", "status", "- z\n", diff=True)
    assert "+- z" in d.diff and p.read_text() == before


def test_region_hand_edit_inside_is_refused(repo):
    p = _readme(repo)
    p.write_text(p.read_text().replace("- a", "- a (edited)"))
    with pytest.raises(W.Refused, match="edited by hand"):
        W.write_region(repo, "README.md", "status", "- b\n")
    W.write_region(repo, "README.md", "status", "- b\n", force=True)
    assert "(edited)" not in p.read_text() and p.read_text().endswith(PROSE_BOTTOM)


def test_region_text_outside_markers_is_never_a_hand_edit(repo):
    p = _readme(repo)
    p.write_text(p.read_text().replace("Hand written intro.", "Hand written intro, revised."))
    W.write_region(repo, "README.md", "status", "- b\n")  # no refusal
    assert "revised" in p.read_text()


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t + W.region_text("status", "dup\n"),  # duplicate region
        lambda t: t.replace("<!-- ddflow:end doc=status -->", ""),  # missing end
        lambda t: t.replace("<!-- ddflow:begin", "<!-- nope", 1),  # missing begin
        lambda t: t + "<!-- ddflow:end doc=status -->\n",  # extra end
    ],
)
def test_region_bad_markers_refuse_even_with_force(repo, mangle):
    p = _readme(repo)
    p.write_text(mangle(p.read_text()))
    before = p.read_text()
    for force in (False, True):
        with pytest.raises(W.Refused):
            W.write_region(repo, "README.md", "status", "- b\n", force=force)
    assert p.read_text() == before


def test_region_reversed_markers_refuse(repo):
    p = repo / "README.md"
    p.write_text(
        "<!-- ddflow:end doc=status -->\nx\n"
        + W.region_text("status", "y\n").split("\n", 1)[0]
        + "\n"
    )
    with pytest.raises(W.Refused, match="unbalanced or reversed"):
        W.write_region(repo, "README.md", "status", "z\n")


def test_region_missing_markers_refuse_but_force_appends(repo):
    p = repo / "README.md"
    p.write_text("# Plain\n\ntext\n")
    with pytest.raises(W.Refused, match="no ddflow region"):
        W.write_region(repo, "README.md", "status", "- a\n")
    W.write_region(repo, "README.md", "status", "- a\n", force=True)
    t = p.read_text()
    assert t.startswith("# Plain\n\ntext\n") and "- a\n" in t
    assert W.write_region(repo, "README.md", "status", "- a\n").action == "unchanged"


def test_region_creates_a_missing_file_and_other_docs_regions_are_independent(repo):
    W.write_region(repo, "OUT.md", "status", "- s\n")
    p = repo / "OUT.md"
    p.write_text(p.read_text() + "between\n" + W.region_text("roadmap", "- r\n"))
    W.write_region(repo, "OUT.md", "status", "- s2\n")
    W.write_region(repo, "OUT.md", "roadmap", "- r2\n")
    t = p.read_text()
    assert "- s2" in t and "- r2" in t and "between\n" in t


# -- append only ------------------------------------------------------------------------


def _produce(entries: str, last: str):
    return lambda _prev: (entries, last)


def test_append_creates_then_appends_without_rewriting_old_lines(repo):
    r = W.append_entries(repo, "LOG.md", "worklog", _produce("- e1\n- e2\n", "ev2"), version="1")
    assert r.action == "created"
    first = (repo / "LOG.md").read_text()
    assert W.last_exported(repo, "LOG.md") == "ev2"
    seen = []
    W.append_entries(
        repo, "LOG.md", "worklog", lambda last: (seen.append(last) or "- e3\n", "ev3"), version="1"
    )
    assert seen == ["ev2"]  # produce got the recorded last id
    second = (repo / "LOG.md").read_text()
    body1, body2 = frame.split(first)[1], frame.split(second)[1]
    assert body2 == body1 + "- e3\n" and body2.startswith("- e1\n- e2\n")
    assert frame.hand_edited(second) is False and W.last_exported(repo, "LOG.md") == "ev3"


def test_append_nothing_new_is_unchanged_and_check_fresh(repo):
    W.append_entries(repo, "LOG.md", "worklog", _produce("- e1\n", "ev1"), version="1")
    before = (repo / "LOG.md").read_text()
    assert W.append_entries(repo, "LOG.md", "worklog", _produce("", "ev1")).action == "unchanged"
    c = W.append_entries(repo, "LOG.md", "worklog", _produce("", "ev1"), check=True)
    assert (c.code, c.action) == (0, "fresh") and (repo / "LOG.md").read_text() == before
    c = W.append_entries(repo, "LOG.md", "worklog", _produce("- e2\n", "ev2"), check=True)
    assert (
        (c.code, c.action) == (1, "stale")
        and "+- e2" in c.diff
        and (repo / "LOG.md").read_text() == before
    )


def test_append_refuses_a_hand_edited_or_foreign_log(repo):
    W.append_entries(repo, "LOG.md", "worklog", _produce("- e1\n", "ev1"), version="1")
    p = repo / "LOG.md"
    p.write_text(p.read_text() + "my own line\n")
    with pytest.raises(W.Refused, match="edited by hand"):
        W.append_entries(repo, "LOG.md", "worklog", _produce("- e2\n", "ev2"))
    assert W.append_entries(repo, "LOG.md", "worklog", _produce("", "x"), check=True).code == 1
    q = repo / "OTHER.md"
    q.write_text("# someone's changelog\n- old\n")
    with pytest.raises(W.Refused, match="no ddflow header"):
        W.append_entries(repo, "OTHER.md", "worklog", _produce("- e\n", "ev"))
    assert q.read_text() == "# someone's changelog\n- old\n"
    W.append_entries(repo, "OTHER.md", "worklog", _produce("- e\n", "ev"), force=True, version="1")
    assert frame.split(q.read_text())[1] == "# someone's changelog\n- old\n- e\n"  # old lines kept


def test_append_registers_the_target_in_append_only_globs(repo):
    from ddflow.services.shared_files import committed_append_only

    r = W.append_entries(repo, "docs/LOG.md", "worklog", _produce("- e1\n", "ev1"), version="1")
    assert r.registered and committed_append_only(repo) == ["docs/LOG.md"]
    assert "docs/LOG.md merge=union" in (repo / ".gitattributes").read_text()
    r = W.append_entries(repo, "docs/LOG.md", "worklog", _produce("- e2\n", "ev2"), version="1")
    assert not r.registered and committed_append_only(repo) == ["docs/LOG.md"]  # once
    # check/diff never register
    W.append_entries(repo, "B.md", "worklog", _produce("- x\n", "e"), check=True)
    assert committed_append_only(repo) == ["docs/LOG.md"]


def test_concurrent_appenders_lose_nothing(repo):
    n = 24
    errs: list[BaseException] = []

    def go(i: int) -> None:
        try:
            W.append_entries(
                repo,
                "LOG.md",
                "worklog",
                lambda last: (f"- entry {i}\n", f"ev{i}"),
                version="1",
                register=False,
            )
        except BaseException as exc:
            errs.append(exc)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    text = (repo / "LOG.md").read_text()
    assert frame.hand_edited(text) is False
    body = frame.split(text)[1].splitlines()
    assert sorted(body) == sorted(f"- entry {i}" for i in range(n))  # each exactly once


def test_concurrent_whole_writers_and_a_hand_edit_never_interleave(repo):
    W.write_whole(repo, "R.md", doc("# Roadmap\n\n- v0\n"))
    p = repo / "R.md"
    outcomes: list[str] = []

    def writer(i: int) -> None:
        try:
            W.write_whole(repo, "R.md", doc(f"# Roadmap\n\n- v{i + 1}\n"))
            outcomes.append("ok")
        except W.Refused:
            outcomes.append("refused")

    ts = [threading.Thread(target=writer, args=(i,)) for i in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    text = p.read_text()
    assert outcomes.count("ok") == 16 and frame.hand_edited(text) is False  # whole, valid file
    assert not [f for f in os.listdir(repo) if f.endswith(".tmp")]  # no stray temp files


def test_hand_edit_check_and_write_share_one_critical_section(repo):
    """An edit that lands while a writer holds the lock is seen by that writer's check, or
    waits for it -- it is never overwritten unseen."""
    from ddflow.infra import tomlcfg

    W.write_whole(repo, "R.md", doc())
    p = repo / "R.md"
    started, release = threading.Event(), threading.Event()
    result: list[object] = []

    def slow_editor() -> None:
        with tomlcfg.locked(W._lock_path(repo)):  # a hand edit made while the lock is held
            started.set()
            release.wait(5)
            p.write_text(p.read_text().replace("one", "ONE (edited)"))

    t = threading.Thread(target=slow_editor)
    t.start()
    started.wait(5)

    def writer() -> None:
        try:
            W.write_whole(repo, "R.md", doc("# Roadmap\n\n- two\n"))
            result.append("wrote")
        except W.Refused:
            result.append("refused")

    w = threading.Thread(target=writer)
    w.start()
    time.sleep(0.3)  # let the writer reach the lock and block on it
    release.set()
    t.join()
    w.join()
    assert result == ["refused"] and "ONE (edited)" in p.read_text()


# -- path safety ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "\\evil",
        "C:/evil",
        "../outside.md",
        "a/../../outside.md",
        "docs/../../x.md",
        ".git/config",
        "sub/.git/hooks/x",
        ".ddflow/events/x.jsonl",
        ".DDFLOW/config.toml",
        ".Git/config",
        "",
        ".",
        "a\0b.md",
    ],
)
def test_path_refusals(repo, bad):
    with pytest.raises(W.Refused) as e:
        W.write_whole(repo, bad, doc())
    assert e.value.code == EXIT_REFUSED
    assert sorted(p.name for p in repo.iterdir()) == [".ddflow"]  # nothing created


def test_symlink_refusals(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "target.md").write_text("victim\n")
    os.symlink(outside / "target.md", repo / "LINK.md")  # the file itself
    os.symlink(outside, repo / "linkdir")  # a parent directory
    os.symlink(repo / "real.md", repo / "dangling.md")  # a dangling link
    for bad in ("LINK.md", "linkdir/x.md", "linkdir/target.md", "dangling.md"):
        with pytest.raises(W.Refused, match="symlink"):
            W.write_whole(repo, bad, doc())
        for fn in (
            lambda b=bad: W.write_region(repo, b, "k", "x\n"),
            lambda b=bad: W.append_entries(repo, b, "k", _produce("x\n", "e")),
        ):
            with pytest.raises(W.Refused):
                fn()
    assert (outside / "target.md").read_text() == "victim\n"
    assert not (outside / "x.md").exists() and not (repo / "real.md").exists()


def test_symlinked_repo_root_itself_is_fine(tmp_path):
    real = tmp_path / "real"
    (real / ".ddflow").mkdir(parents=True)
    os.symlink(real, tmp_path / "alias")
    W.write_whole(tmp_path / "alias", "R.md", doc())
    assert (real / "R.md").is_file()


def test_directory_target_and_non_directory_parent_refused(repo):
    (repo / "adir").mkdir()
    (repo / "afile").write_text("x")
    with pytest.raises(W.Refused, match="not a regular file"):
        W.write_whole(repo, "adir", doc())
    with pytest.raises(W.Refused, match="not a directory"):
        W.write_whole(repo, "afile/x.md", doc())


def test_check_and_diff_also_refuse_unsafe_paths(repo):
    for kw in ({"check": True}, {"diff": True}):
        with pytest.raises(W.Refused):
            W.write_whole(repo, "../x.md", doc(), **kw)


def test_path_swapped_for_a_symlink_before_the_lock_is_caught_inside_it(repo, tmp_path_factory):
    """Safety is re-checked under the lock: a symlink planted between the first check and
    the write is refused, not followed."""
    outside = tmp_path_factory.mktemp("outside")
    from ddflow.infra import tomlcfg

    started, release = threading.Event(), threading.Event()

    def attacker() -> None:
        with tomlcfg.locked(W._lock_path(repo)):
            started.set()
            release.wait(5)
            os.symlink(outside, repo / "docs")

    t = threading.Thread(target=attacker)
    t.start()
    started.wait(5)
    res: list[str] = []

    def victim() -> None:
        try:
            W.write_whole(repo, "docs/R.md", doc())
            res.append("wrote")
        except W.Refused:
            res.append("refused")

    v = threading.Thread(target=victim)
    v.start()
    time.sleep(0.3)  # let the victim reach the lock and block on it
    release.set()
    t.join()
    v.join()
    assert res == ["refused"] and not (outside / "R.md").exists()


def test_region_in_a_crlf_file_keeps_the_prose_bytes(repo):
    p = repo / "README.md"
    begin, end = W.region_text("status", "- a\n").split("- a\n")[0], W._end("status")
    p.write_bytes(f"intro\r\n{begin.strip()}\r\n- a\n{end}\r\noutro\r\n".encode())
    W.write_region(repo, "README.md", "status", "- b\n")
    raw = p.read_bytes()
    assert raw.startswith(b"intro\r\n") and raw.endswith(b"outro\r\n") and b"- b\n" in raw
