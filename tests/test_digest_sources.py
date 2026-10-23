"""OD-inventory: the operator-queue source table is complete and well formed."""

from __future__ import annotations

import argparse
import re

from ddflow.services.approvals import sites
from ddflow.services.digest import sources as S
from ddflow.surfaces.cli import build_parser


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
        for f in (s.what, s.detected_from, s.answer, s.command, s.chat, s.blocks):
            assert f.strip(), (s.kind, f)


def test_operator_rows_have_a_chat_route_and_lead_rows_do_not_pretend():
    for s in S.SOURCES:
        if s.owner == S.OPERATOR:
            assert "AskUserQuestion" in s.chat, s.kind
        else:
            assert s.chat == S.LEAD_ONLY


def _fixed(command: str) -> list[str]:
    """The tokens of a command that are not placeholders: verb words and options."""
    return [w for w in command.split() if not w.startswith("<") and w != "..."]


def _subsequence(needle: list[str], hay: list[str]) -> bool:
    it = iter(hay)
    return all(w in it for w in needle)


def test_commands_are_runnable_ddflow_commands():
    for s in S.SOURCES:
        assert s.command.startswith("ddflow "), s.kind
        assert "..." not in s.command, s.kind


def test_site_commands_appear_in_the_answer_of_their_source():
    cmd = {s.verb: s.command for s in sites.SITES}
    for s in S.SOURCES:
        for v in s.sites:
            assert _subsequence(_fixed(cmd[v]), _fixed(s.command)), (s.kind, v)


def test_red_main_is_urgent_and_by_kind_indexes_all():
    assert S.by_kind()["red_main"].severity == "urgent"
    assert len(S.by_kind()) == len(S.SOURCES)


def _resolve(command: str) -> tuple[argparse.ArgumentParser, list[str]]:
    """Walk the real parser along the command's verb words; return the final parser and the rest."""
    parser = build_parser()
    words = command.split()[1:]
    while words:
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs or words[0] not in subs[0].choices:
            break
        parser = subs[0].choices[words[0]]
        words = words[1:]
    return parser, words


def test_every_command_resolves_against_the_real_parser():
    for s in S.SOURCES:
        parser, rest = _resolve(s.command)
        assert parser.prog != build_parser().prog, (s.kind, "no verb resolved")
        known = {o for a in parser._actions for o in a.option_strings}
        for w in rest:
            if w.startswith("--"):
                assert w in known, (s.kind, w)
        # a verb word left over that is not a placeholder, flag or value means a renamed verb
        stray = [w for w in rest if re.fullmatch(r"[a-z][a-z-]*", w)]
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        assert not (subs and stray and rest[0] == stray[0]), (s.kind, stray)


def test_collectors_resolve_once_the_queue_module_exists():
    """OD-queue lands `queue.py`; from then on every named collector must exist and be callable."""
    import importlib
    import importlib.util

    for s in S.SOURCES:
        mod, _, fn = s.collector.partition(":")
        assert re.fullmatch(r"collect_[a-z_]+", fn) and mod == "ddflow.services.digest.queue"
        if importlib.util.find_spec(mod) is not None:
            assert callable(getattr(importlib.import_module(mod), fn, None)), s.kind


def test_the_site_table_is_not_empty():
    """An empty `sites.SITES` would make 'every verb is covered' vacuously true."""
    assert len(sites.SITES) >= 10
