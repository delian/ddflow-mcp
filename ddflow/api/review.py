"""Cross-family review: run the reviewers configured for a gate and record the outcome.

The rule this family exists to enforce: **an unavailable reviewer is never a passed
review.** No reviewer configured, an empty diff, an endpoint that does not answer — each
records `unavailable`, which is exit 2 and explicitly NOT a pass. Collapsing any of them
into success is the vacuous-pass class at the level of a whole reviewer, and it is the one
failure that looks exactly like a clean bill of health.

`on_progress` exists because a review takes minutes. The CLI prints each line as it
happens; silence for two minutes reads as a hang, and an agent watching a hung tool kills
it. The api streams rather than the surface polling, because only the api knows when a
reviewer has actually answered.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..infra import worktree as W
from ._base import _load


def commit_diff(repo: Path, sha: str) -> tuple[str, str]:
    """(diff, how) for ONE landed commit, against its first parent.

    The review the source projects run after every commit (`roborev review <sha>`) is of
    the commit, not of a branch: by then the branch is merged and gone, and HEAD may be a
    merge whose own diff is empty. First parent, so a merge commit's review is of what
    the merge brought in. A root commit is diffed against the empty tree.
    """
    r = W.git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if not r.ok:
        return "", f"commit {sha!r} not found"
    full = r.out
    # Whether the commit HAS a parent comes from its own header, which a shallow clone
    # keeps; whether the parent is PRESENT is a separate question. Conflating them
    # reviewed a shallow-cloned commit against the empty tree -- the whole repository
    # presented as that commit's change (roborev 830).
    header = W.git(repo, "cat-file", "-p", full)
    parents = [ln.split()[1] for ln in header.out.splitlines() if ln.startswith("parent ")]
    if not parents:
        base = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # the empty tree: a root commit
    else:
        base = parents[0]
        if not W.git(repo, "cat-file", "-e", f"{base}^{{commit}}").ok:
            return "", (
                f"commit {full[:12]}'s parent {base[:12]} is not in this clone (shallow?); "
                f"its change cannot be computed here"
            )
    d = W.git(repo, "diff", "--no-color", base, full)
    return (d.out + "\n") if d.ok and d.out else "", f"commit {full[:12]} vs its first parent"


#: How many untracked files a review names before saying "... (N in all)".
SHOWN_UNTRACKED = 10


def diff_for(
    repo: Path,
    cfg,
    st,
    item: str,
    base: str = "",
    branch: str = "",
    called_from: Path | None = None,
) -> tuple[str, str]:
    """(diff, how) for an item: its worktree branch vs base, plus tracked edits.

    Untracked files in an item's tree are NOT in the diff (B2bf4d38cc1): a draft left
    untracked was reviewed as part of the change. `how` names them -- "untracked, not
    reviewed" -- so a reviewer's "no tests" is explained by a file the author has not
    committed. Commit what is to be reviewed.

    For an item, the item's work (B60de9a57ed): ``branch`` when named; else its tree;
    else its recorded branch (the tree is gone); else the branch checked out in the
    linked worktree the caller stands in, unless another open item holds that tree. An
    item CLAIMED without a worktree gets nothing more -- an empty diff, recorded
    UNAVAILABLE: the primary's working tree was the old fallback, and in a busy checkout
    it held other agents' uncommitted event logs, 43 KB of them, which the reviewer found
    nothing wrong with and which were recorded as this item's PASS. Any other uncommitted
    edit there would be credited the same way, so no path filter makes it the item's.
    Only an unclaimed item, or worktrees off, still reads the primary -- without ddflow's
    own bookkeeping.
    """
    base = base or cfg.worktree.base_ref or W.default_branch(repo)
    if not item:
        return W.capture_diff(repo), f"working tree in {repo}"
    it = st.items.get(item)
    wt_path = W.load_path(repo, it.worktree) if it and it.worktree else None
    if not branch and wt_path and wt_path.exists():
        # The branch's commits plus TRACKED edits. An untracked file is a draft nobody
        # committed (B2bf4d38cc1): it is named, not reviewed.
        diff = W.capture_diff(wt_path, base, include_untracked=False)
        how = f"{base}..HEAD + tracked working-tree changes in {wt_path}"
        if untracked := W.untracked_files(wt_path):
            shown = ", ".join(untracked[:SHOWN_UNTRACKED]) + (
                f", ... ({len(untracked)} in all)" if len(untracked) > SHOWN_UNTRACKED else ""
            )
            how += f" (untracked, not reviewed: {shown})"
        if diff.strip():
            ok, missing = W.diff_covers_everything(wt_path, diff, ignore_untracked=True)
            if not ok:
                how += f" (WARNING: {len(missing)} changed path(s) absent from the diff)"
        return diff, how
    chosen = "named with --branch" if branch else ""
    if not branch and it and it.branch:
        branch, chosen = it.branch, f"{item}'s branch (its tree is gone)"
    if not branch and it:
        from .lifecycle import callers_tree

        here, held = callers_tree(repo, cfg, st, it, called_from)
        if held:
            return "", (
                f"the worktree you are in belongs to {held}, not {item}; pass {item}'s "
                f"branch with --branch"
            )
        if here is not None and here.branch:
            branch, chosen = here.branch, f"checked out in {here.path}"
    if not branch and it and it.lease is not None and cfg.worktree.enabled:
        return "", (
            f"{item} was claimed without a worktree, and the primary's working tree is not "
            f"its: pass --branch <branch>, run review from the worktree it is worked in, or "
            f"set [worktree].enabled = false if the primary is where you work"
        )
    if not branch:
        from ..services.enforce import SELF_MANAGED

        return W.capture_diff(repo, exclude=SELF_MANAGED), (
            f"working tree in {repo}, ddflow's bookkeeping excluded -- for {item}'s work "
            f"on a branch, pass --branch <branch> or run review from its worktree"
        )
    d = W.git(repo, "diff", "--no-color", f"{base}...{branch}")
    return (d.out + "\n") if d.ok and d.out else "", f"{base}...{branch} ({chosen})"


#: How often a running review says what it is still waiting for.
PROGRESS_EVERY_S = 60


def _lease_ticker(log, cfg, it, tick_s: float) -> Callable[[], None] | None:
    """A tick renewing the caller's lease on `it` every `lease.heartbeat_s`, or None.

    A review on a reasoning model runs 20-40 minutes against a 30-minute lease, and the
    caller is blocked in it: the lease expired mid-review, the agent's next commit was
    refused, and `next` offered the item's files to another agent while its owner was
    about to merge (bugs Bc6ec4fd40d, Bf0cccb8fb1). `gate run` already renewed; this is
    the same keeper (`gates._lease_keeper`: only the HOLDER's lease, never a
    bystander's), throttled because the review ticks more often than it must renew:
    it renews at the last tick before `heartbeat_s` has passed since the lease's own
    last renewal, so two renewals are never further apart than `heartbeat_s` (critic:
    a 0.9 x heartbeat threshold let a 60 s tick land a renewal up to a tick late).
    """
    import time

    from .gates import _lease_keeper

    renew = _lease_keeper(log, cfg, it) if it else None
    if renew is None:
        return None
    due = max(0.0, max(1, cfg.lease.heartbeat_s) - tick_s)
    last = [it.lease.renewed_at or time.time()]

    def tick() -> None:
        if time.time() - last[0] >= due:
            last[0] = time.time()
            renew()

    return tick


def _say_result(say: Callable[[str], None], res) -> None:
    """One reviewer's verdict, then each finding with its detail."""
    say(
        f"  {res.reviewer} {res.label}: {len(res.findings)} finding(s), {res.coverage()}, "
        f"{res.elapsed_s:.1f}s" + (f" — {res.reason}" if res.reason else "")
    )
    for n, f in enumerate(res.findings, 1):
        say(f"\n  #{n} [{f.severity}] {f.location or f.title}")
        for line in f.detail.splitlines():
            if line.strip():
                say(f"      {line.strip()}")


def triage(
    repo: Path,
    item: str,
    *,
    gate: str = "critic",
    finding: int = 0,
    verdict: str = "",
    probe: str = "",
    agent: str = "",
) -> O.Outcome:
    """Record the author's triage of ONE finding of a gate's recorded `ddflow review`.

    ``finding`` is the number `ddflow review` printed (#N): the finding's place in the
    recorded review's per-chunk findings. ``verdict`` is ``refuted`` -- ``probe`` is the
    run that shows the finding false -- or ``confirmed`` -- ``probe`` is the fix or test
    that answers it. Appends a `review.triaged` event; the gate's outcome is untouched
    (decision D-review-triage: a review that reported findings stays `failed`, and that
    does not block completion -- the log now shows what became of each finding).
    """
    from ..services import gates as G

    log, _cfg, st = _load(repo, agent)

    def bad(why: str, **extra) -> O.Outcome:
        return O.failed("review.triage", why, id=item, gate=gate, text=why, **extra)

    it = st.items.get(item)
    if it is None:
        return bad(f"no such item {item!r}")
    if verdict not in ("refuted", "confirmed"):
        return bad("say which: --refuted (the probe shows the finding false) or --confirmed")
    if not probe.strip():
        return bad(
            "--probe is required: for --refuted the run that shows the finding false, for "
            "--confirmed the fix or test that answers it"
        )
    rec = it.gates.get(gate)
    found = (rec.evidence or {}).get("chunk_findings") if rec else None
    if not found or not all(f.get("digest") for f in found):
        return bad(
            f"{item}.{gate} has no `ddflow review` with numbered findings on record "
            f"(a hand-recorded gate, a clean review, or one from before triage)"
        )
    if not 1 <= finding <= len(found):
        return bad(
            f"no finding #{finding}: {item}.{gate} has #1..#{len(found)}", findings=len(found)
        )
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
    _log, _cfg, st = _load(repo, agent)
    counts = G.triage_counts(st.items[item], gate) or {}
    text = (
        f"{item}.{gate} finding #{finding} [{f.get('severity', '')}] {verdict}: "
        f"{G.triage_line(counts)}"
    )
    return O.ok(
        "review.triage",
        id=item,
        gate=gate,
        finding=finding,
        verdict=verdict,
        counts=counts,
        text=text,
    )


def _chunk_numbers(value) -> list[int] | str:
    """`--chunk 2 --chunk 5`, `--chunk 2,5`, or an MCP list of numbers: sorted, once each."""
    items = [value] if isinstance(value, (str, int)) else list(value or [])
    out: set[int] = set()
    for item in items:
        for part in str(item).replace(";", ",").split(","):
            if not part.strip():
                continue
            try:
                out.add(int(part))
            except ValueError:
                return f"--chunk takes chunk numbers, e.g. 5 or 2,5; got {part.strip()!r}"
    return sorted(out) or "--chunk names no chunk"


def _say_triage_scope(say: Callable[[str], None], results: list, best) -> None:
    """With several reviewers each one's findings were numbered from #1, but only the
    recorded reviewer's can be triaged: say whose (critic: another's #1 is not it)."""
    if len(results) > 1 and best.findings:
        say(f"triage addresses {best.reviewer}'s findings: #1..#{len(best.findings)}")


def _rerun_scope(it, gate: str, revs: list, diff: str, value):
    """(the reviewer to re-run, the evidence to merge into, the chunks), or why not.

    A chunk number means something only in the cut that printed it: the same diff, the
    same chunk size, the same reviewer. Checked BEFORE a reviewer is called, so a 30-
    minute request is never spent on a result that could not be merged.
    """
    from ..services import review as R

    chunks = _chunk_numbers(value)
    if isinstance(chunks, str):
        return chunks
    if it is None:
        return "--chunk re-reviews part of an ITEM's recorded review: name the item"
    rec = it.gates.get(gate)
    prior = dict(rec.evidence) if rec and rec.evidence else {}
    if not prior.get("diff_sha") or "reviewed" not in prior:
        return (
            f"no `ddflow review` of {it.id}.{gate} with per-chunk evidence is on record; "
            f"run the full review first"
        )
    if prior["diff_sha"] != R.diff_digest(diff):
        return (
            f"the diff changed since the recorded review of {it.id}.{gate}, so its chunk "
            f"numbers no longer apply: run the full review"
        )
    mine = [r for r in revs if r.name == prior.get("reviewer")]
    if not mine:
        return (
            f"reviewer {prior.get('reviewer')!r}, which recorded {it.id}.{gate}, is not "
            f"configured for {gate!r} now: run the full review"
        )
    if mine[0].max_chunk_chars != prior.get("max_chunk_chars"):
        return (
            f"[[reviewer]].max_chunk_chars is {mine[0].max_chunk_chars} now, "
            f"{prior.get('max_chunk_chars')} when {it.id}.{gate} was recorded: the chunks "
            f"differ, run the full review"
        )
    total = int(prior.get("chunks_total", 0))
    outside = [n for n in chunks if not 1 <= n <= total]
    if outside:
        return f"no chunk {outside}: the recorded review of {it.id}.{gate} had 1..{total}"
    return mine[:1], prior, chunks


def reviewers_list(repo: Path, *, agent: str = "") -> O.Outcome:
    """Every configured reviewer, and which of them cannot satisfy the family rule."""
    from ..services import review as R

    _log, _cfg, _st = _load(repo, agent)
    revs = R.load_reviewers(repo)
    rows = [
        {
            "name": r.name,
            "family": r.resolved_family(),
            "gates": list(r.gates),
            "enabled": r.enabled,
            "base_url": r.base_url,
            "model": r.model,
        }
        for r in revs
    ]
    # Not cosmetic: an unclassified reviewer cannot satisfy the different-family
    # requirement, so `complete` refuses and the reason looks like it is about the review
    # rather than about a missing `family = "..."` line.
    unclassified = [r["name"] for r in rows if not r["family"] and r["enabled"]]
    from ..views import human

    data: dict[str, Any] = {"reviewers": rows, "unclassified": unclassified}
    if not rows:
        empty = "No reviewers configured. Run `ddflow reviewers detect --write`."
        return O.nothing("reviewers.list", empty, text=empty, **data)
    out = O.ok("reviewers.list", text="", **data)
    out.data["text"] = human.render(out)
    return out


def reviewers_detect(
    repo: Path, *, write: bool = False, shared: bool = False, agent: str = ""
) -> O.Outcome:
    """Probe well-known local ports for an OpenAI-compatible endpoint.

    `write` records what answered in the git-ignored `.ddflow/local/reviewers.toml`:
    an endpoint on this machine is this machine's, and committing it hands every clone
    an address that does not exist there (bug B-reviewers-write-committed). `shared`
    commits it to `.ddflow/config.toml` instead, deliberately.
    """
    from ..services import review as R

    _log, _cfg, _st = _load(repo, agent)
    found = R.detect()
    if not found:
        none = (
            "No local OpenAI-compatible endpoint answered on any well-known port.\n"
            "Checked: " + ", ".join(u for u, _ in R.WELL_KNOWN_ENDPOINTS)
        )
        return O.nothing("reviewers.detect", none, found=[], blocks="", written="", text=none)
    rows, blocks = [], []
    for url, label, models in found:
        for m in models:
            fam = R.family_of(m)
            rows.append({"url": url, "label": label, "model": m, "family": fam})
            blocks.append(
                f'\n[[reviewer]]\nname = "{m.split("/")[-1].lower()}"\n'
                f'base_url = "{url}"\nmodel = "{m}"\nfamily = "{fam}"\n'
                f'gates = ["critic"]\n'
            )
    written = ""
    if write:
        from ..services.configwrite import append_block

        written = str(append_block(repo, "".join(blocks), shared=shared, own="reviewers.toml"))
    from ..views import human

    out = O.ok(
        "reviewers.detect",
        found=rows,
        blocks="".join(blocks),
        written=written,
        count=len(rows),
        text="",
    )
    out.data["text"] = human.render(out)
    return out


def review(  # noqa: PLR0913 -- what to diff is one of commit | branch | the item's tree, and called_from says where the caller stands
    repo: Path,
    *,
    gate: str = "critic",
    item: str = "",
    intent: str = "",
    context: str = "",
    base: str = "",
    on_progress: Callable[[str], None] | None = None,
    commit: str = "",
    branch: str = "",
    called_from: Path | None = None,
    agent: str = "",
    chunks: list[int] | list[str] | str | None = None,
) -> O.Outcome:
    """Run every reviewer configured for `gate`, and record the outcome against `item`.

    `chunks` re-reviews only those chunks (numbered as the recorded review printed
    them) and merges the result into that record -- see `_rerun_scope`.

    `commit` reviews that one landed commit instead of the item's branch -- the
    after-merge review, recorded against the item (done or not) all the same. `branch`
    reviews that branch against base, for an item claimed without a worktree (see
    `diff_for` for how the branch is otherwise found).
    """
    from ..services import gates as G
    from ..services import prompts as P
    from ..services import review as R

    # Collected AND forwarded. The CLI prints each line as it happens so a two-minute
    # review does not look like a hang; the MCP tool has no live channel, so the same
    # lines become its body — which is what its PROSE_TOOLS entry has always promised
    # ("reviewer findings, already formatted with their severities"). One transcript, two
    # deliveries, rather than a second rendering of the same run.
    transcript: list[str] = []

    def say(line: str) -> None:
        transcript.append(line)
        if on_progress:
            on_progress(line)

    log, cfg, st = _load(repo, agent)
    gates = G.load_gates(repo, cfg)

    def unavailable(reason: str, **extra) -> O.Outcome:
        """Record it, then report it. An unrecorded UNAVAILABLE is indistinguishable
        from a gate nobody ran, which is how "we reviewed it" becomes true on paper."""
        if item:
            G.record(log, cfg, item, gate, "unavailable", reason=reason, gates=gates)
        return O.nothing(
            "review",
            reason,
            id=item,
            gate=gate,
            outcome="unavailable",
            findings=[],
            text=reason,
            **extra,
        )

    revs = R.reviewers_for(R.load_reviewers(repo), gate)
    if not revs:
        return unavailable(
            f"No reviewer is configured for gate {gate!r}. "
            f"`ddflow reviewers detect --write` finds local models.\n"
            f"Recording UNAVAILABLE — which is NOT a pass.",
            how="",
        )

    diff, how = (
        commit_diff(repo, commit)
        if commit
        else diff_for(repo, cfg, st, item, base, branch=branch, called_from=called_from)
    )
    if not diff.strip():
        return unavailable(
            f"Empty diff ({how}) — nothing to review. Recording UNAVAILABLE.", how=how
        )

    it = st.items.get(item)
    intent = intent or (f"{it.title}. {it.body}".strip() if it else "")
    if not intent:
        return O.failed(
            "review",
            "--intent is required: the reviewer flags where the diff and the stated "
            "intent disagree, so without it there is nothing to disagree with.",
            id=item,
            gate=gate,
            outcome="",
            findings=[],
            how=how,
            text="",
        )

    prior: dict[str, Any] = {}
    only: list[int] | None = None
    if chunks:
        scoped = _rerun_scope(it, gate, revs, diff, chunks)
        if isinstance(scoped, str):
            return O.Outcome(
                kind="review",
                data={
                    "id": item,
                    "gate": gate,
                    "outcome": "",
                    "findings": [],
                    "how": how,
                    "text": scoped,
                },
                exit=O.REFUSED,
                reason=scoped,
            )
        revs, prior, only = scoped
        say(f"→ re-reviewing chunk(s) {only} of {item}.{gate} (recorded: {prior['coverage']})")

    overrides = P.overrides_from(cfg)
    tick_s = min(PROGRESS_EVERY_S, max(1, cfg.lease.heartbeat_s))
    keep_lease = _lease_ticker(log, cfg, it, tick_s)
    results = []
    for r in revs:
        say(f"→ {r.name} ({r.resolved_family()}) reviewing {len(diff)} chars from {how}")
        res = R.review(
            r,
            diff,
            intent,
            context=context,
            repo=repo,
            prompt_overrides=overrides,
            on_progress=say,
            on_tick=keep_lease,
            tick_s=tick_s,
            only=only,
        )
        if prior:
            # An ERROR, or a cut that does not match, is refused WITHOUT recording: the
            # record holds every other chunk's coverage, and a later --chunk needs it.
            why = (
                (res.reason or "the re-run errored")
                if res.status == R.ERROR
                else R.merge_rerun(prior, res)
            )
            if why:
                return O.Outcome(
                    kind="review", data={"id": item, "gate": gate}, exit=O.REFUSED, reason=why
                )
        results.append(res)
        _say_result(say, res)

    best = min(results, key=lambda r: r.status)
    outcome = {
        R.REVIEWED: ("failed" if best.findings else "passed"),
        R.PARTIAL: "partial",
        R.UNAVAILABLE: "unavailable",
        R.ERROR: "unavailable",
    }[best.status]
    if item:
        G.record(
            log,
            cfg,
            item,
            gate,
            outcome,
            reason=best.reason or (f"{len(best.findings)} finding(s)" if best.findings else ""),
            evidence={**best.evidence(), "diff_source": how, "diff_chars": len(diff)},
            gates=gates,
            by=best.model,
        )
        say(
            f"\nrecorded {item}.{gate} = {outcome} (reviewer {best.reviewer}, family {best.family})"
        )
        _say_triage_scope(say, results, best)

    data: dict[str, Any] = {
        "id": item,
        "gate": gate,
        "outcome": outcome,
        "how": how,
        "reviewer": best.reviewer,
        "family": best.family,
        "findings": [
            {"severity": f.severity, "title": f.title, "location": f.location, "detail": f.detail}
            for f in best.findings
        ],
        "text": "\n".join(transcript),
    }
    exit_code = {R.REVIEWED: O.OK, R.PARTIAL: O.REFUSED, R.UNAVAILABLE: O.NOTHING, R.ERROR: O.FAIL}[
        best.status
    ]
    if exit_code == O.OK:
        return O.ok("review", **data)
    return O.Outcome(
        kind="review",
        data=data,
        exit=exit_code,
        reason=best.reason or f"{len(best.findings)} finding(s)",
    )
