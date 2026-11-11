"""`pins regenerate`: the application-layer entry (DX-pins-regenerate)."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core import outcome as O
from ..core.events import SkewRefused
from ..infra import git as G
from ..infra.fsio import LockTimeout
from ..services import approval as AP
from ..services import drift as DR
from ..services import leasekeep as LK
from ..services import mergedrivers as MD
from ..services import pins as PN
from ..services import pins_merge as PM
from ..services import pins_written as PW
from ..services.approvals import class_states as APCS
from ..services.approvals import consume as CN
from ..services.approvals.canon import Canon
from ._base import _load


def pins_regenerate(  # noqa: PLR0913 -- one keyword per regeneration facet; the door's flags
    repo: Path,
    *,
    only: str = "",
    check: bool = False,
    install: bool = False,
    after_merge: bool = False,
    verb: str = "regenerate",
    target: str = "",
    start: Path | None = None,
    agent: str = "",
    approval: str = "",
    on_progress=None,
) -> O.Outcome:
    """Re-record the stale pin files; with ``check`` list them (exit 1 when any)."""
    if verb == "merge-driver":
        code = PM.merge_driver(target)
        return O.ok("pins", driver=True) if code == 0 else O.failed("pins", "no file to keep")
    # The working tree the caller stands in, not the primary checkout `repo` resolves to: a
    # pin file is re-recorded where the code under test is.
    primary = repo  # the event log lives here; `repo` becomes the caller's own tree below
    top = G.run(start, "rev-parse", "--show-toplevel") if start else None
    if top is not None and top.ok and top.out.strip():
        repo = Path(top.out.strip())
    cfg = Config.load(repo)
    if verb == "drivers":
        return _drivers(repo, cfg, check=check, install=install)
    if install:
        actions = PM.install(repo, cfg)
        if not actions:
            return O.nothing("pins", "nothing to install: no pin recipes, or already installed")
        return O.ok("pins", installed=actions)
    refused = _skew_refusal(primary, repo, agent, check=check, after_merge=after_merge)
    if refused is not None:
        return refused
    return _regenerate_tail(
        primary,
        repo,
        cfg,
        only=only,
        check=check,
        after_merge=after_merge,
        verb=verb,
        agent=agent,
        approval=approval,
        on_progress=on_progress,
    )


def _drivers(repo: Path, cfg: Config, *, check: bool, install: bool) -> O.Outcome:
    """`pins drivers`: show the merge-driver lines; `--install` (the consent) writes them;
    `--check` verifies the merged baselines are still sorted and unique."""
    if check and install:
        return O.failed(
            "pins",
            "--check and --install are separate steps: run `ddflow pins drivers --install`, then `ddflow pins drivers --check` (MCP: ddflow_pins document=drivers install=true, then check=true)",
        )
    if check:
        bad = MD.check_baselines(repo, cfg)
        return (
            O.failed("pins", "; ".join(bad))
            if bad
            else O.ok("pins", note="baselines are sorted and unique")
        )
    if install:
        gaps = [f"unavailable: {u}" for u in MD.plan(repo, cfg).unavailable]
        acts = MD.apply(repo, cfg)
        if acts:
            return O.ok("pins", installed=acts, note="\n".join(gaps))
        if gaps:  # a driver that could not be set up is not "already set up"
            return O.nothing("pins", "; ".join(gaps))
        return O.nothing("pins", "merge drivers already set up")
    p = MD.plan(repo, cfg)
    note = "\n".join(p.lines()) or "nothing to add: the merge drivers are already set up"
    if p.unavailable and p.empty():
        note = "\n".join(p.lines())  # the unavailable lines, never "already set up"
    if not p.empty():
        note += f"\nwrite these with: {MD.NEXT_CLI}  (MCP: {MD.NEXT_MCP})"
    return O.ok("pins", note=note)


def _regenerate_tail(
    primary: Path,
    repo: Path,
    cfg,
    *,
    only: str,
    check: bool,
    after_merge: bool,
    verb: str,
    agent: str,
    approval: str,
    on_progress=None,
) -> O.Outcome:
    """Regenerate (or check) the pin files, from the snapshot on."""
    files = PN.pin_files(repo, cfg)
    # `.gitattributes` is watched with the pins: the line sync_attributes adds is a regeneration
    # output, committed without a lease like the pin files (DX-pins-gitattributes-dirty).
    watched = [*files, ".gitattributes"]
    before = PW.snapshot(repo, watched) if not check else {}
    if after_merge:
        return _after_merge_note(primary, repo, cfg, before, watched, check=check)
    stamp: dict[str, object] = {}
    # An agent re-records pins only with a valid approval; omitting ``--approval`` must not
    # bypass the pins-are-stale gate (B90b8d3fe89: the check used to apply only when an
    # approval WAS passed, so a bare agent `pins regenerate` re-recorded pins ungated). The
    # check runs BEFORE anything is written: a refused regenerate must touch nothing, not
    # even `.gitattributes` (roborev finding: the recipe write used to land first).
    if (
        (approval or AP.agent_marker(agent))
        and verb == "regenerate"
        and not check
        and not after_merge
    ):
        bad = _regenerate_approval(
            primary, agent=agent, before=before, only=only, approval=approval
        )
        if isinstance(bad, O.Outcome):
            return bad
        stamp = bad
    # attributes follow the recipes (the hook is `--install`'s), even when the regeneration then
    # cannot run; what is written is reported. A refused `--only nosuch` writes nothing.
    attr_actions = [] if check else _follow_the_recipes(repo, cfg, only)
    tick = _lease_tick(primary, agent)
    rep = PN.regenerate(repo, cfg, only=only, check=check, tick=tick, on_progress=on_progress)
    if not check:
        _record_written(primary, repo, before, watched)
    if rep.problem:
        return O.failed("pins", rep.problem)
    out = _regenerate_outcome(rep, check, attr_actions)
    _attach_behind_warning(out, primary, repo, cfg, check)
    if stamp and out.exit == O.OK and not check:
        out.data["approval"] = stamp
    return out


def _after_merge_note(primary: Path, repo: Path, cfg, before, watched, *, check: bool) -> O.Outcome:
    """Run what the post-merge hook runs, on the ``after_merge`` route."""
    if _linked_worktree(repo):
        # a task worktree's merge of main: its pins are regenerated by `ddflow pins regenerate`,
        # never by the hook running tests against the worktree's index (B683c75d7b3)
        return O.ok(
            "pins",
            note="pins: post-merge hook skipped in a linked worktree; run `ddflow pins regenerate`",
        )
    note = PM.after_merge(repo, cfg)
    _record_written(primary, repo, before, watched)
    return O.ok("pins", note=note)


def _regenerate_approval(primary: Path, *, agent: str, before, only: str, approval: str):
    """The pins-are-stale class route (verb pins.regenerate): consume an operator approval of
    re-recording ``before`` with ``only`` (``approval`` = request or id), else a person."""
    log, cfg, _st = _load(primary, agent)
    if not AP.agent_marker(agent):
        return O.refused("pins", "--approval is an agent's route; a person re-records directly")
    canon = Canon(
        verb="pins.regenerate",
        agent=log.agent_id,
        item=only,
        args={"only": only},
        state=APCS.pins_state(before),
    )
    decision = CN.operator_gate(log, cfg, canon, approval_id=approval)
    if not decision.ok:
        return O.refused("pins", decision.message)
    return decision.stamp


def _follow_the_recipes(repo: Path, cfg, only: str = "") -> list[str]:
    """Add the `merge=pinsregen` line of every recipe file `.gitattributes` lacks, when the
    project uses the driver at all and ``only`` (if given) names a recipe; the lines written."""
    attrs = repo / ".gitattributes"
    if not (attrs.exists() and "merge=pinsregen" in attrs.read_text("utf-8")):
        return []
    try:
        if only and only not in {r.name for r in PN.load_recipes(repo, cfg)}:
            return []
    except ValueError:  # recipes that do not parse: the run reports it
        return []
    return PM.sync_attributes(repo, cfg)


def _skew_refusal(
    primary: Path, repo: Path, agent: str, *, check: bool, after_merge: bool
) -> O.Outcome | None:
    """The refusal when an older binary would re-record pins, else None. A check writes
    nothing, and a post-merge hook in a linked worktree records nothing: neither is refused."""
    if check or (after_merge and _linked_worktree(repo)):
        return None
    reason = _older_binary_refusal(primary, agent)
    return O.refused("pins", reason) if reason else None


def _older_binary_refusal(primary: Path, agent: str) -> str:
    """Why this ddflow must not re-record pins, "" when it may: the log was worked on by a
    NEWER ddflow, whose pin outputs (brief, knob table, transcripts) this one would
    re-record differently, a PATCH behind included (B-path-ddflow-version-skew-noise). The
    skew policy and a recorded session override apply as for `adopt`. The message names the
    cause: usually a stale `ddflow` on PATH, not the project's own."""
    try:
        log, _cfg, _st = _load(primary, agent)
        log.check_skew()
    except SkewRefused as exc:
        return (
            f"pins regenerate refused: the `ddflow` running is older than the log's "
            f"(a stale ddflow on PATH?) and would re-record the pins differently. {exc}"
        )
    except Exception:  # a log this cannot read is not a version skew to refuse on
        return ""
    return ""


def _behind_warning(primary: Path, repo: Path, cfg) -> str:
    """A warning when a re-recording runs on a tree behind its base: the pins it writes may
    record drift from the out-of-date base along with this item's own change, and nothing
    else says the branch lags the base (Bf4ee89e08e)."""
    try:
        base = DR.base_of(primary, repo, cfg)
    except Exception:  # an unreadable log is not a reason to fail the regeneration
        return ""
    if not base:
        return ""
    n = G.run(repo, "rev-list", "--count", f"HEAD..{base}")
    if not n.ok or not n.out.isdigit() or int(n.out) <= 0:
        return ""
    return (
        f"ddflow: this branch is {n.out} commit(s) behind `{base}`: pins regenerated here "
        "may record drift from the out-of-date base along with this item's own change. "
        f"Merge `{base}` first when the drift is not this item's."
    )


def _attach_behind_warning(out: O.Outcome, primary: Path, repo: Path, cfg, check: bool) -> None:
    """Put the behind-base warning on a re-recording's note, off the ``--check`` route too."""
    if check or out.exit != O.OK:
        return
    warn = _behind_warning(primary, repo, cfg)
    if warn:
        out.data["note"] = warn


def _linked_worktree(repo: Path) -> bool:
    """True when ``repo`` is a linked worktree: its ``.git`` is a gitfile that points into ``<common>/.git/worktrees/`` (a separate-git-dir
    checkout or a submodule is not one). Read
    from the path, not from git, so a stray GIT_DIR in the environment cannot change the answer."""
    try:
        text = (repo / ".git").read_text()
    except OSError:  # a directory, or no .git
        return False
    return text.startswith("gitdir:") and "/.git/worktrees/" in text.replace("\\", "/")


def _record_written(primary: Path, repo: Path, before, files) -> None:
    """The lease-free commit of what was just re-recorded (B1579b8a1a9). The record sits in the
    primary checkout, which is where the commit hook reads it from. Best effort: a record that
    cannot be written costs the exemption, never the regeneration that succeeded."""
    try:
        PW.record(primary, PW.written(repo, before, files))
    except (OSError, LockTimeout):
        pass


def _regenerate_outcome(rep, check: bool, attributes: list[str] | None = None) -> O.Outcome:
    """The outcome of a run: failed when a recipe failed (or, with ``check``, is stale); nothing
    (exit 2, could not run) when a recipe could not finish; ok otherwise."""
    rows = [
        {
            "name": p.name,
            "files": list(p.files),
            "stale": p.stale,
            "regenerated": p.regenerated,
            "error": p.error,
            **({"unavailable": p.unavailable} if p.unavailable else {}),
        }
        for p in rep.pins
    ]
    extra = {"resolved": rep.resolved} if rep.resolved else {}
    if attributes:  # .gitattributes is tracked: say what was written, it is not a stray change
        extra["attributes"] = attributes
    if rep.failed:
        return O.failed(
            "pins", "; ".join(f"{p.name}: {p.error}" for p in rep.failed), pins=rows, **extra
        )
    if check and rep.stale:
        return O.failed(
            "pins",
            "stale pins: "
            + ", ".join(p.name for p in rep.stale)
            + ". `ddflow pins regenerate` re-records them",
            pins=rows,
        )
    if rep.unavailable:
        why = "; ".join(f"{p.name}: {p.unavailable}" for p in rep.unavailable)
        return O.nothing("pins", f"could not finish: {why}", pins=rows, **extra)
    return O.ok("pins", pins=rows, **extra)


def _lease_tick(repo: Path, agent: str):
    """(a tick renewing every live lease the caller holds, its interval) while the recipes
    run, or None: regenerating pins runs the whole snapshot suite, minutes long."""
    try:
        log, cfg, st = _load(repo, agent)
    except Exception:
        return None
    tick, every = LK.for_holder(log, cfg, st)
    return (tick, every) if tick is not None else None
