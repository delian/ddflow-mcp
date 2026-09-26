"""Writing the project's TOML config, safely — the one place that edits it.

Split out of `cli.py` because it is machinery, not a command: `cmd_config` uses it, and
so do `workflow pipeline`, `workflow gate` and `workflow drop`, which are edits to the
same file wearing a friendlier name. Four call sites reaching into one 4,300-line module
for a private helper is how that module got to 4,300 lines.

The order inside `_write_config` is the whole point — compose every edit, validate the
RESULT, then replace the file atomically under a lock. A writer that validates the state
it is replacing has checked nothing, and a truncating write interrupted halfway leaves an
empty config that loads as "no overrides at all" without saying so.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ...config import Config
from ...infra import tomlcfg as TC
from ..context import FAIL, OK, Ctx


def _toml_literal(value: str) -> str:
    """A TOML literal for `value`, quoting it unless it already is one."""
    if re.fullmatch(r"(true|false|-?\d+(\.\d+)?|\[.*\]|\{.*\})", value.strip()):
        return value
    return json.dumps(value)  # quote + escape as a TOML basic string


def _is_header(line: str, header: str) -> bool:
    """Is this line the `[section]` header, allowing a trailing comment?

    `[gates]  # how work is checked` did not match an exact compare, so the upsert
    appended a SECOND `[gates]` -- and TOML forbids declaring a table twice, which means
    every later edit failed with a parse error pointing at a line the operator did not
    write. Commenting your own config should not disable the tool that edits it.
    """
    return line.split("#", 1)[0].strip() == header


#: TOML's two multi-line string delimiters. Built rather than written so this module's
#: own source does not have to escape them.
_TRIPLES = ('"' * 3, "'" * 3)


def _toml_lines(text: str) -> list[tuple[str, bool]]:
    """Every line paired with "is this line INSIDE a multi-line string?".

    The one thing a TOML line editor has to know. A hand-written agent prompt is a
    triple-quoted value, and a line inside one reading `command = <what you ran>`
    matched the key scan -- so `--command` was written INTO the prompt, the real key
    was never set, and `ddflow workflow` reported the result coherent. Exit 0: a
    silently dropped knob, and every future task handed a tampered instruction. A `[`
    in the same prose ended the section scan and inserted the new key mid-sentence.

    Counts delimiters rather than parsing. The alternative is a TOML round-trip, and
    the one thing worse than a line editor here is a writer that silently discards the
    comments this file carries most of its reasoning in.
    """
    out: list[tuple[str, bool]] = []
    delim = ""
    for raw in text.splitlines():
        inside = bool(delim)
        rest = raw
        while rest:
            if delim:
                hit = rest.find(delim)
                if hit < 0:
                    break
                rest = rest[hit + 3 :]
                delim = ""
                continue
            starts = [(rest.find(d), d) for d in _TRIPLES if d in rest]
            if not starts:
                break
            at, d = min(starts)
            if "#" in rest[:at]:
                break  # the opener is inside a comment
            delim = d
            rest = rest[at + 3 :]
        out.append((raw, inside))
    return out


def _outside_quotes(raw: str) -> str:
    """`raw` with quoted strings and the trailing comment removed.

    The bracket counter above must not see a `[` that is part of a VALUE. It used to:
    `body.count("[")` on `command = "sed \'s/\\[//g\'"` started the depth at 1, no
    later line ever brought it back to 0 (a `[section]` header contributes +1 then -1),
    and `_value_span` returned `len(lines)` — so replacing that key replaced everything
    from it to END OF FILE. Sibling keys and whole later sections were deleted, the
    result was still valid TOML, `Config.check` passed, the workflow diff was empty,
    and the write succeeded with exit 0.

    Probed: a `[gate.lint]` with `command = "sed \'s/\\[//g\'"` plus `timeout_s` and
    `title`. Re-setting `command` left `['command']` and dropped the other two, silently.

    Gate commands are arbitrary operator shell strings, so `sed \'s/\\[//g\'`,
    `cut -d\'[\' -f1` and `grep -F \'[\'` are all ordinary things to find in one. A
    comment marker inside a quoted string is the same hazard in the other direction,
    which is why the `#` split happens HERE rather than before the quote scan.
    """
    out, quote, esc = [], "", False
    for ch in raw:
        if quote:
            if esc:
                esc = False
            elif ch == "\\" and quote != "'":
                esc = True  # literal (single-quoted) TOML strings have no escapes
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "#":
            break  # a comment, now that we know we are not inside a string
        out.append(ch)
    return "".join(out)


def _value_span(lines: list[tuple[str, bool]], start: int) -> int:
    """The index one past the end of the value beginning at `lines[start]`.

    A value spans lines two ways: a bracketed array written one entry per line, which
    is how a person writes a ten-gate pipeline, and a multi-line string. Replacing only
    the first line left the rest orphaned -- `task_pipeline = ["x"]` followed by a
    stray `]` -- which TOML rejects, so every later edit was refused with a parse error
    blaming the operator for the editor's mistake.
    """
    depth = 0
    for i in range(start, len(lines)):
        raw, inside = lines[i]
        body = "" if inside else _outside_quotes(raw)
        depth += body.count("[") + body.count("{") - body.count("]") - body.count("}")
        open_string = i + 1 < len(lines) and lines[i + 1][1]
        if depth <= 0 and not open_string:
            return i + 1
    return len(lines)


def _toml_upsert(text: str, dotted: str, literal: str) -> str:
    """Set one `<section>.<key>` in TOML text, in place, preserving comments.

    Pure, and takes TEXT rather than a path, so several edits compose into one write.
    Applying them one file-write at a time would leave the config half-updated when the
    third of four is rejected -- and a half-applied workflow change is the state nobody
    can reason about.
    """
    section, _, key = dotted.rpartition(".")
    lines = _toml_lines(text)
    header = f"[{section}]"
    try:
        start = next(
            i for i, (ln, inside) in enumerate(lines) if not inside and _is_header(ln, header)
        )
    except StopIteration:
        body = "\n".join(ln for ln, _ in lines).rstrip()
        return (f"{body}\n\n" if body else "") + f"{header}\n{key} = {literal}\n"
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if not lines[i][1] and lines[i][0].lstrip().startswith("[")
        ),
        len(lines),
    )
    i = start + 1
    while i < end:
        raw, inside = lines[i]
        stripped = raw.lstrip()
        if inside or stripped.startswith(("#", ";")) or "=" not in stripped:
            i += 1
            continue
        if stripped.split("=")[0].strip() == key:
            lines[i : _value_span(lines, i)] = [(f"{key} = {literal}", False)]
            return "\n".join(ln for ln, _ in lines).rstrip() + "\n"
        i = max(i + 1, _value_span(lines, i))
    lines.insert(end, (f"{key} = {literal}", False))
    return "\n".join(ln for ln, _ in lines).rstrip() + "\n"


def _workflow_problems(repo: Path, text: str) -> set[str]:
    """The workflow problems a given config TEXT would have. Never raises.

    Loaded from the text rather than from disk, so the result can be judged before it
    becomes the state. A text that will not even load has no workflow problems to
    report -- `Config.check` is what catches that, and reporting it twice in different
    words is how an operator learns to read neither message.
    """
    import tomllib

    from ...services import workflow as WF
    from ...services.gates import GateDef, load_gates

    try:
        data = tomllib.loads(text)
        cfg = Config()
        cfg._apply(data, "file")
        gates = load_gates(repo, cfg)
        # `load_gates` overlays `[gate.*]` from the FILE, and this text is not on disk
        # yet -- so a gate being defined in the very same call was invisible, and
        # defining `lint` and piping it in one command refused itself for naming an
        # undefined gate. Judge the candidate, not the predecessor.
        for gid, spec in (data.get("gate") or {}).items():
            g = gates.get(gid) or GateDef(id=gid)
            for field, value in (spec or {}).items():
                if hasattr(g, field):
                    setattr(g, field, value)
            gates[gid] = g
        return {f"{f.subject}: {f.detail}" for f in WF.check(cfg, gates) if f.level == WF.PROBLEM}
    except Exception:
        return set()


#: `gate.<id>.human` — three segments minimum. Named so the guard's shape is legible
#: rather than a bare `3` in a boolean chain.
_GATE_KEY_PARTS = 3


def _guarded_human_gates(repo: Path, text: str) -> set[str]:
    """Human gates that a given config TEXT places in a pipeline. Never raises.

    Used to refuse an edit that would REMOVE one. Blocking `gate.<id>.human` was not
    enough: `workflow drop plan_approved` took the checkpoint out of the pipeline
    entirely, exit 0, and `workflow pipeline task <list-without-it>` does the same by
    omission. Whether the operator's approval step exists is the operator's decision, and
    the flag and the pipeline membership are two ways of saying it.
    """
    import tomllib

    from ...services.gates import load_gates

    try:
        data = tomllib.loads(text)
        cfg = Config()
        cfg._apply(data, "file")
        gates = load_gates(repo, cfg)
        for gid, spec in (data.get("gate") or {}).items():
            if gid in gates and isinstance(spec, dict) and "human" in spec:
                gates[gid].human = bool(spec["human"])
        in_pipeline = set(cfg.gates.task_pipeline) | set(cfg.gates.phase_pipeline)
        return {g for g in in_pipeline if g in gates and gates[g].is_human_gate}
    except Exception:
        return set()


def _write_config(
    repo: Path, pairs: list[tuple[str, str]], *, dry_run: bool = False, check_workflow: bool = True
) -> tuple[str, str]:
    """Apply every `(dotted, value)` edit, validate ONCE, write ONCE, under a lock.

    Returns `(error, new_text)`; a non-empty error means nothing was written. The order
    is the whole point -- compose, check the RESULT, then replace the file atomically --
    because a writer that validates the state it is replacing has checked nothing, and
    a truncating write interrupted halfway leaves an empty config that loads as "no
    overrides at all" without saying so.

    Two validations, not one. `Config.check` is the SCHEMA: is every section and knob
    real. `workflow.check` is the MEANING: does the result hang together. Without the
    second, `workflow gate X --required` (with no pipeline) exited 0 having created the
    exact inert requirement that `ddflow workflow` then reports as a problem -- the
    writer manufacturing a defect its own reader diagnoses.

    Only NEW problems are refused. Refusing on any problem at all would mean a config
    already broken could never be repaired by the tool that reports it broken.
    """
    import tomllib

    # `gate.<id>.human` is not editable from here, and this is the one knob that is
    # special. Everything else in this file is a preference; that flag decides whether
    # a checkpoint belongs to the operator, and `ddflow_configure` is on the MCP
    # surface. Two calls -- flip it false, then `ddflow_gate_record` -- cleared a human
    # gate with no shell involved, which made the docstring claim that the ordinary
    # path is closed simply untrue. Declaring the gate in `.ddflow/gates.toml` is the
    # supported way, and that file is not writable from any tool.
    # Normalised, so whitespace and quoting cannot walk past the guard: `_toml_upsert`
    # strips the segments, so `" gate.x.human"` and `gate.x."human"` reach the same TOML
    # key as the bare form and must be refused the same way.
    def _key_parts(k: str) -> list[str]:
        return [seg.strip().strip("\"'") for seg in k.split(".")]

    blocked = [
        k
        for k, _v in pairs
        if (pp := _key_parts(k))
        and len(pp) >= _GATE_KEY_PARTS
        and pp[0] == "gate"
        and pp[-1] == "human"
    ]
    if blocked:
        return (
            f"refusing to edit {', '.join(blocked)}: whether a gate is a human "
            f"checkpoint is the operator's decision, not a configurable preference. "
            f"Set `human` in .ddflow/gates.toml, which no tool writes.",
            "",
        )

    from ...services import workflow as WF

    path = repo / ".ddflow" / "config.toml"
    with TC.locked(path):
        text = path.read_text("utf-8") if path.exists() else ""
        before = _workflow_problems(repo, text) if check_workflow else set()
        human_before = _guarded_human_gates(repo, text)
        for dotted, value in pairs:
            if "." not in dotted:
                return f"{dotted!r} is not <section>.<key>, e.g. gate.unit_tests.command", text
            text = _toml_upsert(text, dotted, _toml_literal(value))
        try:
            Config.check(tomllib.loads(text))
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            return f"that edit would break the config: {exc}", text
        # Refusing the FLAG was not enough. `workflow drop <human-gate>` took the
        # checkpoint out of the pipeline, exit 0, and `workflow pipeline task <list
        # without it>` does the same by omission — two ways to delete the operator's
        # approval step without ever touching `human`. Checked on the RESULT, at the
        # choke point, because a guard in one branch is a guard the other branch does
        # not have, which is how the first version of this shipped.
        removed = sorted(human_before - _guarded_human_gates(repo, text))
        if removed:
            return (
                f"that edit would remove the human-approval gate(s) "
                f"{', '.join(removed)} from the pipeline. Whether the operator's "
                f"approval step exists is not a configurable preference — edit "
                f".ddflow/gates.toml, which no tool writes.",
                text,
            )
        if check_workflow:
            introduced = sorted(_workflow_problems(repo, text) - before)
            if introduced:
                return (
                    "that edit would leave the workflow incoherent:\n  "
                    + "\n  ".join(introduced)
                    + f"\nNothing was written. ({WF.PROBLEM} findings are refused at "
                    f"the point of writing; `ddflow workflow` reports any that are "
                    f"already there.)",
                    text,
                )
        if not dry_run:
            TC.atomic_write(path, text)
    return "", text


def _config_set(a, c: Ctx) -> int:
    """`ddflow config --set <section>.<key> <value>` — edit one key in place.

    Exists because appending is not always possible: TOML forbids a duplicate table, so
    once a section is present the documented "append a block" path fails. Editing in
    place also preserves the surrounding comments, which for this file carry most of
    the reasoning.
    """
    err, _text = _write_config(c.repo, [(a.set, a.value)])
    if err:
        print(err, file=sys.stderr)
        return FAIL
    c.out(
        f"{a.set} = {_toml_literal(a.value)}",
        {"key": a.set, "value": a.value, "path": str(c.repo / ".ddflow" / "config.toml")},
    )
    return OK
