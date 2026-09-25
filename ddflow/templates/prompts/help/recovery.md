# When something goes wrong

    ddflow doctor       integrity and health; exit 1 = problems, each named
    ddflow recover      find crashed agents' work; exit 2 = nothing to recover
    ddflow rebuild      re-derive the search index from the log
    ddflow replay       reconstruct the project's decisions from the log alone

## A crashed agent

Its lease expires; its worktree is still on disk with its work in it. `ddflow recover`
finds it, measures what is there — commits ahead, uncommitted changes, untracked files —
and tells you whether it is salvageable. **It is never stolen automatically.** A lease
that expired because an agent was thinking for an hour looks exactly like one that
expired because it died, and the difference is only visible to a human.

`ddflow recover --apply` adopts it, keeping the work. Nothing is deleted.

## A broken index

The index is a disposable, gitignored SQLite projection. Anything wrong with it is fixed
by `ddflow rebuild`, which drops it and re-derives it from the log. There is
deliberately no incremental update: an incremental projector is a second implementation
of the fold that can disagree with it, and a cache that disagrees with its source
silently is worse than no cache.

## A missing everything

`ddflow replay --out ./recovery-kit` writes a reconstruction addressed to a fresh
agent: every operator prompt in order, every decision with its reason, every research
verdict including the approaches that were tried and rejected with the measurement that
killed them, and the queue's shape.

It states its own limit: it reproduces the DECISIONS, not the bytes. Model outputs are
not deterministic, so replaying prompts will not recreate the original source. What it
recreates is every input that produced it, which no other artefact holds.
