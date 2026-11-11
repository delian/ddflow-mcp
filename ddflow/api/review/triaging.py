"""Findings triage and gate settling for a recorded review."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...core import blobref as B
from ...core import outcome as O
from ...infra import worktree as W
from ...services import gates as GD
from ...services import logblobs as LB
from ...services import review as RV
from ...services.completion import changed_paths
from ...services.gates import evidence as GE
from ...services.gates import reviewed_paths as RP
from ...services.gates.rounds import rounds_used as _rounds_used
from .._base import _load
from ..gates import item_tree
from ..knowledge.regression import _unresolved_tests
from ..review_finding_ref import resolve as resolve_finding
from .findings import _chunk_numbers, _finding_listing, _mirror_sibling_gates
from .records import _prior_evidence, _unrechecked_confirmed

_NODE = re.compile(r"[\w./-]+\.py::\S+")


_PY_PATH = re.compile(r"[\w./-]+\.py\b(?:::[\w:.\[\]-]+)?")


_RUN_ARG = re.compile(r"(?:-k\s+|\./|make\s+)\S+")  # an expression or script run, not a test name


_BARE_TEST = re.compile(r"\btest_\w+")


def _defined_anywhere(text: str, name: str) -> bool:
    return (
        re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b", text, re.M) is not None
    )


def probe_problem(roots: list[Path], probe: str) -> str:
    """Why a triage probe names a test that does not exist, or "" (bug Bb049f0ae06).

    A probe is free text -- the command run, the fix made -- so only what it CLAIMS to be a
    test is checked: a pytest node id (`tests/x.py::test_y`, `::Class::test_y`) must name an
    existing file defining each part, and a bare `test_name` must be defined somewhere under
    `tests/`. A node that resolves nowhere was never run, so the verdict rests on nothing."""
    nodes = [m.group(0).rstrip(".,;:)\"'") for m in _NODE.finditer(probe)]
    # The resolver `bug fixed --regression-test` uses: every worktree, bases, parametrize ids.
    missing, _unchecked = _unresolved_tests(roots[-1], ",".join(nodes)) if nodes else ([], [])
    if missing:
        return (
            f"probe names {', '.join(dict.fromkeys(missing))}, which no worktree defines "
            f"(looked in tests/ of every worktree)"
        )
    prose = _RUN_ARG.sub(" ", _PY_PATH.sub(" ", probe))  # a file's own name is not a test name
    bare = set(_BARE_TEST.findall(prose))
    if bare:
        texts = [
            f.read_text(errors="replace")
            for r in roots
            for f in sorted((r / "tests").rglob("*.py"))
        ]
        for name in sorted(bare):
            if not any(_defined_anywhere(t, name) for t in texts):
                return _bare_problem(roots, name)
    return ""


def _where(roots: list[Path]) -> str:
    return ", ".join(str(r / "tests") for r in roots)


def _bare_problem(roots: list[Path], name: str) -> str:
    """Why a bare ``name`` in a probe is not a test: the stem of a test FILE (name the file
    and a test in it), or defined nowhere under ``tests/`` (B3c2f2fea11)."""
    for r in roots:
        for f in sorted((r / "tests").rglob(f"{name}.py")):
            rel = f.relative_to(r).as_posix()
            return (
                f"probe mentions {name!r}, the stem of {rel}, which is a file and not a test: "
                f"name a test in it as {rel}::<test name>"
            )
    return (
        f"probe names test {name!r}, but no test or class of that name is defined under "
        f"tests/ (looked in {_where(roots)})"
    )


def _probe_refusal(repo: Path, it, probe: str) -> str:
    """`probe_problem` over the item's worktree and the repo, as a refusal sentence or ""."""
    roots = [r for r in (W.load_path(repo, it.worktree) if it.worktree else None, repo) if r]
    problem = probe_problem(roots, probe)
    return (
        f"{problem}: a probe must name an existing test -- a path::node id "
        f"(tests/test_x.py::test_y) or a test name defined under tests/ -- or a command that was "
        f"run (pytest -k expr, ./script)"
        if problem
        else ""
    )


def triage(
    repo: Path,
    item: str,
    *,
    gate: str = "",
    finding: int | str = 0,
    verdict: str = "",
    probe: str = "",
    agent: str = "",
) -> O.Outcome:
    """Record the author's triage of ONE finding of a gate's recorded `ddflow review`.

    ``finding`` is the number `ddflow review` printed (#N) -- the finding's place in the
    recorded review's per-chunk findings -- or a unique digest prefix (6+ hex characters). ``verdict`` is ``refuted`` -- ``probe`` is the
    run that shows the finding false -- or ``confirmed`` -- ``probe`` is the fix or test
    that answers it. Appends a `review.triaged` event; the gate's outcome is untouched
    (decision D-review-triage: a review that reported findings stays `failed`, and that
    does not block completion -- the log now shows what became of each finding) --
    except once the review budget is spent: the triage that settles the gate's LAST
    finding records it passed on refutation, flagged (`_settle_after_cap`).

    Finding numbers are per gate, so an omitted ``gate`` is never defaulted (bug
    Bca71987363): it resolves only when exactly one gate has numbered findings, else it
    is refused naming the gates that do.
    """

    log, _cfg, st = _load(repo, agent)

    def bad(why: str, **extra) -> O.Outcome:
        return O.failed("review.triage", why, id=item, gate=gate, text=why, **extra)

    it = st.items.get(item)
    if it is None:
        return bad(f"no such item {item!r}")
    if verdict not in ("refuted", "confirmed"):
        # The LISTING leads, the refusal follows: a read of the recorded round prints the
        # findings first, never hidden behind the error (bug Bdd43b55a2a).
        listing = _finding_listing(it, gate)
        remedy = "say which: --refuted (the probe shows the finding false) or --confirmed"
        return bad(f"{listing}\n{remedy}" if listing else remedy)
    if not probe.strip():
        return bad(
            "--probe is required: for --refuted the run that shows the finding false, for "
            "--confirmed the fix or test that answers it"
        )
    if problem := _probe_refusal(repo, it, probe):
        return bad(problem)
    if not gate:
        withf = sorted(g for g, r in it.gates.items() if (r.evidence or {}).get("chunk_findings"))
        if len(withf) > 1:
            return bad(
                f"{item} has findings on gates {', '.join(withf)} and finding numbers are "
                "per gate: say which with --gate"
            )
        if not withf:
            return bad(
                f"{item} has no `ddflow review` with numbered findings on any gate to triage"
            )
        gate = withf[0]
    rec = it.gates.get(gate)
    found = LB.full(rec.evidence or {}).get("chunk_findings") if rec else None
    if isinstance(found, B.Unavailable):
        return bad(
            f"the findings of {item}.{gate} are {found}: they cannot be triaged until the blob is restored"
        )
    if not GD.is_triageable(found):
        return bad(
            f"{item}.{gate} has no `ddflow review` with numbered findings on record "
            f"(a hand-recorded gate, a clean review, or one from before triage)"
        )
    finding, why = resolve_finding(finding, found, f"{item}.{gate}")
    if not finding:
        return bad(why, findings=len(found))
    f = found[finding - 1]
    log.append(
        "review.triaged",
        item,
        {
            "gate": gate,
            "finding": finding,
            "digest": f["digest"],
            "verdict": verdict,
            "probe": probe.strip(),
            "severity": f.get("severity", ""),
            "title": f.get("title", ""),
            "location": f.get("location", ""),
        },
    )
    mirrored = _mirror_sibling_gates(
        log,
        it,
        gate=gate,
        digest=f["digest"],
        verdict=verdict,
        probe=probe.strip(),
        severity=f.get("severity", ""),
        title=f.get("title", ""),
        location=f.get("location", ""),
        detail=f.get("detail", ""),
    )
    log, cfg, st = _load(repo, agent)
    counts = GD.triage_counts(st.items[item], gate) or {}
    text = (
        f"{item}.{gate} finding #{finding} [{f.get('severity', '')}] {verdict}: "
        f"{GD.triage_line(counts)}"
    )
    extra, this_refuted = _settle_chain(log, cfg, st, repo, item, gate, counts, mirrored)
    text = f"{text}\n{extra}" if extra else text
    revoke = _revoke_refutation_pass(log, cfg, st, item, gate, mirrored, verdict)
    text = f"{text}\n{revoke}" if revoke else text
    return O.ok(
        "review.triage",
        id=item,
        gate=gate,
        finding=finding,
        verdict=verdict,
        counts=counts,
        mirrored=mirrored,
        text=text,
        passed_on_refutation=this_refuted,
    )


def reviewed_other_tree(repo: Path, cfg, st, item: str, gate: str) -> str:
    """Why ``gate``'s recorded review round cannot be passed on triage, or "" (bug B835d22b138).
    A triage settles the findings of THAT round; recording the gate passed on it once the
    code has moved on would put a pass on work no reviewer saw. Fails closed: a round that
    names no tree, or a tree that cannot be read now, cannot show it reviewed what the item
    holds, and a pass must name the head it reviewed."""
    it = st.items.get(item)
    rec = it.gates.get(gate) if it else None
    if rec is None:
        return ""
    ev = rec.evidence or {}
    wt, _held = item_tree(repo, cfg, st, it, None)
    was, now = _round_and_current_tree(repo, ev, wt, it)
    redo = _redo_hint(cfg, it, item, gate)
    if not was:
        return f"the round names no tree it reviewed; {redo}"
    if not now:
        return f"the tree {item} holds now cannot be read to compare with the round's; {redo}"
    if was != now:
        moved = _filtered_moved(repo, it, wt, ev, cfg)
        if moved == []:  # only files outside what the item changed and the round saw
            return ""
        where = f" ({_name_paths(moved)})" if moved else ""
        # Bb2c62995e3: name the head the review READ (`reviewed_head`, captured before the
        # diff was taken), not `tree_sha`, which a `record_measured` run stores from the tree
        # as it was when the run ENDED -- a commit landed while the review ran that no
        # reviewer saw. With no reviewed head (an older round), the tree it measured stands.
        reviewed = (ev.get("reviewed_head") or ev.get("tree_sha") or "")[:19]
        return f"the round reviewed {reviewed}, and {item} holds other content now{where}; {redo}"
    return ""


def _round_and_current_tree(repo: Path, ev: dict[str, Any], wt, it) -> tuple[str, str]:
    """(the content id the round reviewed, the one the item holds now); "" for one not known."""
    readable = wt is not None and wt.exists()
    was = GE.recorded_content(ev.get("tree_sha", "") or "", ev.get("source_tree", "") or "")
    base, _sep, dirt = GE.split_identity(GE.normal_fingerprint(ev.get("tree_sha", "") or ""))
    if not was and dirt == "clean" and base:
        # `<head>+clean` names the commit's own tree: read that content id from git wherever
        # the commit is reachable -- the worktree, or the primary once a merge removed it.
        at = wt if readable else repo
        was = GE.commit_source_tree(at, base)
    if not was and ev.get("reviewed_head") and not readable and not ev.get("tree_sha", ""):
        # An after-merge `--commit` review has no worktree to measure, so its round records
        # only the commit it covered (`reviewed_head`) and no `tree_sha`: that commit's own
        # tree is what the round reviewed (bug B-critic-refutation-pass-needs-tree-after-merge).
        # Never read it where a worktree stands (`reviewed_head` there is HEAD and the
        # reviewer saw the dirty working tree over it), and never for a round that DID name a
        # tree: a dirty `tree_sha` whose content the gone worktree cannot vouch for is left
        # unresolved, not read as `reviewed_head`'s clean commit.
        was = GE.commit_source_tree(repo, ev["reviewed_head"])
    now = GE.source_tree(wt) if readable else _merged_content(repo, it)
    return was, now


def _merged_content(repo: Path, it) -> str:
    """`source_tree` of a merged item's landed commit, "" when it names none."""
    if not (getattr(it, "merged_sha", "") or getattr(it, "landed_after", "")):
        return ""
    _where, landed = GE.tree_being_completed(repo, it)
    return GE.commit_source_tree(repo, landed) if landed else ""


def _redo_hint(cfg, it, item: str, gate: str) -> str:
    """What to do about a round the item's tree has left behind."""
    spent = 0 < cfg.review.max_rounds <= _rounds_used(it, gate) if cfg is not None else False
    if spent:
        return (
            f"the review budget is spent: ask the operator for one more round on the current tree "
            f'(`ddflow review {item} --gate {gate} --force --reason "..."`)'
        )
    return (
        f"re-run `ddflow review {item} --gate {gate}`: a pass needs a round that saw the "
        "current tree"
    )


def _reviewed_snapshot(repo: Path, cfg, st, it, called_from) -> dict[str, Any]:
    """The `reviewed_paths` evidence of a round: the content of the item's changed set, so a
    later triage compares those files and not the whole tree. {} when it cannot be read, or
    for a review of no item."""
    if it is None:
        return {}
    wt, _held = item_tree(repo, cfg, st, it, called_from)
    paths = changed_paths(repo, it) if wt is not None and wt.exists() else None
    snap = RP.path_snapshot(wt, paths) if paths is not None and wt is not None else None
    return {} if snap is None else {RP.REVIEWED_PATHS_FIELD: snap}


def _moved_paths(repo: Path, it, wt, recorded: dict) -> list[str] | None:
    """The changed-set paths whose content differs from the round's `reviewed_paths`; None
    when the round has none (an older round: the whole tree stands) or they cannot be read."""
    if not isinstance(recorded, dict) or wt is None or not wt.exists():
        return None
    now_changed = changed_paths(repo, it)
    return None if now_changed is None else RP.snapshot_differing(recorded, wt, now_changed)


def _filtered_moved(repo: Path, it, wt, ev: dict[str, Any], cfg) -> list[str] | None:
    """The paths that differ from what the round saw, minus ones `reviewed_other_tree`
    ignores (triage_ignore_globs); [] when none differ, None when they cannot be read (an
    older round without `reviewed_paths`, or a tree gone): the caller reads None as
    "cannot compare" and [] as "compare, none relevant differ"."""
    moved = _moved_paths(repo, it, wt, ev.get(RP.REVIEWED_PATHS_FIELD))
    if not moved:  # a test the round did not review is not new work (triage_ignore_globs)
        return moved
    seen, ignore = ev.get(RP.REVIEWED_PATHS_FIELD), cfg.review.triage_ignore_globs or []
    gone = {p for p in moved if not (wt / p).exists()} if wt is not None else set()
    return [p for p in moved if not RP.ignorable(p, seen, ignore, cfg.review.doc_globs or [], gone)]


def _confirmed_files(it, gate: str) -> set[str]:
    """The files a gate's CONFIRMED findings cite (their fix locations): a confirmed finding
    was fixed, so a tree move confined to these files is the fix (B-review-round-budget-
    no-fix-path), whereas a move outside them is unreviewed content (B835d22b138)."""
    files: set[str] = set()
    for verdict in (it.triage.get(gate, {}) if it is not None else {}).values():
        if verdict.get("verdict") != "confirmed":
            continue
        loc = (verdict.get("location") or "").split(":", 1)[0].strip()
        if loc:
            files.add(str(loc))
    return files


def _movement_is_confirmed_fix(
    counts: dict[str, int], moved: list[str] | None, it, gate: str
) -> bool:
    """Whether a stale tree is explained by the confirmed findings' fixes: at least one
    confirmed finding, and every moved file is cited by a confirmed finding. A refuted
    finding's file that also moved is NOT a confirmed fix's, so it keeps the stale block
    (fail closed); an all-refuted round has no confirmed files and never counts. A `moved`
    of None (no `reviewed_paths` on the round, so the moved set cannot be compared) is not
    a verified fix either: cannot compare, so the stale block stays (fail closed)."""
    if not counts.get("confirmed") or not moved:
        return False
    confirmed = _confirmed_files(it, gate)
    return bool(confirmed) and not (set(moved) - confirmed)


def _moved_for(repo: Path, cfg, st, item: str, gate: str) -> list[str] | None:
    """The files the gate's recorded round saw that moved since (`_filtered_moved`), for
    checking whether a stale tree is nothing but the confirmed findings' fixes."""
    it = st.items.get(item)
    rec = it.gates.get(gate) if it else None
    if rec is None:
        return []
    wt, _held = item_tree(repo, cfg, st, it, None)
    return _filtered_moved(repo, it, wt, rec.evidence or {}, cfg)


def _name_paths(paths: list[str], cap: int = 5) -> str:
    return ", ".join(paths[:cap]) + (f" and {len(paths) - cap} more" if len(paths) > cap else "")


def _settle_mark(counts: dict[str, int], used: int, cap: int) -> dict[str, Any]:
    """The evidence of a pass recorded from settled triage (D-unify 5): passed ON
    REFUTATION, flagged, when a finding was refuted; `findings_fixed` when every one was
    confirmed -- never in `gate list --refuted` (B1396d7bd55). `rounds.settled_pass` keeps
    either off the round count."""
    rounds = {"rounds": used, "max_rounds": cap}
    refuted = counts.get("refuted", 0)
    return (
        {
            "passed_on_refutation": {
                "refuted": refuted,
                "confirmed": counts.get("confirmed", 0),
                **rounds,
            }
        }
        if refuted
        else {"findings_fixed": {"confirmed": counts.get("confirmed", 0), **rounds}}
    )


def _settle_chain(log, cfg, st, repo, item, gate, counts, mirrored) -> tuple[str, bool]:
    """The lines to append to a triage's text, and whether THIS gate settled ON REFUTATION
    (scoped to ``gate`` alone, whatever a sibling's mirrored settle says): a note naming the
    sibling gate(s) the mirror answered, this gate's settle-after-cap, then each mirrored
    sibling gate's own settle (bug B-combined-review-findings-triaged-twice)."""
    parts: list[str] = []
    if mirrored:
        parts.append(f"(also recorded for {', '.join(mirrored)}: the same finding)")
    it = st.items[item]
    moved = _moved_for(repo, cfg, st, item, gate)
    settled = _settle_after_cap(
        log,
        cfg,
        it,
        gate,
        counts,
        stale=reviewed_other_tree(repo, cfg, st, item, gate),
        moved=moved,
    )
    if settled:
        parts.append(settled)
    for g2 in mirrored:
        settled2 = _settle_after_cap(
            log,
            cfg,
            it,
            g2,
            GD.triage_counts(it, g2) or {},
            stale=reviewed_other_tree(repo, cfg, st, item, g2),
            moved=_moved_for(repo, cfg, st, item, g2),
        )
        if settled2:
            parts.append(settled2)
    return "\n".join(parts), "ON REFUTATION" in settled


def _settle_after_cap(
    log,
    cfg,
    it,
    gate: str,
    counts: dict[str, int],
    *,
    stale: str = "",
    moved: list[str] | None = None,
) -> str:
    """What the triage means for a gate whose review budget is spent (decision
    D-gate-economy 2, D-unify 5): "" while rounds remain (a re-review settles it).

    Once `[review].max_rounds` rounds are used and EVERY finding on the gate's record
    has a verdict with its probe, the gate is recorded passed ON REFUTATION: the
    review's own evidence and reviewer, plus `passed_on_refutation` (the refuted and
    confirmed counts and the rounds), which gate status shows so the operator can
    spot-check it. A finding still without a verdict holds the gate where it is, and the
    answer says how to finish: settle it, or ask the operator for one more round."""
    cap = cfg.review.max_rounds
    used = _rounds_used(it, gate)
    rec = it.gates.get(gate)
    if cap <= 0 or used < cap or rec is None or rec.outcome == "passed":
        return ""
    if stale and not _movement_is_confirmed_fix(counts, moved, it, gate):
        # A stale round blocks a pass unless the moved tree is exactly the confirmed
        # findings' fixes (B-review-round-budget-no-fix-path): a pure refutation says the
        # reviewed tree is fine as it is, so it must be the tree the item holds now
        # (B835d22b138), and a refuted finding's file that moved for its own reason is
        # unreviewed content even with a sibling confirmed fix. When the relax applies, the
        # settle below records it (`findings_fixed`, or `passed_on_refutation` for a mixed
        # set) instead of leaving a gate with no path once its budget is spent. Confirmed
        # findings still hold the gate until every finding has a verdict (the untriaged
        # check just below).
        return f"{gate} stays {rec.outcome}: {stale}"
    if counts.get("untriaged", 1):
        return (
            f"the review budget is spent ({used} of {cap} rounds) and "
            f"{counts.get('untriaged')} finding(s) have no verdict: settle each with `ddflow "
            f"review triage`, and the gate is recorded passed on refutation; one you cannot "
            f'settle goes to the operator (`ddflow review {it.id} --gate {gate} --force --reason "..."` '
            "is theirs to grant)"
        )
    # D-unify 5 flags a pass on REFUTATION. A gate whose every finding was confirmed (and
    # fixed) is a fix: passed, shown as fixed, never in `gate list --refuted` (B1396d7bd55).
    refuted = counts["refuted"]
    mark = _settle_mark(counts, used, cap)
    GD.record(
        log,
        cfg,
        it.id,
        gate,
        "passed",
        reason="",
        evidence={**(rec.evidence or {}), **mark},
        by=rec.by,
    )
    verdicts = (
        f"every one of the {counts['findings']} finding(s) has a verdict with its probe "
        f"({refuted} refuted, {counts['confirmed']} confirmed) after {used} of {cap} review rounds"
    )
    if not refuted:
        return f"recorded {it.id}.{gate} = passed (findings fixed): {verdicts}"
    return f"recorded {it.id}.{gate} = passed ON REFUTATION (flagged): {verdicts}"


def _revoke_refutation_pass(log, cfg, st, item: str, gate: str, mirrored, verdict: str) -> str:
    """Re-triaging a finding to CONFIRMED after ``gate`` passed on refutation revives a
    confirmed, unfixed defect the pass no longer covers, so the pass is revoked: the gate is
    re-recorded failed with its `passed_on_refutation` flag dropped (bug B31e91454f8). One
    line per revoked gate, or "".

    ``st`` is the state as folded in `triage` BEFORE `_settle_chain` runs: it excludes any
    pass that settle may record in this same call. That is the desired read — a pass recorded
    by this very call already accounts for the just-confirmed finding — so do not re-load the
    state after settling here."""
    if verdict != "confirmed":
        return ""
    parts: list[str] = []
    for g in (gate, *(mirrored or [])):
        it = st.items.get(item)
        rec = it.gates.get(g) if it is not None else None
        if rec is None or rec.outcome != "passed":
            continue
        flag = (rec.evidence or {}).get("passed_on_refutation")
        if not isinstance(flag, dict):
            continue
        live = GD.triage_counts(it, g) or {}
        # Skip a gate whose confirmed count did NOT grow: the pass already accounted for
        # it. triage_counts is per gate, and a mirrored finding counted only for the
        # primary gate leaves the mirror's count below the snapshot, so the mirror keeps
        # its pass while the primary (whose count grew) revokes below.
        if live.get("confirmed", 0) <= int(flag.get("confirmed", 0)):
            continue
        ev = {k: v for k, v in (rec.evidence or {}).items() if k != "passed_on_refutation"}
        GD.record(
            log,
            cfg,
            item,
            g,
            "failed",
            reason="a finding was re-triaged confirmed over the refutation pass",
            evidence=ev,
            by=rec.by,
        )
        parts.append(f"{item}.{g} reverted to failed: {live['confirmed']} confirmed finding(s)")
    return "\n".join(parts)


def _hold_shape(ev: dict[str, Any]) -> bool:
    """Is ``ev`` the record of a clean delta an untriaged earlier finding held (``_hold``)?
    New records carry the `held_pass` marker; one written before it existed has the same
    shape without it: a delta REVIEWED with no finding of its own can end `failed` only
    through `_hold`, so `_held_and_settled` releases both (flag first, since the marker
    says the review was clean; with neither, the shape is false)."""
    if ev.get("held_pass") is True:
        return True
    return (
        ev.get("review_kind") == "delta"
        and ev.get("status") == "REVIEWED"
        and not ev.get("delta_findings")
    )


def _held_and_settled(it, gate: str) -> dict[str, int] | None:
    """The gate's triage counts when its `failed` is ONLY the hold of a clean delta over
    untriaged earlier findings (`_hold`), with every finding settled since; None otherwise.
    The hold's promise -- "the next clean delta then passes" -- is kept without new commits
    only for that shape: nothing changed since the reviewed head means the reviewer already
    saw everything a pass would ride on (B10ad449deb)."""
    if it is None:
        return None
    rec = it.gates.get(gate)
    if rec is None or rec.outcome != "failed" or not _hold_shape(dict(rec.evidence or {})):
        return None
    counts = GD.triage_counts(it, gate) or {}
    if not counts.get("findings") or counts.get("untriaged"):
        return None
    return counts


def _why_outcome(log, cfg, it, gate: str, how: str, why: str, sargs: dict[str, Any]) -> O.Outcome:
    """The refusal `why` of a review that will not run, as its outcome: an unchanged delta
    re-evaluates the gate (`_unchanged_delta`); every other why is refused, which is exit 3
    unless `_scope` named it exit 2 (nothing to review)."""
    if sargs.get("unchanged_delta"):
        return _unchanged_delta(log, cfg, it, gate, how, why)
    return O.Outcome(
        kind="review",
        data={
            "id": sargs.get("item", ""),
            "gate": gate,
            "outcome": "",
            "findings": [],
            "how": how,
            "text": why,
        },
        exit=sargs.get("refusal_exit", O.REFUSED),
        reason=why,
    )


def _release_settled(
    log, cfg, it, gate: str, how: str, why: str, counts: dict[str, int]
) -> O.Outcome:
    """Record ``gate`` passed from settled triage (the ''held pass'' or ''confirmed-finding'' shape
    `_unchanged_delta` re-evaluates): no round was counted for the unchanged delta, and every
    recorded finding has a verdict with its probe."""
    rec = it.gates.get(gate) if it is not None else None
    mark = _settle_mark(counts, _rounds_used(it, gate), cfg.review.max_rounds)
    GD.record(
        log,
        cfg,
        it.id,
        gate,
        "passed",
        reason="",
        evidence={**((rec.evidence or {}) if rec is not None else {}), **mark},
        by=rec.by if rec is not None else "",
    )
    flag = "ON REFUTATION (flagged)" if counts.get("refuted") else "(findings fixed)"
    tag = f"{it.id}.{gate}" if it is not None else gate
    text = (
        f"{why} No round was counted: recorded {tag} = passed {flag}: every one of the "
        f"{counts.get('findings', 0)} finding(s) has a verdict with its probe "
        f"({counts.get('refuted', 0)} refuted, {counts.get('confirmed', 0)} confirmed)"
    )
    return O.ok(
        "review",
        id=getattr(it, "id", ""),
        gate=gate,
        outcome="passed",
        findings=[],
        how=how,
        text=text,
    )


def _dropped_unrechecked(it, gate: str) -> list[tuple[str, str]]:
    """Unchecked confirmed findings (`_unrechecked_confirmed`) the gate's LATEST record no
    longer carries -- a later round reviewed the tree and did not raise them again. Only a
    delta asked for over a tree unchanged since the reviewed head gets here (their fix IS that
    head). A finding the latest record still carries raises no fix at all, so it is left out:
    releasing it would merge an unfixed defect. Returns the list only when EVERY unchecked
    confirmed finding was dropped; a mix (one dropped, another still carried) releases nothing."""
    unrechecked, unreadable = _unrechecked_confirmed(it, gate)
    if unreadable or not unrechecked:
        return []
    rec = it.gates.get(gate)
    carried: set[str] = set()
    found = LB.full((rec.evidence or {}) if rec is not None else {}).get("chunk_findings")
    if GD.is_triageable(found):
        carried = {f["digest"] for f in found}
    dropped = [(d, fam) for d, fam in unrechecked if d not in carried]
    return dropped if len(dropped) == len(unrechecked) else []


def _unchanged_delta(log, cfg, it, gate: str, how: str, why: str) -> O.Outcome:
    """A delta over a tree unchanged since the reviewed head (B10ad449deb): no reviewer
    has anything new to see, so no round runs and none counts. The gate re-evaluates
    instead: a held pass over now-settled findings is released as passed; a failed gate
    over a CONFIRMED finding whose fix is at that reviewed head is released too, because
    the delta that would re-check the fix is empty (B-tk-delta-round-empty); anything else
    stays as recorded, and the answer says which."""
    counts = _held_and_settled(it, gate)
    if counts is not None:
        return _release_settled(log, cfg, it, gate, how, why, counts)
    rec = it.gates.get(gate) if it is not None else None
    stays = rec.outcome if rec is not None else "unreviewed"
    counts = GD.triage_counts(it, gate) or {} if it is not None else {}
    dropped = _dropped_unrechecked(it, gate) if it is not None else []
    # B-tk-delta-round-empty: a FAILED gate over a CONFIRMED finding whose fix is now AT the
    # reviewed head. `_delta_scope` already said nothing changed since the reviewed head, so
    # the reviewed head answers it; a delta round that re-checks the fix is impossible because
    # the fix IS that head (a `--delta` re-checks only what changed since it). Untriaged
    # findings still need `ddflow review triage`, not a round this path can settle.
    if rec is not None and rec.outcome == "failed" and dropped and not counts.get("untriaged"):
        return _release_settled(log, cfg, it, gate, how, why, counts)
    tag = f"{it.id}.{gate}" if it is not None else gate
    reason = f"{why} No round was counted; {tag} stays {stays}."
    untriaged = counts.get("untriaged", 0)
    if untriaged:
        reason += (
            f" {untriaged} earlier finding(s) still have no triage verdict: `ddflow review "
            "triage` settles each."
        )
    return O.Outcome(
        kind="review",
        data={
            "id": getattr(it, "id", ""),
            "gate": gate,
            "outcome": "",
            "findings": [],
            "how": how,
            "text": reason,
        },
        exit=O.REFUSED,
        reason=reason,
    )


def _rerun_scope(it, gate: str, revs: list, diff: str, value, chunk_max_tokens: int = 0):
    """(the reviewer to re-run, the evidence to merge into, the chunks), or why not.

    A chunk number means something only in the cut that printed it: the same diff, the
    same chunk size, the same reviewer. Checked BEFORE a reviewer is called, so a 30-
    minute request is never spent on a result that could not be merged.
    """

    chunks = _chunk_numbers(value)
    if isinstance(chunks, str):
        return chunks
    if it is None:
        return "--chunk re-reviews part of an ITEM's recorded review: name the item"
    rec = it.gates.get(gate)
    prior = _prior_evidence(rec)
    if isinstance(prior.get("chunk_findings"), B.Unavailable):
        return f"the recorded review's findings are {prior['chunk_findings']}: run the full review"
    if not prior.get("diff_sha") or "reviewed" not in prior:
        return (
            f"no `ddflow review` of {it.id}.{gate} with per-chunk evidence is on record; "
            f"run the full review first"
        )
    if prior["diff_sha"] != RV.diff_digest(diff):
        return (
            f"the diff changed since the recorded review of {it.id}.{gate}, so its chunk "
            f"numbers no longer apply: run the full review (the retry of a partial round "
            f"is not counted)"
        )
    mine = [r for r in revs if r.name == prior.get("reviewer")]
    if not mine:
        return (
            f"reviewer {prior.get('reviewer')!r}, which recorded {it.id}.{gate}, is not "
            f"configured for {gate!r} now: run the full review"
        )
    if RV.effective_chunk_budget(mine[0], chunk_max_tokens) != prior.get("max_chunk_chars"):
        return (
            f"max_chunk_chars is {RV.effective_chunk_budget(mine[0], chunk_max_tokens)} now, "
            f"{prior.get('max_chunk_chars')} when {it.id}.{gate} was recorded: the chunks "
            f"differ, run the full review"
        )
    total = int(prior.get("chunks_total", 0))
    outside = [n for n in chunks if not 1 <= n <= total]
    if outside:
        return f"no chunk {outside}: the recorded review of {it.id}.{gate} had 1..{total}"
    return mine[:1], prior, chunks
