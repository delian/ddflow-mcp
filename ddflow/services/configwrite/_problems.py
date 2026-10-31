"""What is wrong with a candidate config text, judged BEFORE it becomes the state.

Split out of ``ddflow/services/configwrite.py``. These read a candidate ``text`` (the
result an edit would produce, or a raw document to append) and report the problems it
would have: an incoherent workflow, an inconsistent adaptive range, a guarded human
gate placed or removed, a table problem, or a config format newer than this ddflow
understands. None of them writes.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from ...config import CONFIG_FORMAT, RENAMED, Config, parallel_range_problems
from .. import install_info as II
from .. import workflow as WF
from ..gates import GateDef, load_gates, pipelined
from ._keys import KeyRefused, _key_parts, gate_id_problem
from .layers import config_file


def _human_cleared(data: dict) -> list[str]:
    """Every `gate.<id>.human` an appended TOML document sets to anything but `true`.

    Only the clearing direction: declaring a NEW checkpoint by appending
    `human = true` is how a project adds one (tests/test_human_gate.py does exactly
    that), and making a gate stricter takes nothing from the operator.
    """
    gates = data.get("gate") if isinstance(data, dict) else None
    if not isinstance(gates, dict):
        return []
    return [
        f"gate.{gid}.human"
        for gid, spec in gates.items()
        if isinstance(spec, dict) and "human" in spec and spec["human"] is not True
    ]


def _effective(repo: Path, text: str, local: bool):
    """`(data, cfg)` for a candidate TEXT of the file being written.

    A committed write is judged ALONE: it is what every other clone gets, and a local
    layer that happens to mask a problem here masks nothing there. A local write is
    judged MERGED over the committed config, because it only ever takes effect on top
    of it -- a local `task_pipeline` that drops a committed human gate is a deletion
    of the operator's checkpoint even though the local file never names the gate.
    """

    data = tomllib.loads(text)
    cfg = Config()
    if local:
        committed = config_file(repo)
        if committed.is_file():
            cfg._apply(tomllib.loads(committed.read_text("utf-8")), "file")
        cfg._apply(data, "local")
    else:
        cfg._apply(data, "file")
    return data, cfg


def _range_problems(repo: Path, text: str, local: bool) -> set[tuple[str, str]]:
    """The auto range problems (`config.parallel_range_problems`) a candidate TEXT would
    have, judged as `_effective` judges it. Raises what `_effective` raises."""
    return set(parallel_range_problems(_effective(repo, text, local)[1]))


def _new_range_problems(repo: Path, before: str, after: str, local: bool) -> set:
    """Range problems the edit INTRODUCES. Only new ones are refused, so a project whose
    range was already inconsistent can still make an unrelated edit, or the repair.

    A local write is judged over the committed layer; when that layer cannot be read
    (it does not parse, say -- the load path reports it), the edit is judged on its own
    over the shipped defaults instead, so a broken sibling never switches the guard off;
    and when the text BEFORE the edit does not load, only the problems the result has
    beyond those the committed layer brings are the edit's (a repair is never refused).
    """
    unreadable = (tomllib.TOMLDecodeError, ValueError, OSError)

    def judged(text: str, over_committed: bool) -> set | None:
        try:
            return _range_problems(repo, text, over_committed)
        except unreadable:
            return None

    found = judged(after, local)
    if found is None:  # the sibling layer is broken: judge the edit over the defaults
        local = False
        found = judged(after, False) or set()
    had = judged(before, local)
    if had is None:  # the text before the edit does not load: what it inherited stays
        had = judged("", local) or set()
    return found - had


def _range_refusal(problems: set[tuple[str, str]]) -> str:
    said = "; ".join(f"invalid {key}: {why}" for key, why in sorted(problems))
    return (
        f"that edit would make the adaptive range inconsistent: {said}. In auto, "
        "schedule.max_parallel_min <= schedule.max_parallel_tasks <= "
        "schedule.max_parallel_max; change the bound first, or set schedule.parallel fixed."
    )


def _workflow_problems(repo: Path, text: str, *, local: bool = False) -> set[str]:
    """The workflow problems a given config TEXT would have. Never raises.

    Loaded from the text rather than from disk, so the result can be judged before it
    becomes the state. A text that will not even load has no workflow problems to
    report -- `Config.check` is what catches that, and reporting it twice in different
    words is how an operator learns to read neither message.
    """

    try:
        data, cfg = _effective(repo, text, local)
        gates = load_gates(repo, cfg)
        # `load_gates` overlays `[gate.*]` from the FILE, and this text is not on disk
        # yet -- so a gate being defined in the very same call was invisible, and
        # defining `lint` and piping it in one command refused itself for naming an
        # undefined gate. Judge the candidate, not the predecessor.
        WF.apply_gate_table(gates, data.get("gate") or {})
        return {f"{f.subject}: {f.detail}" for f in WF.check(cfg, gates) if f.level == WF.PROBLEM}
    except Exception:
        return set()


def _parsed(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _gate_table_problems(data: dict) -> set[tuple[str, str, str]]:
    """What is wrong with the `[gate.*]` blocks of a parsed config, STRUCTURALLY: `(kind,
    gate, field)` for an id that is not a bare key, a table where a field belongs (a
    dotted id nested one: `[gate.a.b]`), or a field no gate has. Tuples, not messages,
    so a pre-existing `[gate."a.b"]` cannot mask a new nested `[gate.a.b]` that would
    render the same words (roborev on 45abe764)."""

    known = set(GateDef.__dataclass_fields__)
    out: set[tuple[str, str, str]] = set()
    for gid, spec in (data.get("gate") or {}).items():
        if gate_id_problem(gid):
            out.add(("id", gid, ""))
            continue
        if not isinstance(spec, dict):
            continue
        for name, value in spec.items():
            if name == "env":
                continue
            if isinstance(value, dict):
                out.add(("nested", gid, name))
            elif name not in known:
                out.add(("unknown", gid, name))
    return out


def _render_gate_problem(kind: str, gid: str, name: str) -> str:
    if kind == "id":
        return gate_id_problem(gid)
    if kind == "nested":
        return gate_id_problem(f"{gid}.{name}")
    return f"[gate.{gid}] has no field {name!r}"


def _guarded_human_gates(repo: Path, text: str, *, local: bool = False) -> set[str]:
    """Human gates that a given config TEXT places in a pipeline. Never raises.

    Used to refuse an edit that would REMOVE one. Blocking `gate.<id>.human` was not
    enough: `workflow drop plan_approved` took the checkpoint out of the pipeline
    entirely, exit 0, and `workflow pipeline task <list-without-it>` does the same by
    omission. Whether the operator's approval step exists is the operator's decision, and
    the flag and the pipeline membership are two ways of saying it.
    """

    try:
        data, cfg = _effective(repo, text, local)
        gates = load_gates(repo, cfg)
        WF.apply_gate_table(gates, data.get("gate") or {}, only=("human",))
        # Every `gates.*_pipeline` (gates.pipelined), derived rather than listed: naming
        # task and phase missed `promotion_pipeline` -- where a deploy sign-off belongs.
        return {g for g in pipelined(cfg) if g in gates and gates[g].is_human_gate}
    except Exception:
        return set()


def _written_keys(pairs: list[tuple[str, str]]) -> set[str]:
    """The dotted keys an edit writes, each under its CURRENT spelling: what `Config.check`
    judges strictly, so a key the file already holds that this version does not know is left
    alone (D-compat 2)."""
    return {".".join(_key_parts(RENAMED[k][0] if k in RENAMED else k)) for k, _v in pairs}


def _appended_keys(data: dict) -> set[str]:
    """The dotted keys an appended TOML document writes (a table's keys, one level down; an
    `[export.<doc>]` table's keys, two), as `Config.check` takes `written`."""
    out: set[str] = set()
    for sec, values in data.items():
        out.add(sec)
        if not isinstance(values, dict):
            continue
        for knob, v in values.items():
            out.add(f"{sec}.{knob}")
            if isinstance(v, dict):
                out |= {f"{sec}.{knob}.{k}" for k in v}
    return out


def format_problem(text: str, path: Path) -> str:
    """Why this file may not be WRITTEN by this ddflow, or "": its `format = N` is newer than
    the config format this code understands (`CONFIG_FORMAT`), so an edit could rewrite a
    section's newer shape (D-compat 2: refused only on a direct conflict). Reading is never
    refused. Exit 3, with advice fitted to how this ddflow is installed."""
    try:
        fmt = tomllib.loads(text).get("format", 1) if text.strip() else 1
    except tomllib.TOMLDecodeError:
        # A file that does not parse is not a licence to write it: read the declaration
        # off the top of the text, before the first table.
        top = re.split(r"(?m)^\s*\[", text, maxsplit=1)[0]
        m = re.search(r"(?m)^\s*format\s*=\s*(\d+)\s*(?:#.*)?$", top)
        fmt = int(m.group(1)) if m else 1
    if isinstance(fmt, bool) or not isinstance(fmt, int) or fmt <= CONFIG_FORMAT:
        return ""
    advice = II.upgrade_advice()
    return KeyRefused(
        f"refusing to write {path}: it is config format {fmt}, and this ddflow understands "
        f"format {CONFIG_FORMAT}; an edit could lose what a newer ddflow wrote. "
        f"{advice[:1].upper()}{advice[1:]}"
    )
