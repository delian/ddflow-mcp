Land everything that is in flight, then leave the tree clean.

Work through these in order. Each step is reported before it is performed; nothing with uncommitted changes is ever touched automatically.

## 1. Find what was left behind

    ddflow_recover     — crashed agents' worktrees, with what is actually in each
    ddflow_cleanup     — every ddflow worktree and branch, classified

`ddflow_cleanup` classifies each tree as **merged** (safe to remove), **unmerged** (carries commits nobody landed), **dirty** (uncommitted edits — a human looks), **orphan** (no item claims it), or **stale_branch**.

## 2. Deal with the dirty ones FIRST, by hand

A dirty worktree is the only thing here that exists nowhere else. It is also *not* automatically valuable: as often as not it is a superseded draft. Judge it by diffing against the base branch, not by its `git status`:

    git -C <worktree> diff <base>
    git -C <worktree> log <base>..HEAD

Then either finish it (commit, gate, merge) or release it with a note saying what you concluded. `ddflow_release` records that note, and six months from now it is the only explanation of why a claim was broken.

## 3. Land the unmerged work

For each tree with commits and a finished item: merge it. For each with commits and an *unfinished* item: finish the item properly — gates and all — or abandon it deliberately.

    ddflow_merge <item>
    ddflow_complete <item> --model "<your model>"
    ddflow_abandon <item> --reason "..."    # if it will not be finished

Do not merge an item whose gates have not run. "Landing it to tidy up" is how unreviewed code reaches the main branch.

## 4. Remove what is finished

    ddflow_cleanup with apply=true   — removes merged worktrees and merged branches only

## 5. Hunt bugs, then deduplicate

Run the bug hunt, then the deduplication pass, in that order — deduplicating first would merge two copies of a defect into one place and make it harder to see that it *was* two.

## 6. Commit, and check nothing is looping

    ddflow_loops       — a clean-up that keeps finding the same work is a loop, not a chore
    ddflow_doctor      — integrity, cycles, unknown dependencies, stale index
    ddflow_progress    — what the effort actually went into

Commit the event log along with the code: it is the source of truth, and a clean tree with an uncommitted log is not clean.
