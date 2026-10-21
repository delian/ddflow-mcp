"""The `[review]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("review")
@dataclass
class ReviewConfig:
    """The review-round budget (decision D-review-budget): a shipped default for every
    project, so a review loop is bounded without anyone writing a config line."""

    max_rounds: int = knob(
        2,
        doc='How many cross-family review rounds one gate (rubber_duck, critic) may have on one item, full and delta alike (default 2; 0 = unlimited; decision D-gate-economy 2 -- a delta after the cap was the way around it). Later rounds each find fewer defects than the one before, so after the cap the way forward is `ddflow review triage` (refute each remaining finding with the run that shows it false, or confirm it with the test that now passes; always allowed), then recording the gate on that triage or asking the operator. A re-review of named chunks (--chunk) repeats part of a recorded round and is not counted. `--force --reason "..."` runs one more round and records why. Change it for the project (`ddflow config --set review.max_rounds 3`), for this machine (add --local), per run (DDFLOW_REVIEW_MAX_ROUNDS), or over MCP with `ddflow_configure` (the operator is told when an agent does).',
    )
    settle_all_refuted: bool = knob(
        True,
        doc="Whether the `ddflow review triage` that gives the LAST open finding of a failed review round its verdict, when every finding of the round was refuted, records the gate passed on refutation (flagged, with each refutation's probe as evidence) at once, instead of leaving it failed until the round budget is spent -- which sent agents to re-record the gate passed by hand. A round with a confirmed finding is never settled by triage alone. false = the gate stays failed until the budget settles it.",
    )
    on_exceed: str = knob(
        "refuse",
        doc="What a round beyond [review].max_rounds does: 'refuse' (default; exit 3, naming `review triage`, --force --reason and how to change the cap) or 'warn' (run it and say the budget is spent). `review triage` is never refused either way; a delta counts against the cap like any round.",
        choices=("refuse", "warn"),
        strictest=("refuse", "a round past the budget is refused"),
    )
    delta_default: bool = knob(
        False,
        doc="Whether `ddflow review <id> --gate G` on a gate that already has a recorded review rechecks ONLY the commits since the head that review covered, instead of the whole diff. Default false (decision D-gate-economy 3): a re-review sends the item's WHOLE diff against its base together with the previous findings and the author's triage of each, so the reviewer confirms each one against the current code and looks for new issues -- a delta that saw only the follow-up commit kept reporting the fix as absent. A re-review is a full round (counted against review.max_rounds). true = the automatic delta: the delta's findings and coverage are merged into the gate's record (earlier findings keep their triage when byte-identical), it is not a full round but counts against review.max_rounds, and the output says 'delta review of N commits since <sha>'; a rebased branch or a partial prior review falls back to a full round and says why. `--delta` asks for one delta whatever the default (for a very large diff); `--full` forces a full round. Change it for the project (`ddflow config review.delta_default true`), for this machine (add --local), per run (DDFLOW_REVIEW_DELTA_DEFAULT=1), or over MCP with `ddflow_configure` (the operator is told when an agent does).",
    )
    fix_round_free: bool = knob(
        True,
        doc="Whether a pure fix round is free: when commits since the head the last review covered rewrote at least one cited line and every finding of that round is rewritten or settled by the author, the next `ddflow review` is a delta over just those changes (prior findings whose cited lines were rewritten are marked superseded, not re-raised) and is not counted against review.max_rounds, nor refused past it. Default true; false counts it like any round. Change it for the project (`ddflow config --set review.fix_round_free false`), for this machine (add --local), per run (DDFLOW_REVIEW_FIX_ROUND_FREE), or over MCP with `ddflow_configure` (the operator is told when an agent does).",
    )
    combined_under_lines: int = knob(
        150,
        doc="`ddflow review <id> --gate rubber_duck,critic` on a change with fewer changed lines than this (default 150; 0 = never) sends ONE review request whose prompt carries both gates' lenses and records its outcome for each gate, with a shared `review_id` in their evidence (decision D-gate-economy 2: a small fix spent 2.9 rounds of each gate, most failing on findings later refuted). A larger change keeps one review per gate, one after the other. Not for --chunk, --delta, --commit or --base, nor when either gate is out of rounds, the two are served by different reviewers, or a gate is not rubber_duck or critic; the combined review is a full round of each gate. Change it for the project (`ddflow config review.combined_under_lines 100`), for this machine (add --local), per run (DDFLOW_REVIEW_COMBINED_UNDER_LINES), or over MCP with `ddflow_configure`.",
    )
    exclude_globs: list[str] = knob(
        factory=lambda: ["tests/golden/**", "*transcript*.json", "uv.lock", "*.ambr"],
        doc="Globs of generated and pinned files (golden snapshots, transcripts, lockfiles) that `ddflow review` leaves out of the diff it sends reviewers: a re-pinned transcript turned a 3 KB change into an 11-chunk review that took 7 minutes and judged generated text. A bare pattern matches at any depth. The review output and the gate's evidence say how many files and bytes were left out and name them, so a skipped file is never silent. Change it for the project (`ddflow config review.exclude_globs ...`), for this machine (add --local), or over MCP with `ddflow_configure`; an empty list leaves out no glob-matched file (the files pins.toml owns are governed by [review].exclude_pin_files, which must be turned off as well to send every file).",
    )
    manifest_max_files: int = knob(
        40,
        doc="How many files the manifest of the whole change lists in every review chunk's prompt (default 40; 0 = no manifest). A chunk is judged alone otherwise, and reviewers reported 'no templates' or 'nothing adds the guard' about facts a sibling chunk answered. Each row gives a file's added/removed lines and a one-line summary; the new files and the files left out by [review].exclude_globs are named; files past the cap are counted. Change it for the project (`ddflow config review.manifest_max_files 80`), for this machine (add --local), per run (DDFLOW_REVIEW_MANIFEST_MAX_FILES), or over MCP with `ddflow_configure`.",
    )
    exclude_pin_files: bool = knob(
        True,
        doc="Whether the files `.ddflow/pins.toml` owns (README regions, golden snapshots, the setup transcript: regenerated, never read) are left out of review chunks as well as [review].exclude_globs. Default true; false sends them.",
    )
    chunk_max_tokens: int = knob(
        0,
        doc="A cap on the tokens ONE review chunk's request may spend, below the reviewer's own `max_tokens` (default 0 = the reviewer's own). A reasoning model sampled cold can loop until its whole budget is gone (lesson Lf3feebf5d3); a chunk that does so is halved and retried (`[review].split_rounds`), so a lower cap finds out sooner. It never raises a reviewer's `max_tokens`.",
    )
    split_rounds: int = knob(
        2,
        doc="How many times a review chunk that never came back -- out of tokens (`TRUNCATED`) or out of time (`DID NOT CONVERGE`, a request cut at the reviewer's `timeout_s`) on every copy -- is split in halves and sent again (default 2; 1 = once, and a value below 1 is read as 1; each half that is lost again is halved in the next round; all inside the reviewer's `total_budget_s`). A chunk some halves of which still never came back is recorded as unreviewed with how many parts were, and the findings of the parts that were are kept (bug B587e607ad9: a ~4.5k-char chunk got no answer in 1800 s twice).",
    )
