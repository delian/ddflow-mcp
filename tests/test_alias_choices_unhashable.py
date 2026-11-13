"""`AliasChoices` treats a positional `nargs='*'` remainder (a list) as a value, not a
command name (Bab15b39e03): it must not be hashed as a dict key."""

from __future__ import annotations

from ddflow.surfaces.registry import (
    AliasChoices,
    Command,
    Param,
    SuggestingParser,
    add_commands,
    used_aliases,
)


def test_a_positional_nargs_remainder_is_not_hashed_as_a_command():
    choices = AliasChoices({"show": object()})
    assert ["a", "b"] not in choices  # TypeError: unhashable type: 'list' on the unfixed code
    assert choices.get(["a", "b"]) is None


def test_used_aliases_survives_a_command_with_a_nargs_remainder():
    cmd = Command(
        path=("echo",),
        summary="echo the rest untouched",
        params=(Param("rest", positional=True, nargs="*"),),
        handler=lambda a, c: 0,
    )
    root = SuggestingParser(prog="ddflow")
    add_commands(root.add_subparsers(dest="cmd", required=True), [cmd])
    typed = ["echo", "a", "b"]
    assert used_aliases(root, root.parse_args(typed), typed) == []
