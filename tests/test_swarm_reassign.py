"""SW-reassign: poison-model reassignment. Pure: two failed leases of one family on one
item send that item to another family; a family below the mark, or poison on a different
item, is untouched.
"""

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services.swarm import reassign as RA


def _report(g: Log) -> RA.Report:
    return RA.detect(list(g.events), fold(list(g.events), strict=False), Config(), T0 + 5000)


def test_two_failed_leases_of_one_family_poison_it_for_that_item():
    g = Log()
    g.session("lane-a", "s1", T0, model="kimi-k3")  # moonshot
    g.claim("lane-a", "T1", T0, "f.py")
    g.add("gate.failed", "T1", "lane-a", T0 + 50, gate="critic")
    g.release("lane-a", "T1", T0 + 60)
    g.session("lane-b", "s2", T0, model="kimi-k3")  # moonshot again
    g.claim("lane-b", "T1", T0 + 100, "f.py")
    g.add("gate.failed", "T1", "lane-b", T0 + 150, gate="critic")
    g.complete("lane-b", "T1", T0 + 200)
    r = _report(g)
    assert r.avoids("T1", "moonshot")
    assert r.route("T1", ["moonshot", "alibaba"]) == ["alibaba"]


def test_one_failure_does_not_poison():
    g = Log()
    g.session("lane-a", "s1", T0, model="kimi-k3")
    g.claim("lane-a", "T1", T0, "f.py")
    g.add("gate.failed", "T1", "lane-a", T0 + 10, gate="critic")
    g.complete("lane-a", "T1", T0 + 20)
    r = _report(g)
    assert not r.avoids("T1", "moonshot")


def test_poison_is_per_item_not_per_family():
    # moonshot failed T1 twice but never touched T2: it is free to take T2.
    g = Log()
    g.session("lane-a", "s1", T0, model="kimi-k3")
    g.claim("lane-a", "T1", T0, "f.py")
    g.add("gate.failed", "T1", "lane-a", T0 + 10, gate="critic")
    g.release("lane-a", "T1", T0 + 20)
    g.claim("lane-a", "T1", T0 + 30, "f.py")
    g.add("gate.failed", "T1", "lane-a", T0 + 40, gate="unit_tests")
    g.complete("lane-a", "T1", T0 + 50)
    g.session("lane-c", "s2", T0, model="kimi-k3")
    g.claim("lane-c", "T2", T0, "g.py")
    g.complete("lane-c", "T2", T0 + 60)
    r = _report(g)
    assert r.avoids("T1", "moonshot")
    assert not r.avoids("T2", "moonshot")


def test_a_partial_or_unavailable_outcome_is_not_a_failed_lease():
    # partial/unavailable never prove the family wrong, so they never count toward the mark.
    g = Log()
    g.session("lane-a", "s1", T0, model="kimi-k3")
    g.claim("lane-a", "T1", T0, "f.py")
    g.add("gate.partial", "T1", "lane-a", T0 + 10, gate="ci")
    g.add("gate.unavailable", "T1", "lane-a", T0 + 20, gate="rubber_duck")
    g.complete("lane-a", "T1", T0 + 30)
    r = _report(g)
    assert not r.avoids("T1", "moonshot")
