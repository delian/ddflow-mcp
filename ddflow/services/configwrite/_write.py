"""The judgement chain and the one write operation.

Split out of ``ddflow/services/configwrite.py``. Everything else only reads: this is
where an edit is refused or composed, and where `apply_edit` -- the single way ddflow
changes a config file -- replaces it atomically under a lock. The guards that need the
whole repo (workflow, gate table, human-approval, reviewer-trust, attributes) come
together here, judged on the RESULT rather than the state being replaced.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ...config import Config, InvalidValue
from ...infra import tomlcfg as TC
from .. import reviewer_trust as RT
from .. import shared_files as SF
from .. import workflow as WF
from ..approvals import config_guard as AG
from ._edits import (
    DEFAULT_GUARDS,
    Block,
    Edit,
    EditResult,
    Guards,
    ReviewerRefusal,
    SetPairs,
    _check_reviewer_fields,
    _compose,
)
from ._keys import (
    _GATE_KEY_PARTS,
    KeyRefused,
    _gate_key_problem,
    _key_parts,
    appended_key_problem,
    plain_key_problem,
)
from ._problems import (
    _appended_keys,
    _gate_table_problems,
    _guarded_human_gates,
    _human_cleared,
    _new_range_problems,
    _parsed,
    _range_refusal,
    _render_gate_problem,
    _workflow_problems,
    _written_keys,
    format_problem,
)
from .layers import ensure_local_dir


def _key_refusal(pairs: tuple[tuple[str, object], ...]) -> str:
    """Why these `--set` keys are refused before anything is read, or "".

    `gate.<id>.human` is not editable from here, and this is the one knob that is
    special. Everything else in the file is a preference; that flag decides whether a
    checkpoint belongs to the operator, and `ddflow_configure` is on the MCP surface. Two
    calls -- flip it false, then `ddflow_gate_record` -- cleared a human gate with no
    shell involved. Declaring the gate in `.ddflow/gates.toml` is the supported way, and
    no tool writes that file. Normalised, so whitespace and quoting cannot walk past the
    guard: `TC.upsert` strips the segments, so `" gate.x.human"` and `gate.x."human"`
    reach the same TOML key as the bare form and are refused the same way.
    """
    keyed = [(k, str(v)) for k, v in pairs]
    # The key itself first: plain keyboard keys only (D-plain-keys), exit 3 -- so the exit
    # code says "not a plain key" whatever field the key names.
    problem = next((plain_key_problem(k) for k, _v in keyed if plain_key_problem(k)), "")
    problem = problem or _gate_key_problem(keyed)
    if problem:
        return problem
    blocked = [
        k
        for k, _v in keyed
        if (pp := _key_parts(k))
        and len(pp) >= _GATE_KEY_PARTS
        and pp[0] == "gate"
        and pp[-1] == "human"
    ]
    if blocked:
        return _human_refusal("edit", blocked)
    return ""


def _human_refusal(verb: str, blocked: list[str]) -> str:
    return (
        f"refusing to {verb} {', '.join(blocked)}: whether a gate is a human "
        f"checkpoint is the operator's decision, not a configurable preference. "
        f"Set `human` in .ddflow/gates.toml, which no tool writes."
    )


def _text_refusal(text: str) -> str:
    """Why TOML text to append is refused before anything is read, or ""."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return f"not valid TOML: {exc}"
    # Plain keyboard keys only (D-plain-keys), exactly as `--set` judges them: exit 3.
    if problem := appended_key_problem(text):
        return problem
    if blocked := _human_cleared(data):
        return _human_refusal("append", blocked)
    return ""


def _schema_problem(edit: Edit, text: str, path: Path) -> str:
    """Why the RESULT is not a loadable config (`Config.check`, strict for what the edit
    writes), or "". A `KeyRefused` for a known knob with a value it cannot take."""
    said = "that edit" if isinstance(edit, SetPairs) else "appending this"
    try:
        result = tomllib.loads(text)
        if isinstance(edit, SetPairs):
            Config.check(result, written=_written_keys(edit.pairs))
        else:
            data = tomllib.loads(edit.text)
            Config.check(result, written=_appended_keys(data))
            _check_reviewer_fields(data, path)
    except InvalidValue as exc:  # a known knob, a value it cannot take: refused
        return KeyRefused(f"{said} would break the config: {exc}")
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return f"{said} would break the config: {exc}"
    return ""


def _human_removed(repo: Path, edit: Edit, prev: str, text: str, layer: str) -> str:
    """The refusal for an edit that REMOVES a human-approval gate from a pipeline, or "".

    Refusing the FLAG was not enough. `workflow drop <human-gate>` took the checkpoint out
    of the pipeline, exit 0, and `workflow pipeline task <list without it>` does the same
    by omission, and the local file is read after the committed `gates.toml`, so an
    appended `[gates] task_pipeline` there could drop it on this machine with nothing in
    any diff to show it. Checked on the RESULT, for every kind of edit, at the choke point.
    """
    local = layer == "local"
    removed = sorted(
        _guarded_human_gates(repo, prev, local=local)
        - _guarded_human_gates(repo, text, local=local)
    )
    if not removed:
        return ""
    if isinstance(edit, SetPairs):
        return (
            f"that edit would remove the human-approval gate(s) "
            f"{', '.join(removed)} from the pipeline. Whether the operator's "
            f"approval step exists is not a configurable preference — edit "
            f".ddflow/gates.toml, which no tool writes."
        )
    return (
        f"appending this would remove the human-approval gate(s) "
        f"{', '.join(removed)} from the pipeline. Edit .ddflow/gates.toml, "
        f"which no tool writes."
    )


def _gate_problem(prev: str, text: str) -> str:
    """The NEW `[gate.*]` table problems of a result, or "". `gate` is a foreign table to
    `Config.check`, so a gate block is judged here, on the parsed result: `gate.a.b.command`
    (however spelled) wrote `[gate.a.b]`, exit 0, and every later command refused to load
    it (B72b8adba30). Only NEW problems: an existing one must not stop the edit that
    repairs it."""
    new = _gate_table_problems(_parsed(text)) - _gate_table_problems(_parsed(prev))
    if not new:
        return ""
    said = sorted(_render_gate_problem(*p) for p in new)
    # Every problem at once; a key refusal keeps exit 3.
    return (KeyRefused if any(isinstance(m, KeyRefused) for m in said) else str)("; ".join(said))


def _workflow_refusal(repo: Path, before: set[str], text: str, layer: str) -> str:
    """The refusal for problems an edit INTRODUCES into the workflow (`workflow.check`), or "".

    `workflow.check` is the MEANING; `Config.check` the SCHEMA. Without it
    `workflow gate X --required` (with no pipeline) exited 0 having created the exact inert
    requirement `ddflow workflow` then reports as a problem. Only NEW problems are
    refused, so a config already broken can still be repaired by the tool that reports it."""

    introduced = sorted(_workflow_problems(repo, text, local=layer == "local") - before)
    if not introduced:
        return ""
    return (
        "that edit would leave the workflow incoherent:\n  "
        + "\n  ".join(introduced)
        + f"\nNothing was written. ({WF.PROBLEM} findings are refused at "
        f"the point of writing; `ddflow workflow` reports any that are "
        f"already there.)"
    )


def _result_problem(
    repo: Path, edit: Edit, layer: str, path: Path, prev: str, text: str, guards: Guards
) -> str:
    """Why the composed ``text`` may not replace ``prev`` in ``path``, or "".

    The order is the whole point: judge the RESULT, never the state being replaced -- a
    writer that validates what is on disk has checked nothing. The same judgements for
    every kind of edit; a dedicated file (`reviewers.toml`) carries no config, so only its
    syntax and its reviewer blocks are judged.
    """
    if path.name != "config.toml":
        try:
            tomllib.loads(text)
            _check_reviewer_fields(_parsed(edit.text) if isinstance(edit, Block) else {}, path)
        except tomllib.TOMLDecodeError as exc:
            return f"refusing to write {path}: the result is not valid TOML: {exc}"
        except ValueError as exc:
            return str(exc)
        return ""
    local = layer == "local"
    before = _workflow_problems(repo, prev, local=local) if guards.workflow else set()
    if problem := _schema_problem(edit, text, path):
        return problem
    if new_range := _new_range_problems(repo, prev, text, local):
        return KeyRefused(_range_refusal(new_range))
    return (
        _gate_problem(prev, text)
        or KeyRefused(AG.problem(_parsed(prev), _parsed(text)))
        or _human_removed(repo, edit, prev, text, layer)
        or (_workflow_refusal(repo, before, text, layer) if guards.workflow else "")
    )


def apply_edit(
    repo: Path,
    edit: Edit,
    *,
    layer: str = "file",
    guards: Guards = DEFAULT_GUARDS,
    dry_run: bool = False,
    person: bool = False,
    agent: str = "",
) -> EditResult:
    """The one way ddflow changes a config file: ``edit`` applied to ``layer``'s file.

    ``layer`` is `"file"` (the committed `.ddflow/config.toml`, which every clone gets) or
    `"local"` (the git-ignored `.ddflow/local/config.toml`: this machine's endpoints, hosts
    and sizing, which must never reach the committed file). The same guards apply to
    both, judged on the committed config with the local one over it.

    Compose every edit, judge the RESULT once, then replace the file atomically under a
    lock; a truncating write interrupted halfway would leave an empty config that loads as
    "no overrides at all" without saying so. Nothing is written when anything is refused
    (``EditResult.error``) or on ``dry_run``. A committed write also brings
    `.gitattributes` up to date with `[lease] append_only_globs`.

    Whether the writer is ``person`` or an ``agent`` decides what the reviewer-trust guard
    allows (decision D-reviewer-trust).
    """
    repo = Path(repo)
    refusal = _key_refusal(edit.pairs) if isinstance(edit, SetPairs) else _text_refusal(edit.text)
    if refusal:
        return EditResult(refusal)
    own = edit.own if isinstance(edit, Block) and layer == "local" and edit.own else "config.toml"
    path = TC.layer_path(repo, layer, own)
    if layer == "local":
        # Even for a dry run: the lock below creates the directory anyway, and a `local/`
        # that exists without its own `*` ignore is one `git add` from committing whatever
        # lands there next.
        ensure_local_dir(repo)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    with TC.locked(path):
        prev = path.read_text("utf-8") if path.exists() else ""
        if refused := format_problem(prev, path):
            return EditResult(refused, prev)
        text, failure = _compose(prev, edit)
        failure = failure or _result_problem(repo, edit, layer, path, prev, text, guards)
        if failure:
            return EditResult(failure, text)
        if not dry_run:
            try:
                # The one place a tool writes config: under the reviewer-trust guard. An
                # agent's `kind = "command"` reviewer is undone and refused, and any
                # reviewer whose identity a tool created or changed is recorded, to count
                # only after a person approves it.
                RT.write(repo, path, text, person=person, agent=agent)
            except RT.ReviewerRefused as exc:
                return EditResult(ReviewerRefusal(str(exc)), text)
    added: list[str] = []
    if layer == "file" and own == "config.toml" and not dry_run:
        added = SF.sync_attributes(repo)
    return EditResult("", text, path, added)
