"""The kind registry: an item's kind -> the gate pipeline it runs.

One registry answers "which pipeline does this kind of item pass through?" (D-unify
kind-pipeline). A kind starts on one of the two built-in pipelines (``task_pipeline`` or
``phase_pipeline``) and `[gates].kind_pipelines` replaces it per kind, so a document, a
research item or a scheduled job gets its own gates as data rather than as another
branch in `pipeline_for`. The promotion pipeline is not a kind: it is chosen by what an
item carries (`promote_to`), and stays ahead of this lookup.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KindSpec:
    """One registered item kind and the built-in pipeline it falls back to."""

    name: str
    #: The `[gates]` field holding the pipeline this kind runs when `kind_pipelines`
    #: does not name it.
    base: str
    doc: str


KINDS: dict[str, KindSpec] = {
    spec.name: spec
    for spec in (
        KindSpec("task", "task_pipeline", "a unit of work with its own worktree"),
        KindSpec("phase", "phase_pipeline", "a group of tasks, closed as a whole"),
        KindSpec("bug", "task_pipeline", "a recorded defect and its fix"),
        KindSpec("doc", "task_pipeline", "a project document"),
        KindSpec("research", "task_pipeline", "a recorded question and its verdict"),
        KindSpec("job", "task_pipeline", "a scheduled or manual job"),
    )
}


def kind_pipelines_problem(value: object) -> str:
    """Why a ``[gates].kind_pipelines`` value is not usable, or ``""``. Unregistered kind
    names are refused: a typo would otherwise configure a pipeline nothing ever runs."""
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, list) and all(isinstance(g, str) for g in v)
        for k, v in value.items()
    ):
        return 'must be a table of lists of gate ids, e.g. { doc = ["implement", "merge"] }'
    unknown = sorted(set(value) - set(KINDS))
    if unknown:
        return f"unknown item kind {unknown[0]!r}; the kinds are {', '.join(KINDS)}"
    return ""
