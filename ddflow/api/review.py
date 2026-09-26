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


def diff_for(repo: Path, cfg, st, item: str, base: str = "") -> tuple[str, str]:
    """(diff, how) for an item: its worktree branch vs base, plus the working tree.

    Goes through `worktree.capture_diff`, which includes UNTRACKED files via
    intent-to-add. A plain `git diff` omits them, so the regression test an agent just
    wrote is invisible to the reviewer — which then reports, correctly given its input and
    wrongly given the facts, that the change ships no tests.
    """
    it = st.items.get(item)
    base = base or cfg.worktree.base_ref or W.default_branch(repo)
    wt_path = W.load_path(repo, it.worktree) if it and it.worktree else None
    if wt_path and wt_path.exists():
        diff = W.capture_diff(wt_path, base)
        if diff.strip():
            ok, missing = W.diff_covers_everything(wt_path, diff)
            how = f"{base}..HEAD + working tree in {wt_path}"
            if not ok:
                how += f" (WARNING: {len(missing)} changed path(s) absent from the diff)"
            return diff, how
    return W.capture_diff(repo), f"working tree in {repo}"


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


def reviewers_detect(repo: Path, *, write: bool = False, agent: str = "") -> O.Outcome:
    """Probe well-known local ports for an OpenAI-compatible endpoint."""
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
        path = repo / ".ddflow" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = path.read_text("utf-8") if path.exists() else ""
        path.write_text(prev.rstrip() + "\n" + "".join(blocks), "utf-8")
        written = str(path)
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


def review(
    repo: Path,
    *,
    gate: str = "critic",
    item: str = "",
    intent: str = "",
    context: str = "",
    base: str = "",
    on_progress: Callable[[str], None] | None = None,
    agent: str = "",
) -> O.Outcome:
    """Run every reviewer configured for `gate`, and record the outcome against `item`."""
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

    diff, how = diff_for(repo, cfg, st, item, base)
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

    overrides = P.overrides_from(cfg)
    results = []
    for r in revs:
        say(f"→ {r.name} ({r.resolved_family()}) reviewing {len(diff)} chars from {how}")
        res = R.review(r, diff, intent, context=context, repo=repo, prompt_overrides=overrides)
        results.append(res)
        say(
            f"  {res.label}: {len(res.findings)} finding(s), {res.coverage()}, "
            f"{res.elapsed_s:.1f}s" + (f" — {res.reason}" if res.reason else "")
        )
        for f in res.findings:
            say(f"\n  [{f.severity}] {f.location or f.title}")
            for line in f.detail.splitlines():
                if line.strip():
                    say(f"      {line.strip()}")

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
