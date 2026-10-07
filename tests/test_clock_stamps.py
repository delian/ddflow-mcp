"""clock.run_stamp and ids.free (B-uni-clock.4-stamps).

run_stamp replaces four hand-made run and file names (review reply files, job logs,
wait files, the flow sampler's lock token); ids.free replaces the two numbered-id loops
(trigger-filed items, import collisions). The loops are pinned against verbatim copies
of the code they replaced, so every id minted stays the same.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from ddflow.core import clock
from ddflow.core.ids import free
from ddflow.services import importer

STAMP = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


def test_run_stamp_is_utc_second_then_a_token():
    at = datetime(2026, 10, 7, 1, 2, 3, tzinfo=UTC).timestamp()
    s = clock.run_stamp(at)
    assert STAMP.match(s), s
    assert s.startswith("20261007T010203Z-")
    assert len(clock.run_stamp(at, token_hex=5).split("-")[1]) == 5
    assert STAMP.match(clock.run_stamp())


def test_run_stamps_made_in_one_second_differ_and_sort_by_time(monkeypatch):
    at = 1_790_000_000.0
    # a fresh token per call, drawn from `secrets` (made deterministic here, so the test
    # proves the draw and never depends on 32 bits not colliding)
    tokens = iter(f"{n:08x}" for n in range(1000))
    monkeypatch.setattr(clock.secrets, "token_hex", lambda _n: next(tokens))
    same = [clock.run_stamp(at) for _ in range(200)]
    assert len(set(same)) == 200
    assert clock.run_stamp(at) < clock.run_stamp(at + 1) < clock.run_stamp(at + 86_400)


def test_review_reply_file_is_named_by_a_run_stamp(tmp_path):
    from ddflow.api.review import _ReplyFile

    f = _ReplyFile(tmp_path, "T1", "critic", "some/model")
    item, gate, who, run, ext = f.path.name.rsplit(".", 4)
    assert (item, gate, who, ext) == ("T1", "critic", "some_model", "jsonl")
    assert STAMP.match(run), run


# -- the numbered-id loops, verbatim, as the pin ------------------------------------


def old_ids_free(candidate: str, used) -> str:
    if candidate not in used:
        return candidate
    n = 2
    while f"{candidate}-{n}" in used:
        n += 1
    return f"{candidate}-{n}"


def old_trigger_id(trig: str, n: int, taken: set[str]) -> str:
    iid = f"T-{trig}-{n}"
    while iid in taken:
        n += 1
        iid = f"T-{trig}-{n}"
    return iid


def old_unique(preferred: str, fallback: str, taken: set[str]) -> str:
    for candidate in (preferred, fallback):
        if candidate and candidate not in taken:
            taken.add(candidate)
            return candidate
    base = fallback or preferred or "item"
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    taken.add(f"{base}-{n}")
    return f"{base}-{n}"


NAMES = st.sampled_from(["", "a", "b", "item", "T-x-1", "T-x-2", "a-2", "a-3", "b-2", "item-2"])
TAKEN = st.sets(
    st.sampled_from(
        ["a", "a-2", "a-3", "a-5", "b", "b-2", "item", "item-2", "T-x-1", "T-x-2", "T-x-3"]
    )
)


@settings(max_examples=300, deadline=None)
@given(NAMES, TAKEN)
def test_free_is_the_old_ids_loop(candidate, taken):
    assert free(candidate, taken) == old_ids_free(candidate, taken)
    as_dict = dict.fromkeys(taken, "task")
    assert free(candidate, as_dict) == old_ids_free(candidate, as_dict)


@settings(max_examples=300, deadline=None)
@given(st.integers(1, 6), TAKEN)
def test_free_reproduces_the_trigger_item_ids(n, taken):
    got = free(f"T-x-{n}", taken, numbered=lambda k: f"T-x-{k}", start=n + 1)
    assert got == old_trigger_id("x", n, taken)


@settings(max_examples=300, deadline=None)
@given(NAMES, NAMES, TAKEN)
def test_importer_unique_is_unchanged(preferred, fallback, taken):
    mine, theirs = set(taken), set(taken)
    assert importer._unique(preferred, fallback, mine) == old_unique(preferred, fallback, theirs)
    assert mine == theirs
