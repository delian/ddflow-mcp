# Using ddflow with more than one person or clone

The event log lives **in the project's own tree**, committed on the same branches as the
code (decision `D-log-in-main-tree`, B193). Every checkout's queue therefore matches its
code, and `ddflow replay` works from a plain clone. This page says what that buys when
several people, machines or agents share one repository, what it does not, and what to do
about the difference. Everything marked *probed* is exercised by
`tests/test_multiuser_merge_model.py` against real git.

## What merges

- **One append-only shard per writer**: `.ddflow/events/<agent>.jsonl`. The agent id of an
  adopted project carries a random per-clone suffix, so two clones do not share a shard
  even when host and directory names match (B190).
- Two people adding events produce two different files. Git merges them with no conflict
  and no merge driver at all. *Probed*: `test_distinct_writers_merge_cleanly_...` runs with
  and without the `.gitattributes` line and merges cleanly both ways.
- Events are content-addressed and sorted by `(lamport, agent, id)`, so folding the merged
  log gives every clone the same answer, and replaying a shard twice changes nothing.
- `.gitattributes` carries `.ddflow/events/*.jsonl merge=union` so that the rare case of
  two branches appending to the **same** shard concatenates instead of conflicting.

## What a forge may not do (UNVERIFIED)

Whether GitHub's or GitLab's server-side merge button (merge commit, squash, rebase)
honours `merge=union` is **unverified**: no forge is reachable from the environment this
was written in, and neither forge documents support for `.gitattributes` merge drivers.
Plan as if it does not. The consequence is narrow, and follows from the shard layout:

| Situation | Forge ignores `merge=union` | Local `git merge` (attribute honoured) |
|---|---|---|
| Two branches, different agents (the normal case) | clean, different files | clean |
| Two branches appended to the **same** shard | PR shows a conflict in `.ddflow/events/<agent>.jsonl` | clean |

For the second row, merge the target branch into yours locally (`git pull --no-rebase`),
push, and the PR merges cleanly. *Probed*:
`test_one_shard_written_by_two_branches_conflicts_if_union_is_not_honoured`. Never resolve
that conflict by picking one side: both sides' lines are events someone wrote. Never
squash a branch's log commits away from its code; the item events and the commit they cite
belong together. Rendered files (`docs/ddflow/QUEUE.md`, `LESSONS.md`) are derived and may
conflict on any forge: take either side and run `ddflow render`.

## What is detected

Merging cannot tell that two offline clones did incompatible things; ddflow can, after the
fact (B191). Two `item.added` events for one id, or two overlapping claims on one item,
fold to a **contested** item instead of letting the later event win: `show` prints a
`CONTESTED` block, `next` withholds the item, `doctor` names it and exits non-zero. A person
settles it with `ddflow resolve <id> --keep <holder-or-event>`. See the README section
"When two clones disagree".

## What is locked

By default **nothing** is: a claim is an event in this clone's log, and two clones that
have not yet pulled each other can both claim one item. The opt-in remedy (B192) is a
cross-machine claim lock: with `[flow] claims = "remote"` a claim also creates
`refs/ddflow/claims/<id>` on the remote by compare-and-swap, so only one online clone wins;
release, completion and expiry delete the ref. A refused claim exits 3 and names the
holder. An unreachable remote is recorded as unavailable, never silently treated as local.
B192 ships separately from this page; until your ddflow includes it, claims are local only.
Even with it, an agent that is offline claims locally and is detected as above.

## What everyone with repository access can read

The log is committed, so **everything in it is as visible as the source**. In particular
`session.prompt` events record the operator's words verbatim (`ddflow session prompt`, or
the `UserPromptSubmit` hook), and notes, decisions, lessons, bug reports and gate evidence
are recorded the same way. Anyone who can read the repository (including forks of a public
one, and its history after the fact) can read what was typed to the agent.
*Probed*: `test_committed_session_prompts_are_in_tracked_files`. Do not put secrets in a
prompt, a note or a `--evidence` string. Machine-local state (`.ddflow/local/`, the index,
the lock) is git-ignored and is not shared. If a secret is committed, treat it as
published: rotate it. Rewriting the log is not a substitute, because history keeps it.
