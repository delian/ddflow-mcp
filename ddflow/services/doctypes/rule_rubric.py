"""The ``rubric`` validate rule: a named scored rubric, with a minimum score."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from ..docscheck_types import Finding
from .rubrics import RUBRICS
from .validate import Ctx, rule


@rule("rubric", "name", "min_score", section=False)
def rubric(ctx: Ctx, r: Mapping[str, Any]) -> Iterator[Finding]:
    name, floor = r.get("name"), r.get("min_score", 1.0)
    if not isinstance(name, str) or name not in RUBRICS:
        yield ctx.finding("rubric", 1, f"unknown rubric {name!r}. Known: {', '.join(RUBRICS)}", r)
        return
    if not isinstance(floor, (int, float)) or isinstance(floor, bool):
        yield ctx.finding("rubric", 1, "min_score must be a number", r)
        return
    checks = RUBRICS[name][1](ctx.doc)
    met = sum(ok for _, ok in checks)
    if checks and met / len(checks) < floor:
        miss = ", ".join(c for c, ok in checks if not ok)
        yield ctx.finding("rubric", 1, f"{name} scored {met}/{len(checks)}; missing {miss}", r)
