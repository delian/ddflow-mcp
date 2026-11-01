"""An edit's value types and its composition onto the file's text.

Split out of ``ddflow/services/configwrite.py``. The three kinds of edit (`SetPairs`,
`AppendText`, `Block`), the typed result, the reviewer-trust refusal, and the functions
that splice an edit onto the text so it can be judged as a RESULT before anything is
written. None of these reach for the surrounding repo state.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ...config import RENAMED, Config
from ...infra import fsio
from ...infra import tomlcfg as TC
from ..review import Reviewer


class Spelled(str):
    """A value as a PERSON typed it (`config --set key 5`): written as the TOML literal it
    spells when it is one (a number, a boolean, an array, an inline table), else quoted.
    Any other value is typed: a `str` is always a string (`--command 5` is the text "5"),
    and a list, bool or number is written as such (`tomlcfg.value`)."""


def _literal(value: object) -> str:
    return TC.literal(value) if isinstance(value, Spelled) else TC.value(value)


@dataclass(frozen=True)
class SetPairs:
    """Set each `<section>.<key>` in turn, in place: comments and layout stay."""

    pairs: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pairs", tuple((k, v) for k, v in self.pairs))


@dataclass(frozen=True)
class AppendText:
    """Append a TOML document to the layer's `config.toml`."""

    text: str


@dataclass(frozen=True)
class Block:
    """Append a hand-built block (a `[[reviewer]]`). In the local layer it goes to ``own``,
    the dedicated machine-local file (`reviewers.toml`); the committed layer's target is
    always `.ddflow/config.toml`, the one committed file a project is configured in."""

    text: str
    own: str = ""


Edit = SetPairs | AppendText | Block


@dataclass(frozen=True)
class Guards:
    """Which optional judgements an edit is held to. The key, human-gate, format, schema,
    range and gate-table guards are not optional."""

    #: Refuse an edit that leaves the workflow incoherent (`workflow.check`).
    workflow: bool = True


DEFAULT_GUARDS = Guards()


@dataclass
class EditResult:
    """What `apply_edit` did. ``error`` is empty on success and then nothing was refused;
    a `KeyRefused` is a refusal (exit 3), a `ReviewerRefusal` an agent's refused reviewer (the
    caller picks its exit), any other string a failure. ``text`` is the file's text (as it would be, on a dry run);
    ``path`` the file written."""

    error: str = ""
    text: str = ""
    path: Path = field(default_factory=Path)
    #: `merge=union` lines added to `.gitattributes` by a committed write.
    attributes_added: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return not self.error


class ReviewerRefusal(str):
    """A refusal by the reviewer-trust guard (D-reviewer-trust): an agent's command reviewer."""


def _apply_edits(text: str, pairs: tuple[tuple[str, object], ...]) -> tuple[str, str]:
    """``text`` with every ``(dotted, value)`` set in turn, and what stopped it, if
    anything: (new text, "") or (text so far, the refusal)."""
    for dotted, value in pairs:
        if "." not in dotted:
            return text, f"{dotted!r} is not <section>.<key>, e.g. gate.unit_tests.command"
        try:
            # A renamed knob is written under its CURRENT key (D-compat); the old spelling
            # still works as an argument.
            key = RENAMED[dotted][0] if dotted in RENAMED else dotted
            text = TC.upsert(text, key, _literal(value))
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            return text, f"that edit would break the config: {exc}"
    # The next write moves what the file still holds under an old key (D-compat).
    try:
        text, _ = TC.move_keys(
            text, {old: new for old, (new, _s, _r) in RENAMED.items()}, live=Config()._sections()
        )
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return text, f"that edit would break the config: {exc}"
    return text, ""


def _check_reviewer_fields(data: dict, path: Path) -> None:
    """ValueError for a `[[reviewer]]` entry in ``data`` with a field `Reviewer` lacks.

    Strict whichever layer it goes to: `Config.check` passes over `[[reviewer]]` (another
    module's table), and read back an unknown field is skipped with a warning in the local
    layer and fails every load in a committed file of ddflow's own tree -- either way not
    what was meant (B96fd182086). A writer knows every field it may write.
    """
    blocks = data.get("reviewer")
    if blocks is None:
        return
    # `[reviewer]` (one bracket) or `reviewer = "x"` is read as no reviewer at all.
    if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
        raise ValueError(f"`reviewer` in {path} must be `[[reviewer]]` blocks (two brackets)")
    for n, raw in enumerate(blocks, 1):
        TC._check(raw, set(Reviewer.__dataclass_fields__), f"[[reviewer]] #{n} of {path}")


def _compose(prev: str, edit: Edit) -> tuple[str, str]:
    """`(the file's text with ``edit`` applied, the refusal or "")`."""
    if isinstance(edit, SetPairs):
        return _apply_edits(prev, edit.pairs)
    if isinstance(edit, Block):
        return fsio.APPEND.splice(prev, edit.text.rstrip() + "\n"), ""
    return (prev.rstrip() + "\n\n" if prev.strip() else "") + edit.text.strip() + "\n", ""
