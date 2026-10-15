"""Gate-config drift: a branch whose gate definition differs from the one the gate ran."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...infra import worktree as W
from .defs import GateDef

#: The `[gate.<id>]` keys that decide what a command gate RUNS and how its exit reads.
#: Only these count as drift: a reworded prompt on main must not turn a real failure on
#: an older branch into "unavailable".
_RUN_FIELDS = frozenset(
    {"command", "cwd", "env", "timeout_s", "unavailable_exits", "partial_exits",
     "require_output", "fail_output"}
)  # fmt: skip


def _run_spec(texts: Iterable[str], gate_id: str) -> dict[str, Any]:
    """The run-deciding part of `[gate.<gate_id>]` across `texts`, later winning."""
    spec: dict[str, Any] = {}
    for text in texts:
        try:
            block = (tomllib.loads(text).get("gate") or {}).get(gate_id)
        except tomllib.TOMLDecodeError:
            continue
        if isinstance(block, dict):
            spec.update(block)
    return {k: v for k, v in spec.items() if k in _RUN_FIELDS}


def _read_text(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except (OSError, ValueError):
        return ""


def gate_config_drift(gate_id: str, tree: Path) -> dict[str, Any]:
    """How the gate definition `load_gates` ran differs from the one `tree` commits.

    `{}` when it does not, or `tree` is the primary, or neither side COMMITTED a change
    since the fork (the difference is an uncommitted edit). Otherwise `kind` says whose
    change it is: "behind" -- the base COMMITTED a new definition since `tree` forked
    and the tree still carries the one it forked with, so its code and dependencies
    predate the command that ran -- or "own", the branch changed the definition itself,
    which merging the base cannot fix.

    Needed because the definition comes from the PRIMARY (`repo_root`) while the command
    runs in the item's tree: main switching unit_tests to `-n 48` alongside adding
    pytest-xdist failed every older branch with `unrecognized arguments: -n 48`,
    recorded as a test failure (bug B8ea7a90aea). The config-knob half of the same
    class warns "merge main" from `config._warn_unknown`.
    """
    # Function-level (deferred, as it was before the area split).
    from ...infra import tomlcfg

    try:
        primary = W.repo_root(tree)
    except (W.GitError, OSError):
        return {}
    if primary.resolve() == Path(tree).resolve():
        return {}
    # The layers `load_gates` reads. Every spec compared below carries the PRIMARY's
    # machine-local layer on top: it is git-ignored, applies whichever tree's committed
    # files it lands on, and wins -- so a committed change it overrides never ran, and
    # calling that drift turned a real failure into "unavailable" (bug
    # B-drift-ignores-local-layer).
    paths = tomlcfg.config_paths(primary, "gates.toml")
    committed = [str(p.relative_to(primary)) for p in paths[:2]]
    local = [_read_text(p) for p in paths[2:]]

    def spec(committed_texts: Iterable[str]) -> dict[str, Any]:
        return _run_spec([*committed_texts, *local], gate_id)

    ran = spec(_read_text(primary / f) for f in committed)
    here = spec(_read_text(Path(tree) / f) for f in committed)
    if ran == here:
        return {}
    # The primary's HEAD by SHA: the name `HEAD` means the tree's own HEAD inside `tree`.
    head = W.git(primary, "rev-parse", "HEAD").out
    base = W.git(primary, "rev-parse", "--abbrev-ref", "HEAD").out
    base = base if base and base != "HEAD" else head[:12] or "the primary checkout"
    fork = W.git(tree, "merge-base", "HEAD", head) if head else None
    if not (fork and fork.ok and fork.out):
        return {}

    def at(rev: str) -> dict[str, Any]:
        return spec(W.git(tree, "show", f"{rev}:{f}").out for f in committed)

    # Classified on COMMITS alone. `ran` and `here` include uncommitted edits, and
    # letting them decide mislabelled both ways: an operator's uncommitted tweak in the
    # primary on top of main's committed change hid the drift, and an agent's
    # half-made edit in the tree turned a branch that is behind into one "changing the
    # gate itself" (bug B-drift-dirty-trees).
    #
    # "behind" first: when the base committed a change, the base's definition is what
    # ran, and the tree's code predates it whatever the tree edited itself -- checking
    # "own" first recorded exactly the original bare failure for a branch that had also
    # touched, say, the timeout (bug B-drift-own-masks-behind).
    forked = at(fork.out)
    if at(head) != forked:
        kind = "behind"
    elif at("HEAD") != forked:
        kind = "own"
    else:
        # Neither side committed a change: the difference is an uncommitted edit -- an
        # operator who just set the command. Merging the base would bring nothing, and
        # the command that ran is the one configured: its failure is a failure
        # (tests/test_cli.py).
        return {}
    return {"kind": kind, "base": base, "ran": ran, "tree": here}


def _account_for_drift(gdef: GateDef, cwd: Path, code: int, out: str, ev: dict[str, Any]) -> str:
    """The outcome of a FAILED run once gate-config drift is known; notes it in `ev`.

    Behind: the failure is the tree's age, not its tests -- UNAVAILABLE, naming the
    remedy, as a tool that could not run is. Own: the failure stands (the branch's
    definition takes effect only when it merges), but says which command ran, or the
    author debugs a command their tree no longer contains.
    """
    drift = gate_config_drift(gdef.id, cwd)
    if not drift:
        return "failed"
    ev["gate_config_drift"] = drift
    base = drift["base"]
    last = next((ln for ln in reversed(out.strip().splitlines()) if ln.strip()), "")
    ran = f"`{gdef.command}` exited {code}" + (f": {last[:160]}" if last else "")
    if drift["kind"] == "behind":
        ev["reason"] = (
            f"your branch is behind a gate-config change on {base}: gate {gdef.id!r} ran "
            f"{base}'s definition, which this branch's code and dependencies predate "
            f"({ran}). NOT a test failure: merge {base} into this branch, then re-run."
        )
        return "unavailable"
    ev["reason"] = (
        f"this branch changes gate {gdef.id!r}'s definition, but gates run {base}'s until "
        f"it merges: {ran}. " + (f"{ev['reason']}" if ev.get("reason") else "")
    ).strip()
    return "failed"
