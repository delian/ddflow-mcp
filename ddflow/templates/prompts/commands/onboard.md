Onboard this project onto ddflow: from "the MCP server is attached" to a workflow ddflow manages, verified end to end, WITH the operator.
{% if scope %}
**Scope: {{ scope }}.** Do only the stage(s) it names (by number or name below: 0 preflight … 6 verify), and say at the end which stages you did not run. The operator widens it by asking.
{% endif %}
This is the whole path, in order. Each stage ends in something you can show the operator, and each has the question only they can answer. Do not batch the questions into one wall of text at the end: ask each where it arises, because the answer changes what the next stage does. A stage that does not apply is reported as skipped and why, never silently dropped.

Record the operator's words as you go (`ddflow_session_start`, then `ddflow_session_prompt` with their text verbatim), and every structural choice you make together with `ddflow_decision_add`. The next agent reconstructs this onboarding from those records, not from your transcript.

## 0. Preflight: what is already in flight

Before anything is written, find the work that exists only in git. `ddflow onboard preflight` reports it and proposes; what it proves, so you can explain every refusal:

- `git worktree list`, `git branch --format='%(refname:short)'` (LOCAL branches), `git stash list` in the primary checkout.
- **Never the default branch itself**: it is its own ancestor, so the check below calls it "merged". Remote-tracking branches (`git branch -r`) are not yours to delete either: report the ones not in the default branch, and leave them.
- For each other worktree and local branch: `git merge-base --is-ancestor <branch> <default>` is the only honest answer to "is it merged?" A matching commit message is not evidence. A worktree is also unmerged if `git -C <tree> status --porcelain` shows anything beyond caches.
- **Merged and clean** → propose removing the worktree and deleting the branch. A tree that is `locked` belongs to an agent harness (Claude Code locks the worktrees it spawns): check whether the process named in its lock is alive, and say so — removing it ends that session's working directory.
- **Holding unique work** → it is not yours to delete. Show the operator the commits (`git log <default>..<branch>`) and the uncommitted files, and ask: land it, import it as an item (`ddflow_import` proposes unmerged branches), or leave it.
- Stashes are shared by every worktree: never pop or drop one you did not create.

**Ask:** "these N are merged, remove them? these M hold work, what are they?" Remove only what they approved: `ddflow onboard preflight --apply --accept NAME ...` (no `--accept` removes everything the report marked safe; a name it did not mark is refused).

## 1. Setup, from the durable place

- ddflow keeps its state in the PRIMARY checkout (every worktree's agent writes the same `.ddflow/events/`), so setup writes there wherever you call it from. Do this stage when no other agent is editing the primary checkout, and commit the result on the default branch.
- `ddflow_setup` (or `ddflow adopt --agents <yours>` from a shell) writes the driver, the `AGENTS.md` / `CLAUDE.md` block, the MCP launch entry and the git hooks. Then check that `.ddflow/config.toml` and `.ddflow/.gitignore` exist: if the MCP path did not create them, run `ddflow adopt` from a shell, which does — never write those two by hand. Read the launch entry it wrote (`.mcp.json` for Claude Code): its command and `PYTHONPATH` must point at a ddflow that will still exist next week — an installed package, or the operator's main ddflow checkout. A path inside a git worktree is removed when that worktree is, and then the server cannot start and every git hook fails closed.
- `ddflow_hooks` (`action: install`, `claude: true`) puts the brief into every Claude Code session start.
- `ddflow_companions` reports what the gates expect; the `install-companions` prompt installs, with the operator's consent per install. A companion that is installed but not registered is registered with `ddflow_companions_add`.
- **The harness must be allowed to start the servers.** Claude Code only launches a project `.mcp.json` server it has been told to trust: add each registered name to `enabledMcpjsonServers` in `.claude/settings.json` (committed, so worktree sessions get it too).
- **The shell must reach the same ddflow.** The driver and the brief say `ddflow <command>`. If the agent sessions' `PATH` has no `ddflow`, tell the operator and offer a one-line wrapper that runs the same code the MCP entry runs.
- Reviewers: `ddflow_reviewers_detect` finds a local model server; endpoints on a LAN are machine-local, so they go in `.ddflow/local/reviewers.toml` (git-ignored, read last), never in the committed config. A sibling project on this machine that already has one is the fastest answer — ask before copying it.

- **Machine-local values go under `.ddflow/local/`** (`config.toml`, `gates.toml`, `reviewers.toml`), which `.ddflow/.gitignore` ignores. Check that it does before anything under `.ddflow/` is staged; a project set up by an older ddflow may lack the line. `.ddflow/gates.toml` itself is committed project policy.

**Ask:** which companions to install; which reviewer endpoints to use.

## 2. The test gate, measured

Until `unit_tests` has a command, it reports `unavailable`, honestly, and blocks every completion.

`ddflow onboard test-gate` detects the runner from the project's own files and measures a **baseline** in a tree nobody is editing — a detached worktree of the default branch, never the one you are changing — with the pass/fail counts and the failing ids. It proposes the command, the worker line and the smoke run; check its reasoning against what you know:
- A suite over a minute or two runs in parallel (`pytest-xdist` and `-n <workers>` for Python; size the workers so several agents can run the gate at once — `auto` on a many-core machine is usually slower). A worker count sized to one machine goes in `.ddflow/local/gates.toml`, which wins over the committed `gate.unit_tests.command`; `.ddflow/gates.toml` is for what every clone shares, `human = true` checkpoints included. Adding a dev dependency changes the project: ask.
- Tests already failing at the baseline would make the gate red for every item for reasons no item caused. Propose a shrink-only known-failures list, tracked as its own phase, rather than a gate everyone learns to ignore.
- A phase-end `live_test`: the smallest real end-to-end run of the project's own entry point (a few seconds), as a script that fails when it produces nothing.
- Set them with `ddflow_configure`.

**Ask:** the test command and worker count, the known-failures policy, the smoke run.

## 3. Import the history

Follow the `import-existing-project` prompt (fetch it with `ddflow_prompts`, `action: show`). It is the judgement half of `ddflow_import`. Three things it needs from the rest of this onboarding:

- **Point the importer at the right files first.** The `[importer]` globs decide what is history. A user-facing `CHANGELOG.md` that stays live is documentation, not a journal: leave it out of `journal_globs`, or `ddflow_import_verify` reports drift on every release.
- **Read every note of the dry run**, especially the open items it did NOT import ("disposes of them", with examples) and the ones it holds. Check each against the project's own handoff or status document: a box the importer dropped as history may be the work the team thinks is next. File what was wrongly dropped by hand (`ddflow_task_add`, then `ddflow_block` if it waits on something), and release what was wrongly held with `ddflow_unblock` and a note saying why.
- **Order is not a dependency, but gates are.** A section's own verification tasks ("ratchet green", "docs + CHANGELOG") depend on the work they verify: `ddflow_update <id> --needs ...`. Where a handoff document states an order, encode it as `--priority` (lower goes first) so `ddflow_next` offers the work in that order. An open bug item still outranks every priority (`[schedule] bugs_first`).

Then `ddflow_next` must offer the item the team would start with. If it does not, you are not done.

## 4. Cut the old workflow over

Stage 4's scan is `ddflow onboard legacy`: it reads the rulebooks, the slash commands and the named handoff docs and proposes the replacement per line; you and the operator apply what is accepted. Read its proposals knowing what each duty should become:

- Every instruction that writes a now-imported surface: in `CLAUDE.md`, `AGENTS.md`, `CLAUDE.local.md`, the harness's slash commands (`.claude/commands/`), prompt files, and any handoff document. It greps for the file names (`todo.md`, `lessons`, `LOG.md`, the memory store) and for the duties ("tick the checkbox", "STATUS line", "append to the journal"). Name any prompt or rulebook file it did not scan so it is added to the report.
- For each, propose the ddflow replacement, and keep everything else the operator wrote: the checkbox and STATUS discipline becomes claimed items whose gates carry outcomes (`ddflow_complete` refuses a silent gate); "update lessons.md" becomes `ddflow_lesson_add` with a one-line summary; the journal becomes `ddflow_session_note`; "check the lessons first" becomes `ddflow_recall`; a research log's verdicts become `ddflow_research_add` (the citations document itself usually stays live); the worktree procedure becomes `ddflow_claim` → `ddflow_merge` → `ddflow_complete`.
- Rulebook conventions that ddflow can enforce become config, not prose: a "never add Co-Authored-By" rule is `[enforce] forbidden_trailers` (the commit-msg hook refuses it); a weekly bug hunt or dedupe pass is `[cadence] every_days`; a commit-trailer convention is `item_trailer_keys`; a sibling repository this one waits on is `[schedule] repos`.
- A file that is not committed (`CLAUDE.local.md` is often git-ignored) has no history to fall back on: keep a copy before editing it, somewhere outside the working tree, and tell the operator where.
- The harness's own per-project memory (Claude Code keeps it under `~/.claude/projects/<project>/memory/`) holds operational facts: `ddflow onboard memory` offers each still-true one as a `memory.recorded` candidate, deduplicated against what is already remembered; `--apply --accept ID-OR-FILE` records the approved ones (a name that matches nothing is refused, never silently skipped).

**Ask:** show the proposed rulebook diff before applying it; this is the operator's text.

## 5. Freeze what was imported

An edit to an imported file after the cutover reaches no agent and silently forks the record. Make it fail. Arm it with `ddflow onboard legacy --apply`: it lists the freeze candidates (the files the import consumed) and freezes the whole list, or only the names you pass with `--accept FILE`; it reports a ratchet it could not arm as a failure, not a success. The refinement it cannot make for you:

- where the project uses the `pre-commit` framework, a local hook with `language: fail` over those paths;
- otherwise a test that pins each file's hash, so the `unit_tests` gate goes red. Mutation-check it: change a byte, watch it fail, restore.

State in the rulebook and in the ratchet's own message what to do instead.

## 6. Verify, end to end, and commit

Nothing here is done because a command exited 0. `ddflow onboard verify` runs most of this as one report — the registered entry really answers an MCP `initialize`, the hooks are armed and the commit-msg hook refuses a forbidden trailer, `brief`/`next` answer, the freeze ratchet holds and the suite is green in a detached tree — and `ddflow onboard status` is the standing drift report you can re-run any day. Prove what it cannot:

- **The MCP server starts from the entry that was registered** — `ddflow onboard verify` spawns exactly that command with exactly that environment and completes an `initialize` handshake; a tool call (`ddflow_next`) answers from this project's queue.
- `ddflow_hooks` (`action: status`): pre-commit, commit-msg and the SessionStart hook installed; if a trailer is forbidden, feed the commit-msg hook a message carrying it and see it refused.
- `ddflow_brief` and `ddflow_next` offer the work the team would start with; `ddflow_doctor` is healthy; `ddflow_import_verify` has nothing left owed that you did not report.
- The whole suite passes with the gate's own command, including the freeze ratchet.
- Commit the cutover on the default branch with explicit paths — `.ddflow/config.toml`, `.ddflow/.gitignore`, your own `.ddflow/events/<agent>.jsonl`, and nothing machine-local — the rulebook, the hooks' config, the ratchet, the MCP config — never `git add -A`. Push only if the operator says so.

## Report

End with what the operator can check in one read: what was cleaned up; what the queue now holds and what it offers first; what was imported, dropped, held and filed by hand, and why; which rules moved into config; what is frozen and how; what could not be done and why (recorded, not glossed); and every ddflow defect you hit, filed with `ddflow_bug_found`.
