"""The fold, as properties (B-uni-property-tests; D-unify 4 pins behaviour before a refactor).

`model.fold` is documented as pure and deterministic, and `read_all` as an idempotent
union of shards. Every interface task in P-unify moves code the fold or its readers call,
so these hold for any log the strategies can build, not only for the hand-written cases:

- deterministic: the same events give an equal State, and folding never changes an event;
- idempotent: a log read with every event twice (two shard copies of one history) folds
  to the same State as the log read once;
- order-free on input: however the shards are concatenated, the sorted union folds the same;
- round-trip: what `to_json` writes, `from_json` reads back into the same State.

The stateful replay-equals-live check is `test_replay_equals_live.py`.
"""

from __future__ import annotations

import copy
import random

from evstrategies import event_logs
from hypothesis import given, settings
from hypothesis import strategies as st

from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import _sorted_unique

#: Folding is fast (~10 µs an event); 100 logs of up to 40 events is well under a second.
FAST = settings(max_examples=100, deadline=None, database=None)


@FAST
@given(event_logs())
def test_the_fold_is_deterministic_and_never_changes_an_event(events):
    before = [e.to_json() for e in events]
    first = fold(events, strict=False)
    second = fold(copy.deepcopy(events), strict=False)
    assert first == second
    assert fold(events, strict=False) == first
    assert [e.to_json() for e in events] == before


@FAST
@given(event_logs())
def test_the_fold_is_strict_clean_on_any_log_the_writers_can_produce(events):
    """Every generated kind is known and every payload is a real writer's shape, so the
    strict fold neither raises nor differs from the lenient one (which would mean the
    lenient fold recorded a problem the strict one raised)."""
    lenient = fold(events, strict=False)
    assert lenient.fold_problems == [] and lenient.skipped_kinds == {}
    assert fold(events, strict=True) == lenient


@FAST
@given(event_logs(), st.randoms(use_true_random=False))
def test_a_duplicated_history_folds_like_the_history_once(events, rnd):
    """Two shard copies of the same events -- a merge that brought a shard in twice -- are
    one history: the union is idempotent."""
    once = fold(_sorted_unique(events), strict=False)
    twice = events + [copy.deepcopy(e) for e in rnd.sample(events, len(events))]
    assert fold(_sorted_unique(twice), strict=False) == once


@FAST
@given(event_logs(), st.randoms(use_true_random=False))
def test_the_order_shards_are_read_in_does_not_matter(events, rnd: random.Random):
    shuffled = list(events)
    rnd.shuffle(shuffled)
    assert _sorted_unique(shuffled) == _sorted_unique(events)
    assert fold(_sorted_unique(shuffled), strict=False) == fold(events, strict=False)


@FAST
@given(event_logs())
def test_what_the_log_writes_folds_back_to_the_same_state(events):
    lines = [e.to_json() for e in events]
    read = [Event.from_json(line) for line in lines]
    assert [e.to_json() for e in read] == lines
    assert fold(read, strict=False) == fold(events, strict=False)
