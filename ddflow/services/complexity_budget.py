"""Warn at commit time when a touched function nears radon's grade D (B85bb921feb).

`tests/test_architecture_guards.py` counts every function above cyclomatic complexity 20
and fails when a module's count rises, but it runs with the rest of the suite: a one-line
`if` in a function already at 20 tripped it only in the 15-minute ci gate. This reads the
functions of the staged ``.py`` files against HEAD and names those whose complexity ROSE to
``[enforce].complexity_warn_at`` or more, while the agent can still fix it. Advisory: it
never refuses a commit. radon is a dev dependency; without it nothing is said (and nothing
is claimed clean).

radon is imported only in `ddflow.adapters.complexity` (the `extras-one-adapter` contract);
``cc_visit`` and ``_complexities`` are re-exported here, so ``complexities`` reads one
function. A test that must stand in for radon re-points ``cc_visit`` on THIS module.
"""

from __future__ import annotations

from collections.abc import Callable

from ..adapters.complexity import _complexities, cc_visit

__all__ = ["GRADE_D_FROM", "GUARD", "SHOWN", "advice", "complexities", "risen"]

#: radon's grade D starts at 21: what the architecture guard counts.
GRADE_D_FROM = 21
SHOWN = 5
GUARD = "tests/test_architecture_guards.py"


def complexities(source: bytes | None) -> dict[str, tuple[int, int]]:
    """``{qualified function name: (line, complexity)}``; empty when radon is missing."""
    return _complexities(source, cc_visit)


def risen(
    paths: list[str],
    staged: Callable[[str], bytes | None],
    head: Callable[[str], bytes | None],
    warn_at: int,
) -> list[str]:
    """One line per function in ``paths`` whose complexity rose to ``warn_at`` or more."""
    if warn_at <= 0:
        return []
    out: list[str] = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        before = complexities(head(path))
        for name, (line, cc) in complexities(staged(path)).items():
            was = before.get(name, (0, 0))[1]
            if cc >= warn_at and cc > was:
                if cc < GRADE_D_FROM:
                    verdict = f"{GRADE_D_FROM - cc} below grade D"
                elif was < GRADE_D_FROM:
                    verdict = (
                        f"grade D: counted by {GUARD}, which fails when a module's count rises"
                    )
                else:  # already counted by the guard; it fails only when a module's count rises
                    verdict = "already grade D, getting worse"
                out.append(f"  {path}:{line} {name} CC {was or 'new'} -> {cc} ({verdict})")
    return out


def advice(lines: list[str]) -> str:
    """The commit-hook note for ``risen``'s lines ('' for none)."""
    if not lines:
        return ""
    more = f"\n  ... and {len(lines) - SHOWN} more" if len(lines) > SHOWN else ""
    return (
        "complexity budget (warning only): a function you touched is at or near radon grade D "
        f"(CC {GRADE_D_FROM}+), which {GUARD} counts:\n"
        + "\n".join(lines[:SHOWN])
        + more
        + "\nSplit it before the ci gate finds it; `[enforce].complexity_warn_at = 0` turns this off."
    )
