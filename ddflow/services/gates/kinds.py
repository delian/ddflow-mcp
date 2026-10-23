"""Which gates an item runs: the pipeline of its kind (the registry in
`config_sections._kinds` and `[gates].kind_pipelines`) and which of those gates apply to
the work it declares (`[gate.<id>] applies_when`)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ...config import Config
from ...config_sections._kinds import KINDS
from ...core.globs import overlap
from ...core.model import Item


def kind_pipeline(cfg: Config, kind: str) -> list[str]:
    """The gate ids an item of ``kind`` passes through: the ``[gates].kind_pipelines``
    entry when it names the kind, else the kind's built-in pipeline; an unregistered kind
    runs the task pipeline, as every kind did before the registry."""
    override = cfg.gates.kind_pipelines.get(kind)
    if override is not None:
        return list(override)
    spec = KINDS.get(kind)
    return list(getattr(cfg.gates, spec.base if spec else "task_pipeline"))


def kind_pipelines(cfg: Config) -> dict[str, list[str]]:
    """Every registered kind's effective pipeline, in registry order."""
    return {name: kind_pipeline(cfg, name) for name in KINDS}


def _no_branch(gdef: Any, item: Item) -> bool:
    """A gate that `needs_branch` on a PHASE that has none: a structural fact, not paths."""
    return bool(
        getattr(gdef, "needs_branch", False)
        and item.kind == "phase"
        and not (item.branch or item.worktree)
    )


def gate_applies(gdef: Any, item: Item) -> str:
    """Why ``gdef`` does NOT apply to ``item``, or ``""`` when it does.

    The predicate behind `[gate.<id>] applies_when`: a gate with path patterns applies
    only when one of them can cover one of the item's declared globs. An undefined gate, a
    gate without patterns and an item that declares no globs always apply, so a gate is
    never left out for want of information. The reason names the patterns and the globs it
    was judged against, because a gate that does not apply is reported, not silently
    absent.

    A gate that `needs_branch` (merge) does not apply to a PHASE with no branch and no
    worktree: a phase is a container that is never claimed, so it cannot have one.
    Derived from the item's recorded facts, so there is nothing to skip or hand-record. A
    task that has merely not been claimed yet still owes the gate."""
    if gdef is None:
        return ""
    if _no_branch(gdef, item):
        return f"no branch: the phase {item.id} has no branch of its own, so nothing to merge"
    if not gdef.applies_when or not item.globs:
        return ""
    if any(overlap(p, g) for p in gdef.applies_when for g in item.globs):
        return ""
    return (
        f"applies only to work touching {', '.join(gdef.applies_when)}; "
        f"{item.id} declares {', '.join(item.globs)}"
    )


#: What the actual change touches: a reader of the item's diff, called at most once per
#: judgement and only when a gate with `applies_when` needs it. None means git could not say.
DiffPaths = Callable[[], "list[str] | None"]


#: How many changed paths the recorded reason names before it counts the rest.
_SHOWN = 6


def _diff_judgement(gdef: Any, item: Item, paths: list[str] | None) -> tuple[bool, str]:
    """(covered, why-not) for ``gdef`` against the ACTUAL changed ``paths``.

    ``covered`` is True when some changed path can be covered by an `applies_when`
    pattern. A diff that cannot be read (None) or is empty says nothing -- "nobody looked"
    is not "no" -- so it is neither covered nor a reason: the caller keeps the gate in play.
    The reason names the changed paths and the patterns, which is the recorded evidence."""
    if not paths:
        return False, ""
    if any(overlap(pat, p) for p in paths for pat in gdef.applies_when):
        return True, ""
    shown = ", ".join(paths[:_SHOWN]) + (
        f" (+{len(paths) - _SHOWN} more)" if len(paths) > _SHOWN else ""
    )
    return False, (
        f"applies only to work touching {', '.join(gdef.applies_when)}; "
        f"the change of {item.id} touches only {shown}"
    )


def not_applicable(
    item: Item,
    pipeline: Iterable[str],
    gates: Mapping[str, Any] | None,
    *,
    required: Iterable[str] = (),
    diff: DiffPaths | None = None,
) -> dict[str, str]:
    """Gate -> why it does not apply, for the ``pipeline`` gates ``item`` does not run.

    A gate with a recorded outcome always applies: work already done is never hidden.

    A gate in ``required`` is never excused by the DECLARED globs alone: those are
    agent-editable (`ddflow update --globs`, no approval), so a required gate could be
    dropped by declaring the work elsewhere (B-na-required-gate). It is excused only when
    ``diff`` reads the ACTUAL changed paths and none of them can match `applies_when`; the
    reason names those paths and the patterns. No ``diff``, an unreadable one or an empty
    one keeps it owed. Any other gate is judged on the declared globs, and re-judged on
    ``diff`` when there is one: a change that touches its paths brings it back. A gate
    that cannot apply for a structural reason (a phase has no branch to merge) is not a
    matter of paths and is excused as before."""
    if not gates:
        return {}
    req = set(required)
    out: dict[str, str] = {}
    cache: list[list[str] | None] = []

    def actual() -> list[str] | None:
        if not cache:
            cache.append(diff() if diff is not None else None)
        return cache[0]

    for gid in pipeline:
        if item.gate_outcome(gid):
            continue
        gdef = gates.get(gid)
        why = gate_applies(gdef, item)
        if not why:
            continue
        if gdef is not None and gdef.applies_when and not _no_branch(gdef, item):
            by_diff = ""
            if diff is not None:
                covered, by_diff = _diff_judgement(gdef, item, actual())
                if covered:
                    continue
            if gid in req:
                if not by_diff:
                    continue
                why = by_diff
        out[gid] = why
    return out


#: The forms `[gate.<id>] requires_evidence` accepts.
EVIDENCE_FORMS: tuple[str, ...] = ("report", "link", "fields")

#: Evidence keys that carry a report: ddflow computes `output_digest` from the output file
#: a caller attaches, and a check that produced its own digest names it `report_digest`.
REPORT_KEYS: tuple[str, ...] = ("report_digest", "output_digest")

#: What counts as no answer at all in a required evidence field.
BOILERPLATE: frozenset[str] = frozenset(
    {"", "-", "--", "n/a", "na", "none", "nil", "null", "ok", "okay", "lgtm", "tbd", "todo", "pass"}
)


def record_owner(state: Any) -> Callable[[str], str | None]:
    """Record id -> the item it belongs to, for the `link` form: the research notes of
    ``state`` (a note carries its item); any id it does not know has no owner."""
    return lambda rid: n.item if (n := state.research.get(rid)) else None


def evidence_forms(gdef: Any) -> tuple[str, ...]:
    """The evidence forms ``gdef`` requires, in `EVIDENCE_FORMS` order: those it lists, and
    "fields" whenever it names ``evidence_fields``. An unknown form is ignored here and
    reported by `evidence_problems`, so a typo is loud rather than a silent no-op."""
    if gdef is None:
        return ()
    named = set(gdef.requires_evidence) | ({"fields"} if gdef.evidence_fields else set())
    return tuple(f for f in EVIDENCE_FORMS if f in named)


def _filled(value: Any) -> bool:
    return isinstance(value, str | int | float) and str(value).strip().lower() not in BOILERPLATE


def bare(evidence: Mapping[str, Any] | None) -> bool:
    """Is a pass carrying ``evidence`` bare? Nothing the caller observed: no entry, or only
    `reviewed_paths`, which ddflow measures itself for a hand-recorded agent gate
    (`handrecord`) and which says nothing about what was done."""
    return not any(k != "reviewed_paths" for k in evidence or {})


def evidence_problems(
    gdef: Any,
    evidence: Mapping[str, Any],
    item_id: str,
    owner_of: Callable[[str], str | None] | None = None,
) -> list[str]:
    """Why a PASS carrying ``evidence`` is refused for ``gdef``, one sentence per unmet
    form; empty when it carries what the gate requires (or requires nothing).

    ``evidence`` is what the CALLER supplied, never ddflow's own measurements: those are
    always present, so counting them made every bare pass look evidenced. The forms:

    * ``report`` -- a non-empty ``report_digest`` or ``output_digest``;
    * ``link`` -- ``link``, a record id or a list of them, at least one of which
      ``owner_of`` says belongs to ``item_id``. Without ``owner_of`` nothing can be
      resolved, so a link requirement is not met: a check that could not run does not pass;
    * ``fields`` -- every name in ``gdef.evidence_fields`` filled in, in
      ``evidence["fields"]`` or at the top level, with something other than ``BOILERPLATE``.
      Every missing field is listed at once."""
    if gdef is None:
        return []
    out = [
        f"requires_evidence names {form!r}, which is not one of {', '.join(EVIDENCE_FORMS)}"
        for form in gdef.requires_evidence
        if form not in EVIDENCE_FORMS
    ]
    forms = evidence_forms(gdef)
    checks = (
        ("report", lambda: _report_problem(evidence)),
        ("link", lambda: _link_problem(evidence, item_id, owner_of)),
        ("fields", lambda: _fields_problem(gdef, evidence)),
    )
    out += [why for form, check in checks if form in forms and (why := check())]
    return out


def _report_problem(evidence: Mapping[str, Any]) -> str:
    # An attached output that is empty or only whitespace (`--output-file /dev/null`) digests
    # to a value like any other, but it reports nothing. `output_evidence` keeps the last
    # characters of the output as `tail`: blank, the output ends in nothing worth reading,
    # and when it is no longer than that tail the whole of it is blank.
    tail = evidence.get("tail")
    empty = evidence.get("output_bytes") == 0 or (isinstance(tail, str) and not tail.strip())
    if any(_filled(evidence.get(k)) and not (empty and k == "output_digest") for k in REPORT_KEYS):
        return ""
    return "a report: attach the output (`--output-file`, not blank) or name its digest"


def _link_problem(
    evidence: Mapping[str, Any], item_id: str, owner_of: Callable[[str], str | None] | None
) -> str:
    raw = evidence.get("link") or []
    ids = [raw] if isinstance(raw, str) else [r for r in raw if isinstance(r, str)]
    if owner_of is None:
        return "a linked record, which cannot be checked here"
    if any(owner_of(rid) == item_id for rid in ids):
        return ""
    named = f"{', '.join(ids)} is not linked to" if ids else "no record is linked to"
    return f"a record linked to {item_id} ({named} it)"


def _fields_problem(gdef: Any, evidence: Mapping[str, Any]) -> str:
    given = evidence.get("fields")
    given = given if isinstance(given, Mapping) else {}
    missing = [n for n in gdef.evidence_fields if not _filled(given.get(n, evidence.get(n)))]
    return f"filled-in field(s): {', '.join(missing)}" if missing else ""
