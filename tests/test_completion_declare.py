"""The completion declaration ratchet: every id-like or closed-set argument resolves to a
completion kind by construction, and an argument that must not complete says so in the
exemption list. A new command with an id-like parameter needs no completion change."""

from __future__ import annotations

import argparse

from ddflow.services.completion import kinds
from ddflow.surfaces import completion_exemptions as EX
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.registry import Command, Param, add_commands


def _walk(parser, path=()):
    """Every action of every command path, as ``(path, action)``."""
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, child in a.choices.items():
                yield from _walk(child, (*path, name))
        elif a.option_strings or not isinstance(a, argparse._SubParsersAction):
            yield path, a


def _record_kind(path: tuple[str, ...]) -> str | None:
    """The record kind a command path belongs to (``bug show`` -> ``bug``), if any."""
    return next((w for w in path if w in kinds.id_kinds()), None)


def _resolve(path: tuple[str, ...], action: argparse.Action) -> str:
    declared = getattr(action, "complete", "") or ""
    metavar = action.metavar if isinstance(action.metavar, str) else None
    return declared or kinds.infer(
        action.dest or "",
        choices=action.choices,
        metavar=metavar,
        positional=not action.option_strings,
        record_kind=_record_kind(path),
    )


def _flagged(parser) -> list[tuple[tuple[str, ...], str]]:
    """The arguments that look completable but resolve to free text and are not exempt."""
    out: list[tuple[tuple[str, ...], str]] = []
    for path, action in _walk(parser):
        name = (action.option_strings or [action.dest])[0] if action.option_strings else (action.dest or "")
        metavar = action.metavar if isinstance(action.metavar, str) else None
        kind = _resolve(path, action)
        looks = action.choices is not None or kinds.looks_like_id(action.dest or "", metavar=metavar)
        if looks and kind == "none" and EX.exempt(path, name) is None:
            out.append((path, name))
    return out


def test_no_free_text_id_like_argument_is_silently_skipped():
    parser = build_parser()
    parsed = [a for _path, a in _walk(parser)]
    id_like = [
        a
        for a in parsed
        if kinds.looks_like_id(a.dest or "", metavar=(a.metavar if isinstance(a.metavar, str) else None))
    ]
    assert id_like, "the ratchet's id-like filter matches the real parser (non-vacuous)"
    flagged = _flagged(parser)
    assert flagged == []


def test_every_exemption_names_a_real_argument():
    """Each exemption matches a real argument that would otherwise be flagged (shrink-only)."""
    parser = build_parser()
    real = {
        (path, (action.option_strings or [action.dest])[0] if action.option_strings else (action.dest or ""))
        for path, action in _walk(parser)
    }
    for entry in EX.EXEMPTIONS:
        assert (entry.path, entry.argument) in real, f"unused exemption {entry.path}{entry.argument}"


def test_inference_resolves_every_choices_argument_to_enum():
    parser = build_parser()
    resolved = [(path, a) for path, a in _walk(parser)]
    assert resolved  # the walk reaches the real parser, at scale
    for path, action in resolved:
        if action.choices is not None and not getattr(action, "complete", ""):
            assert _resolve(path, action) == "enum", (path, action.dest)


def test_declared_complete_is_attached_to_the_action():
    """A declared `Param.complete` reaches the argparse action verbatim."""
    subparsers = argparse.ArgumentParser(prog="x").add_subparsers(dest="cmd", required=True)
    add_commands(
        subparsers,
        [
            Command(
                path=("probe",),
                summary="probe",
                params=(Param("bug", positional=True, complete="id:bug"),),
                handler=lambda a, c: 0,
            ),
        ],
    )
    probe = subparsers.choices["probe"]
    positional = [a for a in probe._actions if not a.option_strings][0]
    assert positional.complete == "id:bug"


def test_canary_a_new_id_parameter_completes_with_no_completion_code():
    """A registrable synthetic record kind is inferred by name, with no per-command code."""
    before = kinds.id_kinds()
    try:
        kinds.declare_id_kind("widget")
        assert kinds.infer("widget") == "id:widget"
        assert kinds.infer("id", positional=True, record_kind="widget") == "id:widget"
        assert kinds.looks_like_id("widget")
    finally:
        # do not leak the synthetic kind into the registry the other tests read
        kinds._id_kinds -= {"widget"}
        assert kinds.id_kinds() == before


def test_infer_vocabulary():
    assert kinds.infer("item") == "id:item"
    assert kinds.infer("point", choices=("a", "b")) == "enum"
    assert kinds.infer("repo") == "dir"
    assert kinds.infer("globs") == "path"
    assert kinds.infer("agent") == "agent"
    assert kinds.infer("freeform") == "none"
    assert kinds.infer("id", positional=True, record_kind="bug") == "id:bug"
    assert kinds.normalize("phase") == "id:phase"


def test_an_id_flag_without_a_positional_metavar_is_inferred():
    assert kinds.infer("id") == "id"
    assert kinds.infer("extends", metavar="ID") == "id"
    assert kinds.infer("decision_id") == "id:decision"
