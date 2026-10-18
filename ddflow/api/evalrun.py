"""Run a workflow over the planted-fault fixtures and say whether it may be enabled by default
(SE-eval-harness.3). The fixtures are `evalfixture` records; scoring is `core.evalharness`; the
two numbers are `[evaluation]` knobs. A workflow here is any callable taking the fixture's
fields and returning its findings as strings, so a real gate and a `services.evalkit` fake run
the same way."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import evalharness as EH
from . import records as R

Workflow = Callable[[Mapping[str, Any]], Iterable[str]]


def fixtures(repo: Path, workflow: str = "") -> list[dict[str, Any]]:
    """The live fixture records (id and fields), those of ``workflow`` when given."""
    kind = R.KINDS["evalfixture"]
    rows = R.record_list(repo, kind, filters={"workflow": workflow}, limit=R.MAX_LIMIT).data.get(
        "rows", []
    )
    out = []
    for row in rows:
        shown = R.record_show(repo, kind, row["id"]).data
        out.append({"id": row["id"], **shown["fields"]})
    return out


def run(
    fixture_set: Iterable[Mapping[str, Any]], workflows: Mapping[str, Workflow]
) -> list[EH.Score]:
    """Score each fixture whose workflow is in ``workflows`` by running that workflow on it."""
    return [
        EH.score(f, workflows[f["workflow"]](f))
        for f in fixture_set
        if f.get("workflow") in workflows
    ]


def report(repo: Path, workflows: Mapping[str, Workflow]) -> dict[str, dict[str, Any]]:
    """The per-workflow miss-rate report over the recorded fixtures, with the verdict of each."""
    cfg = Config.load(repo)
    return EH.report(
        run(fixtures(repo), workflows),
        max_miss_rate=cfg.evaluation.max_miss_rate,
        min_fixtures=cfg.evaluation.min_fixtures,
    )
