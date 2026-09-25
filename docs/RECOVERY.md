# Recovery runbook

Written for the person at the terminal at 2 a.m. Every procedure starts with an
inspection and ends with a decision, because the one irreversible mistake available here
is deleting a worktree that held the only copy of something.

**The rule underneath all of it:** the event log (`.ddflow/events/*.jsonl`) is the only
thing that must survive. Everything else — the index, the boards, the worktrees — is
either derived or replaceable.

---

## First response, always

```sh
ddflow doctor          # integrity, cycles, unknown deps, orphaned trees, stale index
ddflow recover         # what a crash left behind, and what is in it
```

`doctor` exits 1 if it found a problem, `recover` exits 2 if there is nothing to recover.

---

## 1. An agent crashed mid-task

**Symptom.** An item sits `running`, or a lease expired, or a worktree exists with no
owner.

```console
$ ddflow recover
1 recoverable situation(s); 1 may contain work:

!! P2.T3  [expired_lease]  was: agent-hostname-41823
     worktree /srv/proj/../.ddflow-worktrees/P2.T3
     INSPECT FIRST — 3 uncommitted file(s), 2 unmerged commit(s).
     `git -C .../P2.T3 diff main` then salvage,
     then `ddflow release P2.T3 --note salvaged`.
```

Entries marked `!!` were **measured** to contain work. ddflow will not touch them.

**Procedure.**

1. **Look before anything else.** A dirty worktree is *not* automatically unshipped work
   — it is just as often a superseded draft the agent had already replaced.

   ```sh
   git -C <worktree> status
   git -C <worktree> diff <base>          # uncommitted changes
   git -C <worktree> log --oneline <base>..HEAD    # commits not yet merged
   ```

2. **Decide, using the diff and not the status.** Compare the working files against the
   base branch, not against the worktree's own HEAD — the question is "does this contain
   anything `main` does not", and `git diff HEAD` cannot answer it.

3. **If it is real work**, finish it: commit in the worktree, then merge and complete
   normally.

   ```sh
   git -C <worktree> add <explicit paths> && git -C <worktree> commit -m "P2.T3: salvaged"
   ddflow release P2.T3 --note "salvaged 3 files"
   ddflow claim P2.T3                    # adopts the SAME worktree; no second tree
   ddflow merge P2.T3 && ddflow complete P2.T3 --model "<your model>"
   ```

4. **If it is genuinely nothing**, release and remove:

   ```sh
   ddflow release P2.T3 --note "inspected: superseded draft, discarded"
   git worktree remove <worktree>
   ```

**Bulk case.** `ddflow recover --apply` expires only the leases whose trees it measured
as *empty*. Anything marked `!!` is left for you, whatever the configured policy — the
knob controls convenience, never safety.

---

## 2. A lease is stuck and the holder is definitely gone

ddflow refuses to steal, by design. Override deliberately:

```sh
ddflow recover --item P2.T3        # confirm what is in the tree FIRST
ddflow release P2.T3 --note "holder confirmed dead: host rebooted 03:14"
ddflow claim   P2.T3               # or: ddflow claim P2.T3 --force
```

The `--note` lands in the event log. Six months later that sentence is the only record of
why a claim was broken.

---

## 3. The whole machine died mid-session

Nothing special is required. The log is `fsync`ed on every append, so the last successful
append is durable, and an interrupted one leaves a torn final line that readers skip and
`doctor` reports.

```sh
ddflow doctor          # reports "N unparseable line(s) — likely a torn append"
ddflow rebuild         # re-derive the index; the torn line is ignored
ddflow recover         # then work through §1 for each tree
```

A torn line loses at most the one event being written when power failed. It cannot
corrupt earlier events, because appends never rewrite existing bytes.

---

## 4. The index is corrupt, or you upgraded ddflow

The index is disposable. This is never a data-loss event.

```sh
rm -f .ddflow/index.db*
ddflow rebuild
```

There is no migration path and none is needed: the schema version is part of the
staleness check, so an upgraded ddflow rebuilds automatically on first read.

---

## 5. Two branches diverged, or a merge brought duplicate events

Nothing to do. Event ids are content addresses, so the union of two logs deduplicates
itself, and the `(lamport, agent, id)` ordering is total and deterministic.

```sh
git merge <other-branch>      # shard files rarely conflict; each agent owns one
ddflow rebuild
ddflow doctor
```

**If a shard file *does* conflict** (only possible if two machines shared one agent id):
resolve by **keeping both sides' lines** — the file is append-only, order within it is
recoverable from the Lamport field, and duplicates are removed on read.

```sh
git checkout --theirs .ddflow/events/<shard>.jsonl   # then re-add yours:
git show :2:.ddflow/events/<shard>.jsonl >> .ddflow/events/<shard>.jsonl
sort -u .ddflow/events/<shard>.jsonl -o .ddflow/events/<shard>.jsonl
ddflow doctor
```

To prevent it recurring, give each machine a distinct agent id (`--agent`, or
`[agent].id` in `.ddflow/config.toml`).

---

## 6. Someone hand-edited the log

```console
$ ddflow doctor
  PROBLEM: e7a3f… : content does not match its address (edited after the fact?)
```

The event's id is the hash of its body, so any edit is detectable. There is no automatic
repair, because guessing at the original content would be worse than the edit.

- If the edit was a mistake, restore that shard from git history:
  `git checkout HEAD~1 -- .ddflow/events/<shard>.jsonl`
- If the change was intended, express it as a **new event** instead. The log is
  append-only; a correction is a later event, never a rewrite. This is the same discipline
  the ledger domain calls a compensating entry.

---

## 7. Everything is gone except the log

This is the case the whole design is for.

```sh
mkdir recovered && cd recovered && git init
mkdir -p .ddflow && cp -r /backup/events .ddflow/events
ddflow rebuild
ddflow replay --out ./recovery-kit
```

You get:

| File | Contents |
|---|---|
| `RECONSTRUCTION.md` | Every operator prompt in order, every research verdict, every lesson, the queue's shape, and every rejected approach **with the measurement that killed it** |
| `QUEUE.md` | The work queue as it stood |
| `LESSONS.md` | Everything learned |

Hand `RECONSTRUCTION.md` to a coding agent with an empty repository and ask it to work
through the instructions in order.

**What this does and does not claim.** It reproduces the *decisions*, not the bytes.
Model outputs are not deterministic, so the rebuilt source will differ. What it carries
is every input that produced the original — including the operator's stated *reasons* for
constraints, which is the part that normally exists only in someone's memory.

If the original repository still exists, check the log still describes it:

```sh
ddflow replay --verify     # every recorded commit sha must still resolve
```

A sha that does not resolve is not necessarily corruption — a rebased or squashed branch
loses shas legitimately — so it reports rather than fails.

---

## 8. Worktrees exist that nothing claims

```console
$ ddflow doctor
  note: worktree /srv/.ddflow-worktrees/P1.T9 exists but no item claims it
```

Usually left by a removed item or a `--force` release. Inspect as in §1, then:

```sh
git worktree remove <path>        # refuses if dirty or ahead
git worktree prune
```

---

## What never to do

- **Never `git checkout` / `git switch` in the primary checkout while agents are live.**
  It swaps files underneath them. ddflow merges *from* the primary without a checkout,
  and refuses if the primary is on the wrong branch rather than switching it for you.
- **Never `git add -A` in a shared tree.** A parallel agent's unrelated file in your
  commit is very hard to notice and very hard to undo.
- **Never delete a worktree you have not diffed against the base branch.**
- **Never hand-edit `.ddflow/events/`.** Append a correcting event instead.
- **Never treat exit 2 as exit 0.** "Could not run" is not "fine".

---

## The MCP session ended and nothing said why

**Symptom.** A tool call returns nothing. The client reports a closed stream. The server
process has exited **zero**, its stderr is empty, and the log shows the previous call
succeeding normally.

**Cause, almost always.** A child process took the server's stdin. ddflow speaks MCP
over stdio — the JSON-RPC session *is* the process's stdin and stdout — so any child
spawned without an explicit `stdin=` inherits that pipe. A child that reads stdin eats
the protocol bytes; one that closes it ends the session.

Inside ddflow this cannot happen any more: every subprocess goes through `proc.run`,
which detaches stdin, and `tests/test_stdio_safety.py` fails the suite if a module
reaches for the stdlib directly. What remains is **your own gate commands**:

```toml
[gate.unit_tests]
command = "pytest"           # fine
command = "make test"        # fine
command = "npm test"         # fine unless a script prompts
```

A command that *prompts* — a migration asking for confirmation, a linter offering to
fix, anything that reads a TTY — is the hazard. ddflow hands it an empty stdin, so it
will see EOF rather than hang; but a command whose behaviour on EOF is to wait anyway
will hold the gate until `timeout_s`.

**Diagnosis.** Run the gate command yourself with stdin closed:

```sh
ddflow gate run <id> unit_tests < /dev/null
```

If that hangs, the command is the problem, not ddflow. Add `--yes`/`--ci`/
`--non-interactive`, or set `[gate.unit_tests].timeout_s` low enough that a stuck gate
reports rather than parks.

**If the session has already ended:** nothing is lost. The event log is on disk and
every completed call is in it — restart the server and run `ddflow doctor`.
