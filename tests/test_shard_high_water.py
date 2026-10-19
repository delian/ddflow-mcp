"""Events lost to a history rewrite are made VISIBLE, never silent (Ba24a8d32ee).

Events live in git history only. After a force-push of an older main, a revert of the
events commit, a `reset --hard`, or a deleted shard, a clone's claim simply vanished from
its tree and `doctor` said Healthy. The clone now keeps a local high-water mark per shard
(`.ddflow/local/shard-marks`); a shard with fewer events than the mark is a doctor PROBLEM
that says where the events may still be. Nothing is rewritten and nothing is auto-repaired.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git

from ddflow.infra import fsio
from ddflow.infra.localstore import LocalStore
from tests.conftest import run_cli

AGENT = "U1"


def _dd(repo: Path, *argv: str, agent: str = AGENT) -> tuple[int, str, str]:
    return run_cli(repo, *argv, agent=agent)


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow/events")
    _git(repo, "commit", "-qm", msg, "--no-verify")


def _shard(repo: Path, name: str = f"{AGENT}.jsonl") -> Path:
    return repo / ".ddflow" / "events" / name


def _lines(repo: Path, name: str = f"{AGENT}.jsonl") -> int:
    return len([ln for ln in _shard(repo, name).read_text().splitlines() if ln.strip()])


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A clone with normal use behind it: a task, a claim, a heartbeat, a second task, each
    commit adding events, so older commits hold fewer of them."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(tmp_path / "a")], check=True)
    a = tmp_path / "a"
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(a, "config", k, v)
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    assert _dd(a, "task", "add", "T1", "--title", "t", "--globs", "src/t.py")[0] == 0
    _commit(a, "T1")
    assert _dd(a, "claim", "T1")[0] == 0
    assert _dd(a, "heartbeat", "T1")[0] == 0
    _commit(a, "claim T1")
    assert _dd(a, "task", "add", "T2", "--title", "u", "--globs", "src/u.py")[0] == 0
    _commit(a, "T2")
    _git(a, "push", "-q", "origin", "main")
    return a


def _doctor(repo: Path) -> tuple[int, str]:
    code, out, err = _dd(repo, "doctor")
    return code, out + err


def _problem_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "PROBLEM" in ln and "lost" in ln]


def test_healthy_after_normal_use(clone):
    _, text = _doctor(clone)
    assert not _problem_lines(text), text


def test_a_reset_to_an_older_commit_is_a_problem_and_nothing_is_rewritten(clone):
    assert _doctor(clone)[0] in (0, 1)  # a look, so the mark is set
    was = _lines(clone)
    last_id = json.loads(_shard(clone).read_text().splitlines()[-1])["id"]
    _git(clone, "reset", "-q", "--hard", "HEAD~2")  # drops the claim and T2
    now = _lines(clone)
    assert now < was
    after_reset = _shard(clone).read_text()

    code, text = _doctor(clone)
    assert code == 1, text
    (line,) = _problem_lines(text)
    assert f"{AGENT}.jsonl" in line and f"lost {was - now} event(s)" in line, line
    assert f"was {was}, now {now}" in line, line
    assert last_id in line, line  # names the last event it saw
    for hint in ("git reflog", "git log --all -- .ddflow/events/U1.jsonl", "git show <sha>:"):
        assert hint in line, line
    assert _shard(clone).read_text() == after_reset  # nothing rewritten
    # It does not go away by looking again, and `brief` carries the same line.
    assert _doctor(clone)[0] == 1
    code, brief, err = _dd(clone, "brief")
    assert f"lost {was - now} event(s)" in brief + err, brief + err


def test_a_deleted_shard_is_a_problem(clone):
    was = _lines(clone)
    _dd(clone, "status")
    _shard(clone).unlink()
    code, text = _doctor(clone)
    assert code == 1, text
    (line,) = _problem_lines(text)
    assert f"was {was}, now 0" in line, line


def test_a_revert_of_the_events_commit_is_a_problem(clone):
    _dd(clone, "status")
    _git(clone, "revert", "--no-edit", "HEAD")  # takes T2 back out of the shard
    code, text = _doctor(clone)
    assert code == 1 and _problem_lines(text), text


def test_growth_a_new_shard_a_pull_and_a_rebase_never_alarm(clone, tmp_path):
    _dd(clone, "status")
    # growth
    assert _dd(clone, "task", "add", "T3", "--title", "v", "--globs", "src/v.py")[0] == 0
    # a new shard (another agent)
    assert (
        _dd(clone, "task", "add", "T4", "--title", "w", "--globs", "src/w.py", agent="U2")[0] == 0
    )
    _commit(clone, "growth")
    assert not _problem_lines(_doctor(clone)[1])
    # a pull that adds events: another clone appends to U2's shard and pushes
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "remote.git"), str(other)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(other, "config", k, v)
    assert (
        _dd(other, "task", "add", "T5", "--title", "x", "--globs", "src/x.py", agent="U3")[0] == 0
    )
    _commit(other, "other")
    _git(other, "push", "-q", "origin", "main")
    _git(clone, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    assert not _problem_lines(_doctor(clone)[1])
    # a rebase that keeps every event
    _git(clone, "checkout", "-qb", "feat")
    assert _dd(clone, "task", "add", "T6", "--title", "y", "--globs", "src/y.py")[0] == 0
    _commit(clone, "feat")
    _git(clone, "checkout", "-q", "main")
    (clone / "other.txt").write_text("x\n")
    _git(clone, "add", "other.txt")
    _git(clone, "commit", "-qm", "unrelated", "--no-verify")
    _git(clone, "checkout", "-q", "feat")
    _git(clone, "rebase", "-q", "main")
    assert not _problem_lines(_doctor(clone)[1])


def test_ddflows_own_commands_do_not_alarm(clone):
    _dd(clone, "status")
    for argv in (("rebuild",), ("recover",), ("render",), ("cleanup",), ("brief",), ("next",)):
        _dd(clone, *argv)
    assert not _problem_lines(_doctor(clone)[1])


def test_accept_lost_events_records_the_new_count_and_clears_the_problem(clone):
    _dd(clone, "status")
    _git(clone, "reset", "-q", "--hard", "HEAD~2")
    assert _doctor(clone)[0] == 1
    _, out, err = _dd(clone, "doctor", "--accept-lost-events")
    assert not _problem_lines(out + err), out + err
    code, text = _doctor(clone)
    assert code == 0 and not _problem_lines(text), text
    # and a later loss is caught against the NEW count
    assert _dd(clone, "task", "add", "T7", "--title", "z", "--globs", "src/z.py")[0] == 0
    _commit(clone, "T7")
    _git(clone, "reset", "-q", "--hard", "HEAD~1")
    assert _problem_lines(_doctor(clone)[1])


def test_a_missing_or_unreadable_mark_file_is_not_an_alarm(clone):
    marks = clone / ".ddflow" / "local" / "shard-marks"
    _dd(clone, "status")
    assert marks.is_file()
    marks.unlink()
    _git(clone, "reset", "-q", "--hard", "HEAD~2")
    assert not _problem_lines(_doctor(clone)[1])  # nothing to compare against
    marks.write_text("{ torn")
    _, text = _doctor(clone)
    assert not _problem_lines(text) and "Traceback" not in text, text


def test_a_newer_schema_mark_file_is_refused_cleanly(clone):
    marks = clone / ".ddflow" / "local" / "shard-marks"
    _dd(clone, "status")
    marks.write_text(
        json.dumps(
            {"schema": 99, "ddflow": "9.9.9", "fmt": 99, "data": {"U1.jsonl": {"count": 999}}}
        )
    )
    before = marks.read_text()
    with pytest.raises(fsio.NewerContent):
        LocalStore(marks.parent).read("shard-marks", schema=1)
    _, text = _doctor(clone)
    assert "Traceback" not in text, text
    assert not _problem_lines(text), text
    assert marks.read_text() == before  # never overwritten
    assert _dd(clone, "status")[0] == 0


def test_no_ddflow_code_but_the_log_writes_a_shard():
    """The mark is only a safe alarm if ddflow itself never shrinks a shard: every writer
    outside infra/log.py that names an events shard must only read it."""
    root = Path(__file__).resolve().parents[1] / "ddflow"
    writers = ("write_text", "write_bytes", "unlink", "truncate", "atomic_write", "os.replace")
    skip = {"infra/log.py", "services/shardmarks.py"}
    offenders = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel in skip:
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if (".jsonl" in line or "events_dir" in line) and any(w in line for w in writers):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, offenders


def test_the_events_of_the_last_command_are_in_the_mark(clone):
    """No command looked after the last write (the repro: a claim, then the history is
    rewritten under it): the append itself moved the mark."""
    _git(clone, "reset", "-q", "--hard", "HEAD~1")  # drops T2, the last command's events
    code, text = _doctor(clone)
    assert code == 1 and _problem_lines(text), text


def test_a_mark_that_cannot_be_written_never_fails_the_append(clone, monkeypatch):
    """The mark is bookkeeping: an unwritable `.ddflow/local` (read-only mount, full disk)
    must not fail a command whose event is already durable (reviewer finding, refuted)."""
    from ddflow.infra import log as logmod

    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(logmod, "APPEND_HOOKS", [boom])
    log = logmod.EventLog(clone, AGENT)
    before = _lines(clone)
    log.append("task.added", "T9", {"title": "x", "globs": []})
    assert _lines(clone) == before + 1


def test_a_log_built_directly_follows_its_appends_too(clone):
    """The CLI's `Ctx.log` and the MCP server's are built with `EventLog(...)`, not `open_log`
    (`ddflow approve`, skew override): their events are in the mark as well (roborev finding)."""
    import ddflow.api  # noqa: F401  (registers the hook, as every command does)
    from ddflow.infra.log import EventLog
    from ddflow.services import shardmarks

    log = EventLog(clone, AGENT)
    log.append("task.added", "T9", {"title": "x", "globs": []})
    _commit(clone, "T9")
    _git(clone, "reset", "-q", "--hard", "HEAD~1")
    assert shardmarks.lost(clone, log), "the directly built log's event was not in the mark"


def test_switching_to_a_branch_without_the_events_is_not_a_loss(clone):
    """The mark was taken on `main`; a checkout of an older branch is not a rewrite."""
    _git(clone, "branch", "older", "HEAD~2")
    _dd(clone, "status")
    _git(clone, "checkout", "-q", "older")
    assert not _problem_lines(_doctor(clone)[1])
    _git(clone, "checkout", "-q", "main")
    assert not _problem_lines(_doctor(clone)[1])
    # a reset ON the branch still is
    _git(clone, "reset", "-q", "--hard", "HEAD~2")
    assert _problem_lines(_doctor(clone)[1])


def test_a_torn_mark_file_is_replaced_not_left_dark(clone):
    marks = clone / ".ddflow" / "local" / "shard-marks"
    _dd(clone, "status")
    marks.write_text("{ torn")
    assert _dd(clone, "task", "add", "T8", "--title", "q", "--globs", "src/q.py")[0] == 0
    json.loads(marks.read_text())  # whole again
    _commit(clone, "T8")
    _git(clone, "reset", "-q", "--hard", "HEAD~1")
    assert _problem_lines(_doctor(clone)[1])


def test_coming_back_to_a_branch_still_compares_against_what_was_seen_there(clone):
    """roborev: a visit to another branch must not retake the original branch's mark."""
    _git(clone, "branch", "older", "HEAD~2")
    _dd(clone, "status")
    _git(clone, "checkout", "-q", "older")
    _dd(clone, "status")  # a look on `older`: its own mark
    _git(clone, "checkout", "-q", "main")
    _git(clone, "reset", "-q", "--hard", "HEAD~2")  # the loss on main, unseen while away
    assert _problem_lines(_doctor(clone)[1])


def test_conflict_markers_that_come_and_go_are_not_events_lost(clone):
    """reviewer: lines that are not events must not raise the mark, or removing them later
    reads as a loss."""
    shard = _shard(clone)
    good = shard.read_text()
    shard.write_text("<<<<<<< HEAD\n" + good + "=======\n>>>>>>> theirs\n" + '{"torn')
    _dd(clone, "status")  # a look while the shard is in conflict
    shard.write_text(good)  # resolved: byte-identical to before
    assert not _problem_lines(_doctor(clone)[1])


def test_a_loss_is_not_forgotten_when_the_shard_regrows_past_the_mark(clone):
    """roborev/reviewer: ordinary work after a reset must not erase an unaccepted loss before
    anyone ran doctor -- the event the mark saw is still gone."""
    _dd(clone, "status")
    _git(clone, "reset", "-q", "--hard", "HEAD~2")  # drops the claim and T2
    for i in range(8):  # more events than the shard held
        assert _dd(clone, "task", "add", f"G{i}", "--title", "g", "--globs", f"src/g{i}.py")[0] == 0
    _, text = _doctor(clone)
    assert _problem_lines(text), text
    assert "no longer holds the event" in text, text


def test_recovering_the_events_clears_the_loss(clone):
    _dd(clone, "status")
    _git(clone, "reset", "-q", "--hard", "HEAD~2")
    assert _problem_lines(_doctor(clone)[1])
    _git(clone, "reset", "-q", "--hard", "ORIG_HEAD")  # the events are back
    assert not _problem_lines(_doctor(clone)[1])


def test_a_visit_to_a_longer_branch_does_not_disarm_the_original(clone):
    """reviewer: raising a mark on another branch must not hide a loss back on this one."""
    _dd(clone, "status")
    _git(clone, "checkout", "-qb", "feat")
    assert _dd(clone, "task", "add", "T6", "--title", "f", "--globs", "src/f.py")[0] == 0
    _commit(clone, "feat")
    _git(clone, "checkout", "-q", "main")
    assert not _problem_lines(_doctor(clone)[1])
    _git(clone, "reset", "-q", "--hard", "HEAD~2")
    assert _problem_lines(_doctor(clone)[1])


def test_the_append_fast_path_does_not_erase_a_loss_at_an_equal_size(clone):
    """roborev: a shard rewritten to exactly the recorded size (the last seen event gone) must
    stay a loss when the next append takes the cheap path."""
    from ddflow.infra.log import EventLog
    from ddflow.services import shardmarks

    _dd(clone, "status")
    shard = _shard(clone)
    text = shard.read_text()
    last_id = json.loads(text.splitlines()[-1])["id"]
    shard.write_text(text.replace(last_id, "f" * len(last_id)))  # same size, event gone
    EventLog(clone, AGENT).append("task.added", "T9", {"title": "x", "globs": []})
    assert shardmarks.lost(clone, EventLog(clone, AGENT))


def test_visiting_commits_on_a_detached_head_is_not_a_loss(clone):
    """reviewer: every detached state would share one mark slot; browsing history is not a loss."""
    _dd(clone, "status")
    _git(clone, "checkout", "-q", "--detach", "HEAD~2")
    _dd(clone, "status")
    _git(clone, "checkout", "-q", "main")
    _git(clone, "checkout", "-q", "--detach", "HEAD~2")
    assert not _problem_lines(_doctor(clone)[1])


def test_a_json_line_that_is_not_an_event_does_not_raise_the_mark(clone):
    """reviewer: a pasted `{"note": ...}` line must not turn its later removal into a loss."""
    shard = _shard(clone)
    good = shard.read_text()
    shard.write_text(good + '{"note": "tidy"}\n')
    _dd(clone, "status")
    shard.write_text(good)
    assert not _problem_lines(_doctor(clone)[1])


def test_an_append_takes_the_cheap_path_and_never_rescans_the_shard(clone, monkeypatch):
    """roborev: the fast path must actually be reachable -- the hook runs AFTER the new event
    is written, so the event it checks for is the one before the last line."""
    from ddflow.infra.log import EventLog
    from ddflow.services import shardmarks

    _dd(clone, "status")

    def no_scan(*_a, **_k):
        raise AssertionError("the append fell back to a full scan of the shard")

    log = EventLog(clone, AGENT)
    marks = clone / ".ddflow" / "local" / "shard-marks"
    before = json.loads(marks.read_text())["data"]
    monkeypatch.setattr(shardmarks, "_scan", no_scan)
    ev = log.append("task.added", "T9", {"title": "x", "globs": []})
    after = json.loads(marks.read_text())["data"]
    (key,) = [k for k in after if k.endswith(f"\t{AGENT}.jsonl")]
    assert after[key]["count"] == before[key]["count"] + 1
    assert after[key]["last_event_id"] == ev.id


def test_an_interior_loss_is_not_masked_by_regrowth_to_the_same_count(clone):
    """reviewer: events removed from the MIDDLE while the last one survives, then ordinary
    appends bring the count back -- still a loss until accepted or restored."""
    _dd(clone, "status")
    was = _lines(clone)
    rows = _shard(clone).read_text().splitlines(keepends=True)
    _shard(clone).write_text("".join(rows[:2] + rows[4:]))  # two events out of the middle
    assert _lines(clone) < was
    assert _dd(clone, "task", "add", "G0", "--title", "g", "--globs", "src/g0.py")[0] == 0
    for i in range(1, 6):
        assert _dd(clone, "task", "add", f"G{i}", "--title", "g", "--globs", f"src/g{i}.py")[0] == 0
    assert _lines(clone) >= was
    assert _problem_lines(_doctor(clone)[1])


def test_restoring_exactly_what_was_seen_clears_a_flagged_loss(clone):
    _dd(clone, "status")
    orig = _shard(clone).read_text()
    _git(clone, "reset", "-q", "--hard", "HEAD~2")
    _dd(clone, "status")  # flags it
    assert _problem_lines(_doctor(clone)[1])
    _shard(clone).write_text(orig)  # the events come back, byte for byte
    assert not _problem_lines(_doctor(clone)[1])


def test_a_stale_unlocked_look_never_reads_as_a_shrink(clone, monkeypatch):
    """roborev: observe looks at the shard before it takes the marks lock; an append by another
    process in between has raised the mark, and the stale count must not flag a loss."""
    from ddflow.infra.log import EventLog
    from ddflow.services import shardmarks

    _dd(clone, "status")
    log = EventLog(clone, AGENT)
    real = shardmarks._scan
    calls = {"n": 0}

    def stale_first(path):
        calls["n"] += 1
        if calls["n"] == 1:  # the unlocked look: before the other process's append lands
            out = real(path)
            log.append("task.added", "T9", {"title": "x", "globs": []})  # the other process
            return out
        return real(path)

    _shard(clone).write_text(_shard(clone).read_text())  # same content, size unchanged
    row_key = next(iter(json.loads((clone / ".ddflow/local/shard-marks").read_text())["data"]))
    marks = clone / ".ddflow" / "local" / "shard-marks"
    doc = json.loads(marks.read_text())
    doc["data"][row_key]["size"] = 0  # force observe to look at the shard
    marks.write_text(json.dumps(doc))
    monkeypatch.setattr(shardmarks, "_scan", stale_first)
    shardmarks.observe(clone, log)
    monkeypatch.setattr(shardmarks, "_scan", real)
    assert not shardmarks.lost(clone, log)


def _marks_path(repo: Path) -> Path:
    return repo / ".ddflow" / "local" / "shard-marks"


def _marks_doc(repo: Path) -> dict:
    return json.loads(_marks_path(repo).read_text())


def _event_ids(repo: Path, name: str = f"{AGENT}.jsonl") -> list[str]:
    return [
        json.loads(ln)["id"] for ln in _shard(repo, name).read_text().splitlines() if ln.strip()
    ]


def _put_legacy(repo: Path, key: str, row: dict) -> None:
    doc = _marks_doc(repo)
    doc["data"][key] = row
    _marks_path(repo).write_text(json.dumps(doc))


def test_a_stale_plain_keyed_mark_with_a_ref_is_not_a_loss(clone):
    """B21968d4ea4: the intermediate build wrote `{shard-name: {count, ref, ...}}` with no `shard`
    field. Read as a row of its branch it named no shard ("" -> not on disk -> now 0): a false
    'lost N event(s) (was N, now 0)'. The shard is there with more events than that old look."""
    _dd(clone, "status")
    ids = _event_ids(clone)
    _put_legacy(
        clone,
        f"{AGENT}.jsonl",
        {"count": 2, "last_event_id": ids[1], "ref": "refs/heads/main", "size": 10},
    )
    code, text = _doctor(clone)
    assert not _problem_lines(text), text
    assert code == 0, text
    assert "now 0" not in _dd(clone, "brief")[1]


def test_a_legacy_plain_keyed_mark_is_a_real_mark_until_it_is_migrated(clone):
    """The legacy row is the only record of the count it holds: a shard now below it is a loss,
    named by its shard (the key), not by a missing field."""
    _dd(clone, "status")
    was = _lines(clone)
    ids = _event_ids(clone)
    doc = _marks_doc(clone)
    for k in [k for k in doc["data"] if "\t" in k]:
        del doc["data"][k]  # only the legacy shape records this shard
    doc["data"][f"{AGENT}.jsonl"] = {
        "count": was,
        "last_event_id": ids[-1],
        "ref": "refs/heads/main",
        "size": _shard(clone).stat().st_size,
    }
    _marks_path(clone).write_text(json.dumps(doc))
    _git(clone, "reset", "-q", "--hard", "HEAD~2")
    now = _lines(clone)
    assert now < was
    code, text = _doctor(clone)
    assert code == 1, text
    (line,) = _problem_lines(text)
    assert f"{AGENT}.jsonl lost {was - now} event(s)" in line, line


def test_a_legacy_plain_keyed_mark_is_folded_into_the_scoped_one_never_lowering_it(clone):
    _dd(clone, "status")
    scoped = {k: v for k, v in _marks_doc(clone)["data"].items() if "\t" in k}
    (key,) = [k for k in scoped if k.endswith(f"\t{AGENT}.jsonl")]
    before = scoped[key]["count"]
    ids = _event_ids(clone)
    _put_legacy(
        clone,
        f"{AGENT}.jsonl",
        {"count": 1, "last_event_id": ids[0], "ref": "refs/heads/main", "size": 1},
    )
    assert _dd(clone, "task", "add", "M1", "--title", "m", "--globs", "src/m1.py")[0] == 0
    data = _marks_doc(clone)["data"]
    assert f"{AGENT}.jsonl" not in data, "the legacy plain key is migrated away"
    assert data[key]["count"] == before + 1  # not lowered by the stale row
    assert data[key]["shard"] == f"{AGENT}.jsonl" and data[key]["ref"] == "refs/heads/main"
    assert not _problem_lines(_doctor(clone)[1])


def test_a_legacy_mark_above_the_scoped_one_raises_it(clone):
    _dd(clone, "status")
    n = _lines(clone)
    ids = _event_ids(clone)
    _put_legacy(
        clone,
        f"{AGENT}.jsonl",
        {"count": n + 50, "last_event_id": ids[-1], "ref": "refs/heads/main", "size": 1},
    )
    code, text = _doctor(clone)
    assert code == 1, text
    (line,) = _problem_lines(text)
    assert f"{AGENT}.jsonl lost 50 event(s)" in line and f"was {n + 50}, now {n}" in line, line


def test_a_scoped_row_with_a_torn_shape_names_its_shard_from_the_key(clone):
    """The key says which (branch, shard) a row is; a row missing or disagreeing on `shard`/`ref`
    is not trusted for them."""
    _dd(clone, "status")
    n = _lines(clone)
    doc = _marks_doc(clone)
    (key,) = [k for k in doc["data"] if k.endswith(f"\t{AGENT}.jsonl")]
    doc["data"][key] = {"count": n + 3, "last_event_id": _event_ids(clone)[-1], "size": 1}
    _marks_path(clone).write_text(json.dumps(doc))
    code, text = _doctor(clone)
    assert code == 1, text
    (line,) = _problem_lines(text)
    assert f"{AGENT}.jsonl lost 3 event(s)" in line, line


def test_a_row_that_is_not_a_mark_is_ignored_and_replaced(clone):
    _dd(clone, "status")
    doc = _marks_doc(clone)
    doc["data"]["refs/heads/main\tGhost.jsonl"] = {"count": "many"}
    doc["data"]["Other.jsonl"] = "junk"
    _marks_path(clone).write_text(json.dumps(doc))
    assert not _problem_lines(_doctor(clone)[1])


def test_uncommitted_appended_events_are_not_a_loss(clone):
    """The fold reads the working tree: what is appended and not yet committed is there, and a
    shard never seen in git (untracked) is not 'lost' either."""
    for i in range(3):
        assert _dd(clone, "task", "add", f"W{i}", "--title", "w", "--globs", f"src/w{i}.py")[0] == 0
    assert (
        _dd(clone, "task", "add", "W9", "--title", "w", "--globs", "src/w9.py", agent="U7")[0] == 0
    )
    assert "U7.jsonl" in _git(clone, "status", "--porcelain")  # untracked
    assert _git(clone, "diff", "--stat").strip()  # U1's shard is modified, not committed
    code, text = _doctor(clone)
    assert not _problem_lines(text), text
    assert code == 0, text


def test_accept_clears_a_loss_recorded_under_the_legacy_shape(clone):
    _dd(clone, "status")
    n = _lines(clone)
    _put_legacy(
        clone,
        f"{AGENT}.jsonl",
        {
            "count": n + 5,
            "last_event_id": _event_ids(clone)[-1],
            "ref": "refs/heads/main",
            "size": 1,
        },
    )
    assert _problem_lines(_doctor(clone)[1])
    _, out, err = _dd(clone, "doctor", "--accept-lost-events")
    assert not _problem_lines(_doctor(clone)[1]), out + err
    assert _doctor(clone)[0] == 0


def test_folding_a_legacy_row_never_clears_a_flagged_loss(clone):
    """bug_hunt: a scoped row already flagged `shrunk` stays flagged when a legacy row with a
    higher count is folded into it (the interior events are still gone)."""
    _dd(clone, "status")
    n = _lines(clone)
    _git(clone, "reset", "-q", "--hard", "HEAD~2")
    _dd(clone, "status")  # flags the loss
    assert _problem_lines(_doctor(clone)[1])
    for i in range(n):  # regrow the shard past the old count
        assert _dd(clone, "task", "add", f"R{i}", "--title", "r", "--globs", f"src/r{i}.py")[0] == 0
    assert _lines(clone) > n
    _put_legacy(
        clone,
        f"{AGENT}.jsonl",
        {
            "count": n + 1,
            "last_event_id": _event_ids(clone)[-1],
            "ref": "refs/heads/main",
            "size": 1,
        },
    )
    assert _problem_lines(_doctor(clone)[1])


def _caught_short(clone, monkeypatch, restore_on_sleep: int | None):
    """observe() on a shard caught half-rewritten; the file is whole again at the given
    sleep (None = never). Returns (sleeps, lost)."""
    from ddflow.infra.log import EventLog
    from ddflow.services import shardmarks

    _dd(clone, "status")
    log = EventLog(clone, AGENT)
    path = _shard(clone)
    whole = path.read_text()
    rows = whole.splitlines(keepends=True)
    path.write_text("".join(rows[: len(rows) // 2]))  # the half-written moment
    sleeps = []

    def sleep(_s):
        sleeps.append(1)
        if restore_on_sleep is not None and len(sleeps) == restore_on_sleep:
            path.write_text(whole)  # it settles

    monkeypatch.setattr(shardmarks.time, "sleep", sleep)
    shardmarks.observe(clone, log)
    return len(sleeps), shardmarks.lost(clone, log)


def test_a_shard_caught_half_rewritten_by_git_is_not_flagged_as_lost(clone, monkeypatch):
    """B-events-uncommitted-shards: a look that lands while another process rewrites the shard
    in place sees half of it; the sticky flag then reported a loss for events still present.
    It settles on the third look here, so a window of one or two looks would not do."""
    n, lost = _caught_short(clone, monkeypatch, restore_on_sleep=3)
    assert n >= 3 and not lost
    assert not any(r.get("shrunk") for r in _marks_doc(clone)["data"].values())


def test_a_shard_that_stays_short_is_still_a_loss_after_the_looks(clone, monkeypatch):
    n, lost = _caught_short(clone, monkeypatch, restore_on_sleep=None)
    assert lost and n >= 4
