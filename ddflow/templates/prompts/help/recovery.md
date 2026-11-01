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

`ddflow recover --apply` acts only on an expired lease whose tree it measured EMPTY: it
records the expiry, saying nothing was lost. A tree holding work, or one it could not
measure, is never touched. Either way the item is taken back with `ddflow claim <item>
--force`, which adopts the existing worktree, the work in it included. Nothing is
deleted.

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

## Events that vanished

A `git push -f` of an older main, a revert of the events commit, a `git reset --hard` or a
deleted shard removes events from the tree, and nothing in the files says so. ddflow keeps a
machine-local high-water mark per branch and shard (`.ddflow/local/shard-marks`: how many
events this clone last saw, and the id of the last), raised by every command and lowered by nothing but the explicit acceptance below.
A shard that now holds fewer is a PROBLEM in `ddflow doctor` and a "Lost events" section at the
top of the brief, with where they may still be: `git reflog`, `git log --all --
.ddflow/events/<shard>`, `git show <sha>:.ddflow/events/<shard>`. Nothing is rewritten.

Once you have decided (restored them, or the loss was meant), the operator runs
`ddflow doctor --accept-lost-events`: it records the present counts and the log is not touched.
It is CLI-only: an agent reports the PROBLEM to the operator, it does not accept it.

The limit: the mark is a count plus the last id. An interior event removed and the shard then
regrown to the old count, before any ddflow command looks, cannot be told from growth; that
needs a per-event record (the P-multiuser sync-check).

## History that cannot be mended

An append-only log keeps two kinds of fact forever: events whose content no longer matches their
address (a secret scrub replaced a key inside event lines) and a Lamport clock that decreases in a
shard (two clones wrote as one agent id). `ddflow doctor` reports them as PROBLEM until a person
acknowledges them: `ddflow doctor --accept-history --history-reason "why" [--history-note "scrub
commit abc123"] [--history-only ID,SHARD:N]` records the findings open now, with who and why, in
the log (a `repair.applied` of `accepted-history`). Doctor then lists each as an `accepted history`
note and exits 0 if nothing else is wrong. The acceptance binds to the event's id and its content as
accepted (and to the clock regression's shard, position and values): a later edit of the same
event, or a new mismatch or regression, is a PROBLEM again. Person-only, like
`--accept-lost-events`: an agent (CLI or MCP) is refused and reports the finding to the operator.
