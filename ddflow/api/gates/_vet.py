"""Gate-record evidence: ``Evidence`` and the claims checked when an agent records.

The vetted ``--model``/``--reviewed-sha``/roborev and the failed-review override refusal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...infra import worktree as W
from ...services import gates as G
from ...services import roborev as RR
from ...services.approvals import gate_skip as GS
from ...services.approvals.consume import operator_gate
from ...services.export import refresh as RF
from ...services.gates import failed_gate as OC
from ...services.gates import failed_review as GO
from ...services.identity import agent_marker
from ..lifecycle.author import _session_model
from ._tree import _at_merged_tree


@dataclass
class Evidence:
    """What the caller claims it did, as a record rather than five loose arguments.

    These five travel together everywhere — argparse flags, MCP input properties, the
    event payload — and grouping them is also what makes the distinction below legible:
    `note` is what a person wrote, while `tree_sha` and `diff_stat` are MEASURED here and
    deliberately not settable by the caller. A gate whose magnitude the caller supplies
    is a gate whose magnitude proves nothing.
    """

    note: str = ""
    command: str = ""
    exit_code: int | None = None
    model: str = ""
    output_file: str = ""
    #: The commit a review tool was run on (`roborev review <sha>`), checked against the
    #: item's branch head (B1ed2b4fde6).
    reviewed_sha: str = ""
    #: ``model`` was given as `--reviewer-model`: the caller states it IS the reviewer's,
    #: so an author-family name on a reviewer gate is not refused (B1979dac602).
    model_is_reviewer: bool = False
    #: Which tests a `ci run` took (`ci_scope.Plan.evidence`): recorded as `ci_scope`.
    ci_scope: dict[str, Any] | None = None
    #: Seconds the run queued for the host suite lock (B1d51fb9da1): recorded as `waited_s`.
    waited_s: float | None = None
    #: Seconds the run's own command took (set by `ci run`; absent when the caller did not run
    #: a command, e.g. a hand-recorded gate): recorded as `elapsed_s` (B28537b615d).
    elapsed_s: float | None = None
    #: Set by a ddflow tool that RAN the gate's command itself (`ci run`), never by a flag or
    #: an MCP argument: it is what tells that record from an agent's typed `--command`.
    ran_by: str = ""


@dataclass
class _Vetted:
    refusal: str = ""
    note: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


def _has_research_note(st, it) -> bool:
    """Does a RESEARCH item have a recorded note (`research add --item`)? Its deliverable."""
    return it.kind == "research" and any(n.item == it.id for n in st.research.values())


def _prior_landing_here(repo: Path, it, base: str) -> bool:
    """B600455cc2a: `it.reopened_merge` is still the item's work ONLY while the item's
    declared paths are unchanged between that landing and `base` (what a merge of the empty
    branch actually lands): a revert of the landing (L then R) leaves the content gone, so
    the landing is then not what would merge. False when there is no declared scope or no
    prior landing, so the empty-branch refusal stays."""
    prior = it.reopened_merge
    if not prior or not it.globs:
        return False
    return W.git(repo, "diff", "--quiet", prior, base, "--", *it.globs).ok


def _implement_without_commits(
    repo: Path, cfg, it, gate: str, result: str, wt, has_note=False
) -> str:
    """A refusal when `implement` is passed for an item whose own worktree has no commit
    ahead of its base, else "" (a landed item is never judged: B998bcca9ce; bug B31c0109012: a commit the lease hook refused was
    silenced by the driver, and the gate passed over an empty branch). An item with no
    worktree of its own, or whose tree was removed, is not judged; a tree whose range cannot
    be measured is refused, not passed. A merged phase is exempt by the same test as any
    other kind: only where its checkout STANDS at the merged tree (the landed case), a
    moved, reverted or dirty phase checkout is judged (B430dae3a68 reviewer finding #2)."""
    if gate != "implement" or result != "passed" or not it.worktree:
        return ""
    if has_note:
        return ""  # its deliverable is the recorded note (`research add --item`), not a commit
    base = cfg.worktree.base_ref or W.default_branch(repo)
    tree = W.load_path(repo, it.worktree)
    if tree is None or _at_merged_tree(repo, cfg, it, tree):
        return ""  # landed (B998bcca9ce): the checkout is gone or STANDS at the merged tree,
        # i.e. it IS the base
    # Off the merged tree, the implement claim is about the item's branch itself: measure
    # IT (not the caller's tree, not the primary a merge left standing at the merged one).
    wt = tree
    r = W.git(wt, "rev-list", "--count", f"{base}..HEAD")
    if not r.ok:
        return (
            f"{it.id}'s branch could not be measured against {base} "
            f"({(r.err or r.out).strip() or 'git rev-list failed'}): an unmeasured range is "
            f"not a pass. Fix the base ref ([worktree].base_ref), or --force with a --reason "
            f"if the item truly changes nothing."
        )
    if r.out.strip() != "0":
        return ""
    # B600455cc2a: a branch at base with no commit ahead is still "implemented" when this item
    # is the rework of one that already merged (the merge also cleared its gates): the work is
    # its prior landing, still in the base's history. Accept it then instead of refusing an
    # empty branch that a re-claim for gates only left behind -- but only while the prior
    # landing's content is actually still at the branch head (a revert or later edit is not).
    if _prior_landing_here(wt, it, base):
        return ""
    return (
        f"{it.id}'s branch has no commit ahead of {base}: there is nothing implemented to "
        f"pass. Commit the work (a commit the lease hook refused leaves nothing behind), "
        f"or --force with a --reason if the item truly changes nothing."
    )


def _vet_claims(repo, cfg, st, log, it, gdef, gate, wt, evidence: Evidence, skip: bool) -> _Vetted:
    """Check the caller's ``--model`` and ``--reviewed-sha`` claims against the project."""
    if skip:
        return _Vetted()
    if evidence.model and not evidence.model_is_reviewer:
        if why := _author_model_on_reviewer_gate(st, cfg, log, gate, gdef, evidence.model):
            return _Vetted(refusal=why)
    if not evidence.reviewed_sha:
        return _Vetted()
    full, why, note = _reviewed_sha_check(repo, cfg, it, wt, evidence.reviewed_sha)
    return _Vetted(refusal=why, note=note, evidence={"reviewed_sha": full} if full else {})


def _author_model_on_reviewer_gate(st, cfg, log, gate: str, gdef, model: str) -> str:
    """A refusal when ``model`` is the AUTHOR's family on a reviewer gate, else "".

    `gate record --model` names the REVIEWER's model; `complete --model` the author's.
    Recording the author's own model on a reviewer gate overwrote the reviewer's, and
    `complete` then judged independence against the author's family (B1979dac602). The
    author is the model this agent declared at `session start`, or the family the
    harness configured (`[agent].family` / DDFLOW_AGENT_FAMILY), with none declared
    there is nothing to compare, and the record stands. A bare FAMILY name after
    `--model` is judged as that family (DX-author-family): an `[agent].families` value
    in place of a model id.
    """
    if not G.is_reviewer_gate(gate, gdef):
        return ""

    author = cfg.agent.family or _session_model(st, log.agent_id)
    if not author:
        return ""
    fam = _family_of_name_or_value(model, cfg)
    author_fam = _family_of_name_or_value(author, cfg)
    if not fam or fam != author_fam:
        # An unrecognised `[agent].family` resolves to "" on BOTH legs -- nothing a map
        # knows to compare -- yet the operator's own spelling still IS the author: the
        # bare strings must compare too, or a writer recording "--model myorg-x" would
        # never be told it just named its own family (roborev standards).
        if not (
            cfg.agent.family
            and not author_fam
            and (model or "").strip().lower() == author.strip().lower()
        ):
            return ""
        fam = author
    declared = (
        f"`[agent].family` / DDFLOW_AGENT_FAMILY for {log.agent_id}"
        if cfg.agent.family
        else f"the model {log.agent_id} declared at `session start`"
    )
    return (
        f"--model {model!r} is the author's own family ({fam}, {declared}), and --model on "
        f"a reviewer gate names the REVIEWER's model (the author's is `complete "
        f"--model`). Pass the model that reviewed, or --reviewer-model {model} to state "
        f"that this reviewer really is of that family (it will not count as independent)."
    )


def _family_of_name_or_value(model: str, cfg) -> str:
    """`model`'s family with a bare family name recognised as itself: the author and the
    typed reviewer model are judged by the same resolution (DX-author-family). One
    canonical form for both legs, so a configured value 'DeepSeek' and a family_for
    answer 'deepseek' are one family, however they were spelled."""
    return G.family_name_of(model, cfg.agent.families) or G.family_of(model, cfg).strip().lower()


def _prior_landing_check(repo, cfg, it, where, full: str, head: str) -> tuple[str, str]:
    """(full, note) when `full` is the item's prior landing of an empty rework branch, else ("", "").

    B600455cc2a: a re-claim of an already-merged item ran `item.reworked` and bound a fresh
    tree at base, so the branch has no commit ahead; the item's work is its PRIOR landing,
    now in the base's history. It is accepted ONLY while the branch still sits at the fork it
    recorded (`base_sha`) AND the landing's content is still what the base holds (`base`), so
    a revert of the landing, or a later commit absorbed into base, is still refused.
    """
    prior = it.reopened_merge
    fork = it.base_sha or ""
    if not (prior and fork and head == W.rev(where, fork) and W.rev(where, prior) == full):
        return "", ""
    if not _prior_landing_here(where, it, cfg.worktree.base_ref or W.default_branch(repo)):
        return "", ""
    return (
        full,
        f"NOTE: --reviewed-sha {full[:10]} is the item's prior landing (re-opened "
        f"on re-claim); the branch head ({head[:10]}) has no commit ahead of "
        f"{cfg.worktree.base_ref or W.default_branch(repo)}.",
    )


def _reviewed_sha_check(repo: Path, cfg, it, wt: Path | None, sha: str) -> tuple[str, str, str]:
    """(full sha, refusal, warning) for a ``--reviewed-sha``; the sha is "" when refused.

    `roborev review HEAD` run from an item's worktree enqueued the PRIMARY's HEAD
    (B1ed2b4fde6): the review was of main, recorded against the item. The branch head
    passes; an older commit of the item's own branch passes with a warning (it was
    reviewed, but not what will merge); anything else -- main, another branch, an
    unknown sha -- is refused.
    """

    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
        # `HEAD` resolves to a different commit in every checkout: it is the very input
        # that was wrong, so it cannot be told apart from a right one.
        return (
            "",
            (
                f"--reviewed-sha {sha!r} is not a commit sha: a symbolic ref such as HEAD "
                f"means a different commit in the primary and in a worktree. Pass the "
                f"hex sha the review ran on (`git rev-parse HEAD` in the item's worktree)."
            ),
            "",
        )
    where = wt if wt is not None and wt.exists() else repo
    full = W.rev(where, sha)
    if not full:
        return "", f"--reviewed-sha {sha!r} is not a commit in this repository.", ""
    head = W.head_sha(wt) if wt is not None and wt.exists() else ""
    if not head and it.branch:
        head = W.rev(repo, it.branch)
    if not head:
        return full, "", "NOTE: no branch head to compare --reviewed-sha with; recorded as given."
    if full == head:
        return full, "", ""
    base = cfg.worktree.base_ref or W.default_branch(repo)
    own = W.git(where, "rev-list", f"{base}..{head}")
    if own.ok and full in own.out.split():
        return (
            full,
            "",
            (
                f"NOTE: --reviewed-sha {full[:10]} is an older commit of the branch; its head "
                f"is {head[:10]}. What merges is not what was reviewed."
            ),
        )
    if own.ok and not own.out.strip():
        taken, note = _prior_landing_check(repo, cfg, it, where, full, head)
        if taken:
            return taken, "", note
    return (
        "",
        (
            f"--reviewed-sha {full[:10]} is not the item's branch head ({head[:10]}) nor a "
            f"commit of its branch: the review was of another commit (`roborev review HEAD` "
            f"from a worktree can enqueue the primary's HEAD). Re-run it with the explicit "
            f"sha: `roborev review {head[:10]}`."
        ),
        "",
    )


def _roborev_reviewer(
    repo: Path,
    wt: Path | None,
    gate: str,
    ev: dict[str, Any],
    typed: str,
    *,
    skip: bool,
    gdef=None,
) -> tuple[str, str]:
    """(the reviewer to record, a note) for a reviewer gate recorded with --reviewed-sha.

    The agent roborev ENQUEUED a job for is not always the one that ran it: when it
    fails, the daemon falls back to its backup agent -- kilo enqueued, claude-code
    reviewed (B03437a6b45). The model the caller typed was recorded either way, so a
    same-family reviewer passed as cross-family. roborev's job record names the agent
    that ran; that is recorded as the reviewer (in ``ev``, as `--reviewer-model` would:
    a same-family one is then flagged at completion, not refused here). Without roborev,
    or without a finished review of the sha, the typed model stands, with a note and
    ``evidence.roborev.verified = false``.
    """

    if skip or not G.is_reviewer_gate(gate, gdef) or not ev.get("reviewed_sha"):
        return typed, ""
    where = wt if wt is not None and wt.exists() else repo
    rv, note = RR.review_of(where, str(ev["reviewed_sha"]))
    if rv is None or not rv.reviewer:
        # Nobody vouched for the typed model: said in the record, not only on screen.
        why = (
            f"roborev job {rv.job} names no agent"
            if rv is not None
            else note or "roborev gave no answer"
        )
        ev["roborev"] = {"verified": False, "why": why}
        return typed, f"NOTE: {why}."
    ev["roborev"] = {"verified": True, "job": rv.job, "agent": rv.agent, "model": rv.model}
    ev["model"] = rv.reviewer
    if not typed or typed in (rv.agent, rv.model):
        return rv.reviewer, ""
    ran = rv.agent + (f" ({rv.model})" if rv.model else "")
    return rv.reviewer, (
        f"NOTE: roborev job {rv.job} was reviewed by {ran}, not {typed!r} (its backup "
        f"agent ran when the requested one failed?): {rv.reviewer!r} is recorded as the "
        f"reviewer."
    )


def _docs_gate_export(
    repo: Path, cfg, ev: dict[str, Any], warning: str, *, applies: bool = True
) -> str:
    """`[export].refresh = docs_gate`: the docs gate's export step. Regenerates and verifies
    the selected documents and records them with their body digests in ``ev``; returns the
    warning, extended when a document was skipped, failed or is not fresh. Never fails."""
    if not applies:
        return warning

    rr = RF.refresh_selected(repo, "docs_gate", cfg=cfg)
    if not rr.outcomes:
        return " ".join(filter(None, [warning, f"NOTE: {rr.note}" if rr.note else ""]))
    ev["export"] = rr.evidence()
    if rr.problems or any(o.verified is False for o in rr.outcomes):
        return " ".join(filter(None, [warning, f"NOTE: {rr.summary()}"]))
    return warning


def _evidence_fields(
    evidence: Evidence, item: str, gate: str, approval: dict[str, Any] | None = None
) -> tuple[dict[str, Any], O.Outcome | None]:
    """The event's evidence from what the caller supplied, or the failure (an unreadable
    ``--output-file``). ``approval`` is the stamp of an operator approval spent on this
    override (who approved, by which channel and level, which digest)."""
    ev: dict[str, Any] = {"approval": approval} if approval else {}
    if evidence.note:
        ev["note"] = evidence.note
    if evidence.command:
        ev["command"] = evidence.command
    if evidence.exit_code is not None:
        ev["exit"] = evidence.exit_code
    if evidence.model:
        ev["model"] = evidence.model
    if evidence.ci_scope:
        ev["ci_scope"] = evidence.ci_scope
    if evidence.waited_s is not None:
        ev["waited_s"] = evidence.waited_s
    if evidence.elapsed_s is not None:
        ev["elapsed_s"] = evidence.elapsed_s
    if evidence.ran_by:
        ev["ran_by"] = evidence.ran_by
    if evidence.output_file:
        try:
            txt = Path(evidence.output_file).read_text("utf-8", errors="replace")
        except OSError as exc:
            return ev, O.failed(
                "gate.record", f"--output-file unreadable: {exc}", id=item, gate=gate
            )
        ev.update(G.output_evidence(txt))
        # WHERE the digested output is, so the digest can be checked against it.
        ev["output_file"] = evidence.output_file
    return ev, None


def _failed_review_unanswered(it, gdef, gate: str, confirmed_only: bool) -> str:
    """Why ``gate``'s FAILED review outcome is still unanswered, or "" -- the empty
    string for a gate with no reviewer too, so "a failed review stands" is ONE
    expression everywhere (roborev job 3680: the refusal below and the not-gated note
    above each computed `gdef is not None and gdef.reviewer and why` by hand).
    ``confirmed_only`` mirrors `unresolved_review_failure`'s: the refusal varies it,
    the note reads what a hard skip sees (always the False flavor past its guard)."""
    if gdef is None or not gdef.reviewer:
        return ""
    return GO.unresolved_review_failure(it, gate, confirmed_only=confirmed_only)


def _not_gated_skip_note(warning, approval_stamp, it, gdef, gate, hard_skip) -> str:
    """B97106be46a: a skip must say WHICH case it is. A refused one already names the
    approval request; one recorded on the strength of a consumed approval carries its
    ask id in the evidence (`approval_stamp`). Any OTHER skip needs neither, for ANY
    caller -- the first critic round flagged the old guard (a reviewer on the gate AND
    an agent marker on the caller), which left a person's skip and a skip of a gate
    with no reviewer silent again. So the not-gated note fires on every non-stamped
    skip and names the one sub-case that differs: over review findings still
    unanswered the skip IS the operator's own override (an agent over one is refused
    before it gets here). The guard keys off `hard_skip`, the flag `record` itself
    uses to treat the call as a skip: `skip=False, outcome="skipped"` records
    `result` as 'skipped' all the same, and must say which case it is too
    (second critic round)."""
    if not hard_skip or approval_stamp:
        return warning
    failed = bool(_failed_review_unanswered(it, gdef, gate, confirmed_only=False))
    clause = (
        f"{gate} is a failed review with findings still unanswered, so this skip is "
        "the operator's own override"
        if failed
        else "a gate skip needs the operator's approval only over a FAILED review "
        f"with findings still unanswered; {gate} is not one, so this skip was "
        "recorded directly"
    )
    return " ".join(
        filter(None, [warning, f"not gated: {clause}, and no approval request was filed"])
    )


def _override_gate(log, cfg, it, gate, item, how, approval: str, reason: str):
    """The question `_agent_skip_refusal` asks the approvals engine when an agent sets aside a
    failed review: ``how`` is (skip?, the outcome word being hand-recorded)."""
    skip, outcome = how
    word = "skipped" if skip else outcome

    def ask(why: str):
        canon = GS.canon_for(it, gate, item, why, log.agent_id, word)
        return operator_gate(log, cfg, canon, approval_id=approval, reason=reason)

    return ask


def _agent_skip_refusal(
    it, gdef, gate: str, item: str, agent: str, hard_skip: bool, hand: bool, required=(), gated=None
) -> tuple[O.Outcome | None, dict]:
    """D-failed-review-gate (B85841c0846): replacing the FAILED outcome of a review gate whose
    confirmed findings were never re-reviewed -- by a skip, or a hand-recorded pass,
    unavailable or partial -- is the operator's override. An agent (`--agent`, DDFLOW_AGENT,
    a harness's shell) is refused; the person records the reason. A review that really runs
    (`ddflow review`, a triage) does not come through here.

    ``gated`` (RA-consume): a callable taking the reason the gate is unresolved and returning
    the approval decision (`approvals.consume.Decision`). An agent with a person's approval of
    exactly this override is let through and the approval's stamp is returned for the record.

    DX-skip-failed-gate: a skip of any other REQUIRED gate that is FAILED is refused for
    everyone (`skip_over_failure_refusal`); a failure is recorded as one, or as unavailable."""
    if hard_skip and not (gdef and gdef.reviewer):
        if why := OC.skip_over_failure_refusal(it, gate, item, required):
            return O.refused("gate.record", why, id=item, gate=gate, outcome=""), {}
    if not (hard_skip or hand) or gdef is None or not gdef.reviewer:
        return None, {}
    why = _failed_review_unanswered(it, gdef, gate, confirmed_only=not hard_skip)
    marker = agent_marker(agent) if why else ""
    if not marker:
        return None, {}
    route = ""
    if gated is not None:
        decision = gated(why)
        if decision.ok:
            return None, decision.stamp
        route = " " + decision.message
    return (
        O.refused(
            "gate.record",
            f"{gate} failed ({why}); setting it aside past those findings is the operator's "
            f"override, and this call is an agent's ({marker}). Fix and re-run "
            f"`ddflow review {item} --gate {gate}`, or get the operator's approval.{route}",
            id=item,
            gate=gate,
            outcome="",
        ),
        {},
    )
