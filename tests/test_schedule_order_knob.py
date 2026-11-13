"""`[schedule].order`: the order keys of the default optimizer as a layered knob.

Closes bug B0db4e156e8: D-priority-semantics cited a knob that did not exist. The default is
today's order, pinned here; a configured order changes the offer; `id` always ends it; an
unknown or repeated key is refused naming the known ones; `order_pin` (a company floor's way
to pin the head) can only grow as layers stack.
"""

from __future__ import annotations

import time

import pytest

from ddflow.config import Config
from ddflow.core import schedule as S
from ddflow.core.model import fold
from ddflow.core.schedule import plan
from ddflow.core.schedule._filters import effective_order

#: The pinned default. Changing it must change this test (the falsifier of SCHED-order-knob).
DEFAULT_ORDER = ["recover_first", "bugs_first", "unphased_first", "priority", "id"]
#: Registered, selectable, and NOT in the default.
OPT_IN = ["critical_path", "waited", "phase_priority", "wave"]


def _queue(log) -> None:
    A = log.append
    A("phase.added", "P", {"title": "p", "priority": 5})
    A("phase.added", "Q", {"title": "q", "priority": 1})
    A("task.added", "T1", {"parent": "P", "globs": ["a/**"], "priority": 50, "tags": ["wave:2"]})
    A("task.added", "T2", {"parent": "P", "globs": ["b/**"], "priority": 10, "tags": ["wave:1"]})
    A("task.added", "T3", {"parent": "Q", "globs": ["c/**"], "priority": 99, "tags": ["bugfix"]})
    A("task.added", "T4", {"parent": "Q", "globs": ["d/**"], "priority": 30, "tags": ["wave:0"]})


def _ready(log, **set_) -> list[str]:
    cfg = Config.load()
    cfg.schedule.max_parallel_tasks = 8
    for k, v in set_.items():
        setattr(cfg.schedule, k, v)
    return [i.id for i in plan(fold(log.read_all()), cfg, now=time.time(), agent="me").ready]


def test_the_default_order_is_pinned():
    assert Config.load().schedule.order == DEFAULT_ORDER
    assert S.ORDER == DEFAULT_ORDER
    assert Config.load().schedule.order_pin == []


def test_opt_in_keys_are_registered_but_not_default():
    assert set(OPT_IN) <= set(S.KEYS)
    assert not set(OPT_IN) & set(DEFAULT_ORDER)
    assert not set(OPT_IN) & set(S.ORDER)


def test_default_config_orders_a_queue_as_the_explicit_default_does(log):
    _queue(log)
    assert _ready(log) == _ready(log, order=list(DEFAULT_ORDER))
    assert _ready(log) == ["T3", "T2", "T4", "T1"]  # bug first, then priority


def test_a_configured_order_changes_the_offer(log):
    _queue(log)
    assert _ready(log, order=["priority", "id"]) == ["T2", "T4", "T1", "T3"]
    assert _ready(log, order=["wave", "id"]) == ["T4", "T2", "T1", "T3"]
    assert _ready(log, order=["phase_priority", "priority", "id"]) == ["T4", "T3", "T2", "T1"]


def test_id_always_ends_the_order():
    cfg = Config.load()
    cfg.schedule.order = ["priority"]
    assert effective_order(cfg) == ["priority", "id"]


def test_the_pin_heads_the_effective_order():
    cfg = Config.load()
    cfg.schedule.order = ["priority", "bugs_first", "id"]
    cfg.schedule.order_pin = ["bugs_first"]
    assert effective_order(cfg) == ["bugs_first", "priority", "id"]


@pytest.mark.parametrize(
    "value, words",
    [
        (["nope", "id"], "nope"),
        (["priority", "priority", "id"], "duplicate"),
        (["id", "priority"], "must come last"),
    ],
)
def test_a_bad_order_is_refused_naming_the_known_keys(value, words):
    with pytest.raises(ValueError) as e:
        Config.check({"schedule": {"order": value}})
    msg = str(e.value)
    assert words in msg and "bugs_first" in msg and "critical_path" in msg


def test_a_bad_pin_is_refused():
    with pytest.raises(ValueError, match="nope"):
        Config.check({"schedule": {"order_pin": ["nope"]}})


def test_id_cannot_be_pinned():
    with pytest.raises(ValueError, match="cannot be pinned"):
        Config.check({"schedule": {"order_pin": ["id"]}})
    with pytest.raises(ValueError, match="cannot be pinned"):
        Config.check({"schedule": {"order_pin": ["bugs_first", "id"]}})


def test_the_pin_only_grows_across_layers():
    cfg = Config()
    cfg._apply({"schedule": {"order_pin": ["bugs_first"]}}, "file")
    cfg._apply({"schedule": {"order_pin": ["priority"]}}, "local")
    cfg._apply({"schedule": {"order_pin": []}}, "env")
    # the later (more senior) layer's keys lead; nothing a lower layer pinned is dropped
    assert cfg.schedule.order_pin == ["priority", "bugs_first"]


def test_the_bool_shortcut_still_switches_bugs_first_off(log):
    _queue(log)
    assert _ready(log, bugs_first=False) == ["T2", "T4", "T1", "T3"]


def test_explain_shows_the_effective_keys():
    rows = {k: (v, s) for k, v, s, _d in Config.load().explain()}
    assert rows["schedule.order"][0] == DEFAULT_ORDER
    assert "schedule.order_pin" in rows
