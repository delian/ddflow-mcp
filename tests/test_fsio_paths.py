"""The path helpers of the file layer (B-uni-fsio-paths): `ensure_ignored_dir`, `repo_rel`
and the line-splice `Region` -- and, first, the pins that hold every call site they
replaced to the bytes and modes it wrote before."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest

from ddflow.infra import fsio
from ddflow.infra import log as L
from ddflow.infra.log import EventLog, clear_parse_cache
from ddflow.services import configwrite as CW
from ddflow.services import upstream_delivery as UD
from ddflow.services.gates import runner as R
from tests.test_log_merged_order import _ev, _put
from tests.test_upstream_delivery import _bundle


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _umask_default() -> int:
    um = os.umask(0)
    os.umask(um)
    return 0o666 & ~um


# -- pins: each former copy still writes what it wrote ---------------------------------


@pytest.fixture
def _log_isolated(monkeypatch):
    monkeypatch.setattr(EventLog, "stamp", False)
    monkeypatch.setattr(L, "SNAPSHOT_MIN_EVENTS", 10)
    monkeypatch.delenv(L.SNAPSHOT_ENV, raising=False)
    clear_parse_cache()
    yield
    clear_parse_cache()


def test_pin_seen_marker_dir_ignores_itself(tmp_path):
    L._write_seen_marker(tmp_path, "1.0")
    ignore = tmp_path / ".ddflow" / "local" / ".gitignore"
    assert ignore.read_bytes() == b"*\n"
    assert _mode(ignore) == _umask_default()


def test_pin_clone_id_dir_ignores_itself(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    assert L._clone_suffix(tmp_path)
    assert (tmp_path / ".ddflow" / "local" / ".gitignore").read_bytes() == b"*\n"


def test_pin_snapshot_dir_ignores_itself(tmp_path, _log_isolated):
    log = EventLog(tmp_path, "x")
    _put(log, "a", [_ev("a", i) for i in range(1, 1600)])
    log.read_all()
    snap = log._snapshot_path()
    assert snap.exists()
    assert (snap.parent / ".gitignore").read_bytes() == b"*\n"


def test_pin_gate_run_logs_dir_ignores_itself(tmp_path):
    R.run_log_writer(tmp_path, "I1", "unit")("output")
    ignore = tmp_path / ".ddflow" / R.RUNS_DIR / ".gitignore"
    assert ignore.read_bytes() == b"# gate run output logs: machine-local, never committed\n*\n"


def test_pin_config_local_dir_ignores_itself(tmp_path):
    d = CW.ensure_local_dir(tmp_path)
    assert d == tmp_path / ".ddflow" / "local"
    assert (d / ".gitignore").read_bytes() == b"*\n"
    assert _mode(d / ".gitignore") == _umask_default()


def test_pin_upstream_reports_ignore_file_is_owner_only(tmp_path):
    UD.write_report(tmp_path, _bundle())
    ignore = tmp_path / ".ddflow" / "local" / ".gitignore"
    assert ignore.read_bytes() == b"*\n"
    assert _mode(ignore) == 0o600


def test_pin_an_in_repo_worktree_root_ignores_itself(tmp_path):
    from ddflow.infra.worktree import _ignore_inside

    root = tmp_path / ".ddflow" / "worktrees"
    root.mkdir(parents=True)
    _ignore_inside(tmp_path, root)
    assert (root / ".gitignore").read_bytes() == b"# ddflow worktrees: never committed\n*\n"


def test_pin_a_worktree_root_outside_the_repo_gets_nothing(tmp_path):
    from ddflow.infra.worktree import _ignore_inside

    repo, outside = tmp_path / "repo", tmp_path / "wts"
    repo.mkdir()
    outside.mkdir()
    _ignore_inside(repo, outside)
    assert not (outside / ".gitignore").exists()


def test_pin_a_worktree_root_that_cannot_be_written_does_not_fail(tmp_path):
    from ddflow.infra.worktree import _ignore_inside

    root = tmp_path / "wts"
    root.mkdir()
    root.chmod(0o500)
    try:
        if os.access(root, os.W_OK):  # pragma: no cover - running as root
            pytest.skip("permissions are not enforced for this user")
        _ignore_inside(tmp_path, root)  # no error
    finally:
        root.chmod(0o700)
    assert not (root / ".gitignore").exists()


def test_pin_a_registered_wait_dir_ignores_itself(tmp_path):
    from ddflow.services import waits as WT

    WT.register(tmp_path, WT.Waiter(agent="a", item="I1"))
    assert (tmp_path / ".ddflow" / "local" / ".gitignore").read_bytes() == b"*\n"


def test_pin_a_place_in_line_dir_ignores_itself(tmp_path):
    from ddflow.services import waits as WT

    WT.queue(tmp_path, "a", "I1", waiting_on=["I0"], reason="held", window_s=60)
    assert (tmp_path / ".ddflow" / "local" / ".gitignore").read_bytes() == b"*\n"


def test_pin_an_unwritable_ignore_file_does_not_stop_a_wait(tmp_path, monkeypatch):
    from ddflow.services import waits as WT

    real = Path.write_text

    def refuse(self, *a, **kw):
        if self.name == ".gitignore":
            raise PermissionError(13, "read-only", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", refuse)
    real_atomic = fsio.atomic_write

    def refuse_atomic(path, data, **kw):
        if Path(path).name == ".gitignore":
            raise PermissionError(13, "read-only", str(path))
        return real_atomic(path, data, **kw)

    monkeypatch.setattr(fsio, "atomic_write", refuse_atomic)
    w = WT.register(tmp_path, WT.Waiter(agent="a", item="I1"))
    assert w.path and Path(w.path).exists()


@pytest.mark.parametrize(
    "write",
    [
        lambda root: L._write_seen_marker(root, "1.0"),
        CW.ensure_local_dir,
        lambda root: UD.write_report(root, _bundle()),
    ],
    ids=["seen-marker", "config-local", "upstream"],
)
def test_pin_an_existing_ignore_file_is_never_rewritten(tmp_path, write):
    local = tmp_path / ".ddflow" / "local"
    local.mkdir(parents=True)
    (local / ".gitignore").write_text("mine\n", "utf-8")
    write(tmp_path)
    assert (local / ".gitignore").read_text("utf-8") == "mine\n"


# -- ensure_ignored_dir ----------------------------------------------------------------


def test_ensure_ignored_dir_creates_the_dir_and_its_ignore_file(tmp_path):
    d = tmp_path / "a" / "b"
    assert fsio.ensure_ignored_dir(d) == d
    assert d.is_dir()
    assert (d / ".gitignore").read_bytes() == b"*\n"


def test_ensure_ignored_dir_puts_the_comment_first(tmp_path):
    fsio.ensure_ignored_dir(tmp_path, comment="machine-local")
    assert (tmp_path / ".gitignore").read_bytes() == b"# machine-local\n*\n"


def test_ensure_ignored_dir_honours_a_mode(tmp_path):
    fsio.ensure_ignored_dir(tmp_path, mode=0o600)
    assert _mode(tmp_path / ".gitignore") == 0o600


def test_ensure_ignored_dir_keeps_an_existing_file(tmp_path):
    (tmp_path / ".gitignore").write_text("keep\n", "utf-8")
    fsio.ensure_ignored_dir(tmp_path, comment="x", mode=0o600)
    assert (tmp_path / ".gitignore").read_text("utf-8") == "keep\n"


def test_ensure_ignored_dir_racing_writers_leave_one_whole_file(tmp_path):
    errors: list[BaseException] = []

    def go() -> None:
        try:
            fsio.ensure_ignored_dir(tmp_path)
        except BaseException as exc:  # pragma: no cover - the failure being tested for
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert (tmp_path / ".gitignore").read_bytes() == b"*\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".gitignore"]


# -- repo_rel --------------------------------------------------------------------------


def test_repo_rel_is_worktree_repo_relative(tmp_path):
    from ddflow.infra import worktree as W

    assert W.repo_relative is fsio.repo_rel


def test_repo_rel_inside_is_posix_and_outside_is_none(tmp_path):
    repo = tmp_path / "r"
    (repo / "sub").mkdir(parents=True)
    assert fsio.repo_rel(repo, repo / "sub" / "x.md") == "sub/x.md"
    assert fsio.repo_rel(repo, repo) == "."
    assert fsio.repo_rel(repo, tmp_path / "elsewhere") is None


def test_repo_rel_not_strict_hands_back_the_path_as_given(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    outside = tmp_path / "elsewhere" / "x.md"
    assert fsio.repo_rel(repo, outside, strict=False) == str(outside)
    assert fsio.repo_rel(repo, str(outside), as_given=True, strict=False) == str(outside)
    assert fsio.repo_rel(repo, repo / "y.md", strict=False) == "y.md"


# -- Region ----------------------------------------------------------------------------

BEGIN = r"<!-- begin -->"
END = r"<!-- end -->"


def test_region_replaces_the_lines_from_begin_through_end():
    r = fsio.Region(BEGIN, END)
    text = "head\n<!-- begin -->\nold\n<!-- end -->\ntail\n"
    assert r.splice(text, "<!-- begin -->\nnew\n<!-- end -->\n") == (
        "head\n<!-- begin -->\nnew\n<!-- end -->\ntail\n"
    )


def test_region_markers_match_whole_lines_only():
    r = fsio.Region(BEGIN, END)
    text = "say <!-- begin --> inline\nbody\n"
    assert r.find(text) is None
    assert r.splice(text, "X\n") == "say <!-- begin --> inline\nbody\nX\n"


def test_region_a_last_end_line_without_newline_is_replaced_whole():
    r = fsio.Region(BEGIN, END)
    assert r.splice("<!-- begin -->\nold\n<!-- end -->", "B\n") == "B\n"


def test_region_body_is_what_lies_between_the_marker_lines():
    r = fsio.Region(BEGIN, END)
    assert r.body("a\n<!-- begin -->\nx\ny\n<!-- end -->\n") == "x\ny\n"
    assert r.body("no markers\n") is None


def test_region_absent_appends_after_the_text():
    r = fsio.Region(BEGIN, END)
    assert r.splice("a\n\n\n", "B\n") == "a\nB\n"
    assert r.splice("a", "B\n", gap="\n\n") == "a\n\nB\n"
    assert r.splice("", "B\n") == "\nB\n"


@pytest.mark.parametrize(
    "text",
    [
        "<!-- begin -->\n<!-- begin -->\n<!-- end -->\n",
        "<!-- begin -->\n<!-- end -->\n<!-- end -->\n",
        "<!-- end -->\n<!-- begin -->\n",
        "<!-- begin -->\nonly the start\n",
        "only the end\n<!-- end -->\n",
    ],
    ids=["two-begins", "two-ends", "reversed", "no-end", "no-begin"],
)
def test_region_refuses_broken_markers(text):
    r = fsio.Region(BEGIN, END)
    with pytest.raises(fsio.RegionError):
        r.splice(text, "x\n")


def test_a_markerless_region_always_appends():
    assert fsio.APPEND.find("<!-- begin -->\n") is None
    assert fsio.APPEND.splice("a = 1\n\n", "b = 2\n") == "a = 1\nb = 2\n"


# -- append_block on the Region primitive ----------------------------------------------


def test_append_block_joins_exactly_as_before(tmp_path):
    path = CW.append_block(tmp_path, "[x]\na = 1   \n\n", shared=True, person=True)
    assert path.read_text("utf-8") == "\n[x]\na = 1\n"
    CW.append_block(tmp_path, "[y]\nb = 2\n", shared=True, person=True)
    assert path.read_text("utf-8") == "\n[x]\na = 1\n[y]\nb = 2\n"
