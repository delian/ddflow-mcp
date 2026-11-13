"""Legacy and torn shard-mark rows of the shard high-water mark (split from test_shard_high_water.py)."""

from __future__ import annotations

import json

from helpers import git_raw as _git
from shard_high_water_support import (  # noqa: F401
    AGENT,
    _clone_fixture,
    _commit,
    _dd,
    _doctor,
    _event_ids,
    _lines,
    _marks_doc,
    _marks_path,
    _problem_lines,
    _put_legacy,
    _shard,
)


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
