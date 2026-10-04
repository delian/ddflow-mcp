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
    reviewed" -- in the output and the recorded `diff_source`, for whoever triages a
    "no tests" finding; the reviewer itself does not see them. Commit what is to be
    reviewed.

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
    gate: str = "",
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

    Finding numbers are per gate, so an omitted ``gate`` is never defaulted (bug
    Bca71987363): it resolves only when exactly one gate has numbered findings, else it
    is refused naming the gates that do.
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


def _announce_rerun(rerun, revs, item: str, gate: str, say):
    """(reviewers, the record to merge into, the chunks) -- `--chunk`'s plan, said aloud;
    for any other review, (all of them, nothing, every chunk)."""
    if not rerun:
        return revs, {}, None
    revs, prior, only = rerun
    say(f"→ re-reviewing chunk(s) {only} of {item}.{gate} (recorded: {prior['coverage']})")
    return revs, prior, only


def _merge_delta(log, it, gate: str, kind, status, ev: dict[str, Any], say) -> int:
    """Fold a delta's findings and coverage into the evidence of the review it follows.

    The record keeps the earlier findings in place (so their numbers, and the triage
    keyed by the digest of their exact text, stay as they were) and appends the delta's
    new ones; a finding byte-identical to an earlier one is that finding, not a second
    copy. `coverage` stays the delta's (a partial one must not read as a full pass); the
    last full round's is kept as `full_coverage`. A no-op for anything but a delta that
    reached a reviewer.

    Returns how many findings of the merged record have no triage verdict yet: a clean
    delta does not clear a gate whose earlier findings nobody has settled (`_hold`).
    """
    from ..services import review as R

    if kind != "delta" or status not in (R.REVIEWED, R.PARTIAL):
        return 0
    ev["delta_from"] = _last_head(log, it.id, gate)
    rec = it.gates.get(gate) if it else None
    prior = dict(rec.evidence) if rec and rec.evidence else {}
    kept = list(prior.get("chunk_findings") or [])
    mine = list(ev.get("chunk_findings") or [])
    ev["delta_findings"] = len(mine)
    if prior.get("full_coverage") or prior.get("review_kind") in ("full", "full_unavailable"):
        ev["full_coverage"] = prior.get("full_coverage") or prior.get("coverage", "")
    if not kept:
        return 0
    have = {f.get("digest"): n for n, f in enumerate(kept, 1)}
    for f in mine:
        if f.get("digest") not in have:
            kept.append(f)
            have[f.get("digest")] = len(kept)
    ev["chunk_findings"] = kept
    ev["findings"] = len(kept)
    ev["titles"] = [f.get("title", "") for f in kept[:10]]
    if mine:
        where = ", ".join(f"#{n} -> #{have[f.get('digest')]}" for n, f in enumerate(mine, 1))
        say(
            f"merged into the record of {it.id}.{gate} ({len(kept)} finding(s) in all): this "
            f"delta's numbers above are its own; to triage use the record's -- {where}."
        )
    verdicts = it.triage.get(gate, {})
    return sum(1 for f in kept if verdicts.get(f.get("digest"), {}).get("verdict") not in _SETTLED)


_SETTLED = ("refuted", "confirmed")


def _hold(outcome: str, reason: str, untriaged: int, say) -> tuple[str, str]:
    """A clean delta passes only when every earlier finding on the record has a verdict.

    Before deltas were the default a re-review read the whole diff and reported an
    unfixed finding again, so the gate stayed `failed` until it was dealt with; a delta
    does not see it, so the record's own findings are what keeps the gate honest.
    `review triage` (refuted, or confirmed with the fix as the probe) releases it.
    """
    if outcome != "passed" or not untriaged:
        return outcome, reason
    why = (
        f"the delta is clean, but {untriaged} earlier finding(s) on the record have no "
        "triage verdict: `ddflow review triage` settles each (the next clean delta then passes)"
    )
    say(f"NOTE: {why}.")
    return "failed", why


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

        written = str(
            append_block(repo, "".join(blocks), shared=shared, own="reviewers.toml", agent=agent)
        )
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


class _ReplyFile:
    """Every chunk's whole reply, appended to ``.ddflow/local/reviews/<item>.<gate>.jsonl``
    as it ARRIVES (bug B206): a review can outlive its caller (the MCP client gave up at
    1800 s on a 2055 s critic), and a finding body that lives only in the tool response
    is then lost. The gate evidence names the file and its digest. The name is scoped to
    THIS run and reviewer (time and a random token): a re-review, or a retry of an aborted call running at the
    same time, must not truncate the file an earlier record's digest refers to."""

    def __init__(self, repo: Path, item: str, gate: str, reviewer: str) -> None:
        import re
        import threading
        import time
        import uuid

        run = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        who = re.sub(r"[^A-Za-z0-9._-]", "_", reviewer)
        self.repo = repo
        self.path = repo / ".ddflow" / "local" / "reviews" / f"{item}.{gate}.{who}.{run}.jsonl"
        self._lock = threading.Lock()
        self._started = False

    def add(self, chunk: int, reply: str) -> None:
        import json

        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"chunk": chunk, "reply": reply}) + "\n")
                self._started = True
            except OSError:
                pass  # the evidence still carries the bodies; this is the belt

    def evidence(self) -> dict[str, Any]:
        import hashlib

        if not self._started:
            return {}
        try:
            digest = hashlib.sha256(self.path.read_bytes()).hexdigest()[:16]
        except OSError:
            return {}
        # Repo-relative: the event log is committed, an absolute path is one machine's.
        return {"output_file": str(self.path.relative_to(self.repo)), "output_digest": digest}


def _round_evidence(
    repo, it, branch, commit, kind, done, status, forced, deltas=0
) -> dict[str, Any]:
    """The round bookkeeping a recorded review carries: kind, the running count of full
    rounds and of delta rounds (kept apart: `gate status` shows both), the head it
    covered (what a delta diffs from), and a forced round's reason. A round counts only
    when a reviewer actually reviewed something."""
    from ..services import review as R

    reviewed = status in (R.REVIEWED, R.PARTIAL)
    counted = kind == "full" and reviewed
    ev: dict[str, Any] = {
        "review_kind": kind if counted or kind != "full" else "full_unavailable",
        "rounds": done + (1 if counted else 0),
        "delta_rounds": deltas + (1 if kind == "delta" and reviewed else 0),
    }
    if reviewed:
        # Only a review that reached a reviewer covered this head: recording it for one
        # that did not would let the next delta start past commits nobody reviewed.
        ev["reviewed_head"] = _head_of(repo, it, branch, commit)
    if counted:
        ev["round"] = done + 1
    if forced:
        ev["budget_forced"] = forced
    return ev


def _full_rounds(log, item: str, gate: str) -> int:
    """Full review rounds already recorded for `item`'s `gate`, counted from the log.

    Counted from the EVENTS, not the gate's current record: a later delta, a manual
    `gate skip`/`record` or a re-claim replaces that record's evidence, and the budget
    must not reset with it. Only rounds `ddflow review` tagged `review_kind = "full"`
    count: a refused round, an errored one, a delta and a `--chunk` re-run are not.
    """
    return sum(
        1
        for e in log.read_all()
        if e.subject == item
        and e.kind.startswith("gate.")
        and e.data.get("gate") == gate
        and (e.data.get("evidence") or {}).get("review_kind") == "full"
    )


def _delta_rounds(log, item: str, gate: str) -> int:
    """Delta rechecks already recorded for `item`'s `gate` (counted from the log, like
    `_full_rounds`): only ones that reached a reviewer."""
    return sum(
        1
        for e in log.read_all()
        if e.subject == item
        and e.kind.startswith("gate.")
        and e.data.get("gate") == gate
        and (e.data.get("evidence") or {}).get("review_kind") == "delta"
        and (e.data.get("evidence") or {}).get("status") in ("REVIEWED", "PARTIAL")
    )


def _budget_refusal(item: str, gate: str, done: int, cap: int) -> str:
    return (
        f"{item}.{gate} has had {done} full review rounds ([review].max_rounds = {cap}). "
        "Later rounds find fewer defects than earlier ones, so instead:\n"
        f"  ddflow review {item} --gate {gate} --delta    recheck ONLY what changed since the "
        "reviewed head (always allowed)\n"
        f"  ddflow review triage {item} --gate {gate} --finding N --refuted|--confirmed "
        '--probe "..."    settle each remaining finding (always allowed)\n'
        f'  ddflow review {item} --gate {gate} --force --reason "..."    one more full round, '
        "recorded\n"
        "To change the budget: ddflow config --set review.max_rounds N [--local] "
        '(0 = unlimited), or [review].on_exceed = "warn".'
    )


def _reason_of(best) -> str:
    return best.reason or (f"{len(best.findings)} finding(s)" if best.findings else "")


def _finding_rows(best) -> list[dict[str, str]]:
    return [
        {"severity": f.severity, "title": f.title, "location": f.location, "detail": f.detail}
        for f in best.findings
    ]


def _item_intent(it) -> str:
    return f"{it.title}. {it.body}".strip() if it else ""


def _scope(repo, cfg, st, log, it, say, revs, *, locals_: dict[str, Any]):
    """(kind, rounds done, forced reason, diff, how, why refused): what this call reviews
    and whether the round budget lets it. `locals_` is review()'s own arguments."""
    a = locals_
    item, gate = a["item"], a["gate"]
    delta = _wants_delta(repo, cfg, log, it, a, say)
    kind = _kind(repo, cfg, log, item, gate, a["chunks"], delta, a["commit"], a["base"])
    done = _full_rounds(log, item, gate) if item else 0
    forced, diff, how, why = "", "", "", ""
    if kind == "full" and item:
        why, forced = _budget(cfg, item, gate, done, a["force"], a["reason"], say)
    if why:
        return kind, done, forced, diff, how, why, None
    if delta:
        diff, how, why = _delta_scope(repo, it, log, gate, a["branch"])
    elif a["commit"]:
        diff, how = commit_diff(repo, a["commit"])
    else:
        diff, how = diff_for(
            repo, cfg, st, item, a["base"], branch=a["branch"], called_from=a["called_from"]
        )
    rerun = None
    if a["chunks"] and diff.strip():
        rerun = _rerun_scope(it, gate, revs, diff, a["chunks"])
        why, rerun = (rerun, None) if isinstance(rerun, str) else ("", rerun)
    return kind, done, forced, diff, how, why, rerun


def _wants_delta(repo, cfg, log, it, a, say) -> bool:
    """Is this call a delta recheck? `--delta` says so; with `[review].delta_default` (on
    in every project) so does a plain review of a gate that already has a recorded one.

    `--full`, `--force` (a forced FULL round), `--chunk`, `--commit` and `--base` are left
    as they are asked for. A delta is only sound from a head the branch still contains: one
    that is no ancestor of the tip (rebased, amended) would diff the rebase's own changes,
    so it falls back to a full round and says why -- as does, for the automatic delta only,
    a prior review that did not cover the whole diff (an explicit `--delta` is the author's
    own choice; the recorded `coverage` is kept beside the delta's).
    """
    explicit = bool(a["delta"])
    plain = not (a["chunks"] or a["commit"] or a["base"] or a["full"] or a["force"])
    if not it or not (explicit or (plain and cfg.review.delta_default)):
        return False
    item, gate = a["item"], a["gate"]
    last = _last_review(log, item, gate)
    head = str(last.get("reviewed_head") or "")
    if not head:
        return explicit  # --delta says "run the full review first"; a plain review just is one
    if str(last.get("status")) != "REVIEWED":
        if not explicit:
            say(
                f"full round: the last {gate} review of {item} was {str(last.get('status')).lower()}"
                ", so a delta from it would leave part of the diff unreviewed."
            )
            return False
    tip = _head_of(repo, it, a["branch"], "")
    if not tip or not W.git(repo, "merge-base", "--is-ancestor", head, tip).ok:
        say(
            f"full round: the reviewed head {head[:10]} is not an ancestor of the current "
            f"head {tip[:10] or '(unknown)'} (the branch was rebased or amended), so a delta "
            "from it would not be what changed."
        )
        return False
    if not explicit:
        say(
            f"[review].delta_default: {item}.{gate} has a recorded review, so only what "
            f"changed since {head[:10]} is reviewed (`--full` forces a full round)."
        )
    return True


def _last_review(log, item: str, gate: str) -> dict:
    """The evidence of the gate's latest `ddflow review`, read from the LOG (the head and
    its status): a re-claim or `gate skip` replaces the gate's current record."""
    for e in reversed(log.read_all()):
        if e.subject == item and e.kind.startswith("gate.") and e.data.get("gate") == gate:
            ev = e.data.get("evidence") or {}
            if ev.get("reviewed_head"):
                return ev
    return {}


def _last_head(log, item: str, gate: str) -> str:
    """The head the gate's latest `ddflow review` covered, read from the LOG: a re-claim,
    `gate skip` or `gate record` replaces the gate's current evidence, and a delta must
    stay possible after any of them (the count survives them too)."""
    for e in reversed(log.read_all()):
        if e.subject == item and e.kind.startswith("gate.") and e.data.get("gate") == gate:
            head = (e.data.get("evidence") or {}).get("reviewed_head")
            if head:
                return str(head)
    return ""


def _kind(repo, cfg, log, item, gate, chunks, delta, commit, base) -> str:
    """full | delta | chunk: only a `full` round counts against `[review].max_rounds`.

    Judged by what a review COVERS, not by the flag that asked for it: `--delta` always
    is one; a `--commit` or `--base` review is one only when that ref can only be
    narrower than the item's whole diff -- at or after the head the gate's last review
    covered, or strictly inside the item's own line (after its merge-base with the base
    branch, up to the reviewed head). A ref that reaches back to the base branch or before can cover it all, and
    counts as a full round.
    """
    if chunks:
        return "chunk"
    if delta:
        return "delta"
    ref = commit or base
    head = _last_head(log, item, gate) if ref and item else ""
    if not head:
        return "full"
    if W.git(repo, "merge-base", "--is-ancestor", head, ref).ok:
        return "delta"
    start = W.git(repo, "merge-base", cfg.worktree.base_ref or W.default_branch(repo), head)
    inside = (
        start.ok
        and W.git(repo, "merge-base", "--is-ancestor", start.out, ref).ok
        and W.git(repo, "merge-base", "--is-ancestor", ref, head).ok
        and W.git(repo, "rev-parse", ref).out != W.git(repo, "rev-parse", start.out).out
    )
    return "delta" if inside else "full"


def _budget(cfg, item, gate, done, force, reason, say) -> tuple[str, str]:
    """(why this full round is refused, "") or ("", the reason it was forced past the cap)."""
    cap = cfg.review.max_rounds
    if cap <= 0 or done < cap:
        return "", ""
    if cfg.review.on_exceed == "warn":
        say(
            f"WARNING: {item}.{gate} has had {done} full review rounds, past "
            f"[review].max_rounds = {cap}: prefer --delta and `review triage`."
        )
        return "", ""
    if force and reason.strip():
        say(f"--force: round {done + 1} of {item}.{gate} runs past max_rounds = {cap}.")
        return "", reason.strip()
    if force:
        return '--force needs --reason "...": the exception is recorded.', ""
    return _budget_refusal(item, gate, done, cap), ""


def _delta_scope(repo, it, log, gate, branch) -> tuple[str, str, str]:
    """(diff, how, why not) for `--delta`: the changes since the recorded review's head."""
    head = _last_head(log, it.id, gate) if it else ""
    if not head:
        return (
            "",
            "",
            f"--delta rechecks what changed since the head of the item's recorded {gate} "
            "review, and none is on record: run the full review first.",
        )
    diff = _delta_diff(repo, it, branch, head)
    if not diff.strip():
        return (
            "",
            "",
            f"nothing changed since the reviewed head {head[:10]}. `--full` reviews the "
            "whole diff again (a full round).",
        )
    n = _commits_since(repo, it, branch, head)
    what = f"{n} commit{'' if n == 1 else 's'}" if n else "uncommitted changes"
    return diff, f"delta review of {what} since {head[:10]}", ""


def _commits_since(repo: Path, it, branch: str, head: str) -> int:
    """How many commits the item's tip has past `head` (0: only working-tree edits)."""
    tip = _head_of(repo, it, branch, "")
    r = W.git(repo, "rev-list", "--count", f"{head}..{tip}") if tip else None
    return int(r.out) if r is not None and r.ok and r.out.strip().isdigit() else 0


def _head_of(repo: Path, it, branch: str, commit: str) -> str:
    """The commit a review covered: what `--delta` later diffs from. "" when unknown."""
    from ..infra import worktree as W

    wt = W.load_path(repo, it.worktree) if it and it.worktree else None
    where, ref = (
        (repo, commit)
        if commit
        else (repo, branch)
        if branch
        else (wt, "HEAD")
        if wt and wt.exists()
        else (repo, it.branch if it and it.branch else "HEAD")
    )
    r = W.git(where, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    return r.out.strip() if r.ok else ""


def _delta_diff(repo: Path, it, branch: str, head: str) -> str:
    """What changed since `head`: the item's branch or tree against that commit."""
    from ..infra import worktree as W
    from ..services.enforce import SELF_MANAGED

    wt = W.load_path(repo, it.worktree) if it and it.worktree else None
    if not branch and wt and wt.exists():
        return W.capture_diff(wt, head, include_untracked=False)
    tip = branch or (it.branch if it and it.branch else "")
    if tip:
        d = W.git(repo, "diff", "--no-color", f"{head}...{tip}")
        return (d.out + "\n") if d.ok and d.out else ""
    return W.capture_diff(repo, head, include_untracked=False, exclude=SELF_MANAGED)


def _log_started(log, it, item: str, gate: str) -> None:
    """The review's start, so reviewer latency (started -> outcome) is derivable (B7ed5137d45).
    Only for a real item: a diff reviewed with no item has no gate to start."""
    if it is not None:
        log.append("gate.started", item, {"gate": gate})


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
    delta: bool = False,
    force: bool = False,
    reason: str = "",
    full: bool = False,
) -> O.Outcome:
    """Run every reviewer configured for `gate`, and record the outcome against `item`.

    A FULL round -- anything that can cover the item's whole diff -- is counted against
    `[review].max_rounds`; `delta` reviews only what changed since the head the gate's
    last review covered (as does a `commit`/`base` at or after that head), and neither
    it nor triage is ever refused.
    `force` with a `reason` runs a full round past the cap, recorded in the evidence.

    With `[review].delta_default` (on unless a project turns it off) a plain review of a
    gate that already has a recorded review is a `delta` too; `full` forces a full round.
    A delta's findings are merged into the gate's record (`_merge_delta`).

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
    if (full or force) and delta:
        return O.failed(
            "review",
            f"{'--full' if full else '--force'} and --delta contradict each other: "
            f"{'--full' if full else '--force'} is a full round, --delta reviews only what "
            "changed since the reviewed head.",
            id=item,
            gate=gate,
            outcome="",
            findings=[],
            text="",
        )

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

    it = st.items.get(item)
    kind, done, forced, diff, how, why, rerun = _scope(
        repo,
        cfg,
        st,
        log,
        it,
        say,
        revs,
        locals_={
            "item": item,
            "gate": gate,
            "chunks": chunks,
            "delta": delta,
            "commit": commit,
            "base": base,
            "branch": branch,
            "called_from": called_from,
            "force": force,
            "reason": reason,
            "full": full,
        },
    )
    if why:
        return O.Outcome(
            kind="review",
            data={"id": item, "gate": gate, "outcome": "", "findings": [], "how": how, "text": why},
            exit=O.REFUSED,
            reason=why,
        )
    if not diff.strip():
        return unavailable(
            f"Empty diff ({how}) — nothing to review. Recording UNAVAILABLE.", how=how
        )

    intent = intent or _item_intent(it)
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

    revs, prior, only = _announce_rerun(rerun, revs, item, gate, say)

    keeps: dict[str, _ReplyFile] = {}
    overrides = P.overrides_from(cfg)
    tick_s = min(PROGRESS_EVERY_S, max(1, cfg.lease.heartbeat_s))
    keep_lease = _lease_ticker(log, cfg, it, tick_s)
    _log_started(log, it, item, gate)
    results = []
    for r in revs:
        keep = keeps[r.name] = _ReplyFile(repo, item, gate, r.name)
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
            on_chunk=keep.add if item else None,
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
        evidence = {
            **best.evidence(),
            "diff_source": how,
            "diff_chars": len(diff),
            **_round_evidence(
                repo,
                it,
                branch,
                commit,
                kind,
                done,
                best.status,
                forced,
                _delta_rounds(log, item, gate),
            ),
            **(keeps[best.reviewer].evidence() if best.reviewer in keeps else {}),
        }
        held = _merge_delta(log, it, gate, kind, best.status, evidence, say)
        outcome, reason = _hold(outcome, _reason_of(best), held, say)
        G.record(
            log,
            cfg,
            item,
            gate,
            outcome,
            reason=reason,
            evidence=evidence,
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
        "findings": _finding_rows(best),
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
