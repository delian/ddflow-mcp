"""The generic record surface: one CLI word and one MCP tool carry every data kind.

``ddflow record <kind> <verb> [id]`` and ``ddflow_record`` (one tool, not one per kind: the
tools list is paid for in every agent's context) run the everyday verbs of any record kind
declared as data (`templates/kinds/*.toml`, `.ddflow/kinds/*.toml`). Fields are given as
``--set name=value`` (an array repeats the name); the work is `surfaces.records.dispatch`,
the same as a kind with commands of its own. A kind that has its own commands (``rule``)
is refused here and pointed at them.

The project's own kinds are loaded on each call (`api.record_kind_data.load_project`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..api import record_kind_data as KDA
from ..api import records as R
from ..core import kinddata as KD
from ..core import outcome as O
from . import records as SR

#: The verbs this surface carries: the seven everyday ones and a record's history. Supersede,
#: merge, split and restore need arguments of their own and come with their commands.
VERBS = (*R.VERBS[:7], "history")


def coerce(kind: R.RecordKind, items: Iterable[str]) -> dict[str, Any]:
    """``--set`` items as field values, typed by the kind's fields. Raises ValueError."""
    out: dict[str, Any] = {}
    for item in items or ():
        name, sep, raw = str(item).partition("=")
        spec = kind.field(name.strip())
        if not sep or spec is None:
            known = ", ".join(f.name for f in kind.fields)
            raise ValueError(
                f"set takes field=value for a field of {kind.name} ({known}): {item!r}"
            )
        if spec.type == "array":
            out.setdefault(spec.name, []).append(raw)
        elif spec.type == "integer":
            try:
                out[spec.name] = int(raw)
            except ValueError:
                raise ValueError(f"{spec.name} is a whole number; got {raw!r}") from None
        elif spec.type == "boolean":
            if raw.lower() not in ("true", "false"):
                raise ValueError(f"{spec.name} is true or false; got {raw!r}")
            out[spec.name] = raw.lower() == "true"
        else:
            out[spec.name] = raw
    return out


def _kind(name: str) -> R.RecordKind:
    kind = R.KINDS.get(name)
    if kind is None or kind.surfaced:
        data = ", ".join(sorted(n for n, k in R.KINDS.items() if not k.surfaced))
        if kind is not None:
            raise ValueError(f"{name} has commands of its own (ddflow {name} ...)")
        raise ValueError(f"unknown record kind {name!r}; the data kinds are {data}")
    return kind


def call(repo: Any, a: dict[str, Any], agent: str) -> O.Outcome:
    """One verb of one data kind; a malformed call is a failure with its reason."""
    verb = a.get("verb") or ""
    try:
        KDA.load_project(repo)
        kind = _kind(str(a.get("kind") or ""))
        if verb not in VERBS or verb not in kind.verbs:
            raise ValueError(
                f"verb must be one of {', '.join(v for v in VERBS if v in kind.verbs)}"
            )
        args = {
            k: a[k]
            for k in ("id", "query", "mode", "where", "all", "limit", "reason", "unset", "relation")
            if a.get(k) not in (None, "")
        }
        args |= {"verb": verb, **coerce(kind, a.get("set") or ())}
        answer = SR.answer_of(a) if verb == "add" else None
        return SR.dispatch(repo, kind, args, agent, answer=answer)
    except (ValueError, KD.KindDataError) as exc:
        return O.failed(f"record.{verb or 'call'}", str(exc))


def text(out: O.Outcome, a: dict[str, Any]) -> str | None:
    """The human form: what `surfaces.records` prints for that verb."""
    kind = R.KINDS.get(str(a.get("kind") or ""))
    if kind is None or out.exit != O.OK:
        return None
    return SR._human(kind, str(a.get("verb")), out)
