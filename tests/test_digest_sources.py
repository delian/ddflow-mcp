"""OD-inventory: the operator-queue source table is complete and well formed."""

from __future__ import annotations

import re

from ddflow.services.approvals import sites
from ddflow.services.digest import sources as S


def test_every_operator_only_verb_is_covered_or_exempt():
    assert S.unregistered_sites() == []


def test_unregistered_verb_is_reported(monkeypatch):
    extra = sites.Site("new.verb", "A", sites.T2, sites.L1, "x", (), "ddflow new")
    monkeypatch.setattr(sites, "SITES", (*sites.SITES, extra))
    assert S.unregistered_sites() == ["new.verb"]


def test_site_names_are_real_and_not_both_covered_and_exempt():
    real = {s.verb for s in sites.SITES}
    named = {v for s in S.SOURCES for v in s.sites}
    assert named <= real and set(S.EXEMPT_SITES) <= real
    assert not named & set(S.EXEMPT_SITES)
    assert all(S.EXEMPT_SITES.values())


def test_kinds_unique_and_rows_complete():
    kinds = [s.kind for s in S.SOURCES]
    assert len(kinds) == len(set(kinds))
    for s in S.SOURCES:
        assert re.fullmatch(r"[a-z_]+", s.kind)
        assert s.owner in S.OWNERS and s.severity in S.SEVERITIES
        for f in (s.what, s.detected_from, s.answer, s.chat, s.blocks):
            assert f.strip(), (s.kind, f)
        assert s.collector == f"ddflow.services.digest.queue:collect_{s.kind}"


def test_operator_rows_have_a_chat_route_and_lead_rows_do_not_pretend():
    for s in S.SOURCES:
        if s.owner == S.OPERATOR:
            assert "AskUserQuestion" in s.chat, s.kind
        else:
            assert s.chat == S.LEAD_ONLY


def test_site_commands_appear_in_the_answer_of_their_source():
    cmd = {s.verb: s.command for s in sites.SITES}
    for s in S.SOURCES:
        for v in s.sites:
            assert cmd[v].split(" ")[1] in s.answer, (s.kind, v)


def test_red_main_is_urgent_and_by_kind_indexes_all():
    assert S.by_kind()["red_main"].severity == "urgent"
    assert len(S.by_kind()) == len(S.SOURCES)
