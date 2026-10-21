# ddflow: collapse git history, and extract/regenerate from records

Research, read-only, 2026-10-18. Measured on this repo (worktree of main 4842cbd1). Marked UNVERIFIED where not checked.

## 0. Key finding

D-log-in-main-tree puts the event log in the main tree as plain files. Shards are append-only, so the TIP tree already contains every event ever written. Squashing git history therefore loses no event. It loses three other things:

1. git-level history of the code and of the log (when each line was added, which is also in every event's `ts` and `lamport`).
2. Resolvability and ancestry of every recorded sha.
3. Other clones' ref positions, plus branches and worktrees based on old commits.

The design problem is item 2 and 3, not "preserving the log". The event log needs no compaction to survive a collapse. Compaction is a separate, optional size feature.

Measured size of this project:

| Item | Value |
|---|---|
| Commits on HEAD | 6773 |
| Commits touching `.ddflow/events` | 1353 |
| Event shards | 385 files, 57 MB, 78,266 events, 58 kinds |
| Blobs | 664 KB, 50-odd git directories under `.ddflow/blobs/` |
| `.git` | 208 MB |
| Archive refs already present | `refs/archive/pre-trailer-strip/*`, `refs/archive/pre-trailer-strip-2/*` |

Event volume by kind (count, then payload size):

| Kind | Count | Size |
|---|---|---|
| `lease.renewed` | 31,043 | 7.4 MB |
| `gate.passed` | 10,165 | 14.4 MB |
| `gate.failed` | 2,902 | 14.5 MB |
| `task.updated` | 6,517 | — |
| `review.triaged` | 5,030 | — |

Kinds that carry 40-hex shas inside the payload:

| Kind | Events with a sha |
|---|---|
| `worktree.merged` | 915 (all) |
| `ci.result` | 688 (all) |
| `item.completed` | 778 |
| `bug.fixed` | 143 |
| `gate.passed` | 2,933 |
| `gate.failed` | 1,971 |
| `release.tagged` | 1 |

## 1. What ddflow has today (do not duplicate)

| Capability | Where | State |
|---|---|---|
| `ddflow replay [--out DIR] [--verify]` | `ddflow/api/reporting/views.py:145`, `services/sessions.py` (`replay`, `verify`, `bundle`) | Writes RECONSTRUCTION.md, QUEUE.md, LESSONS.md. README "Reconstruction from logs alone" says it reproduces decisions, not bytes. `--verify` only does `git cat-file -e <merged_sha>^{commit}` per item and reports unresolved shas as "rebased, squashed, or a different repository". It does NOT compare folded state. |
| `ddflow rebuild` | `views.py:22`, `infra/store.py:314` | Re-derives `index.db` (gitignored, machine-local) from the log. |
| `ddflow export <doc>` | `services/export*`, `templates/events/exports.toml` | Renders DOCUMENTS (changelog, bugs, QUEUE.md, LESSONS.md, README regions) from the fold into repo files. Not a data export. DX-export-committed-log (open) notes staged exports are still checked against the live log. |
| `ddflow import` | importer, `[importer] max_tasks` | Imports markdown/checklist task lists as proposed tasks. Not a ddflow-bundle import. `--include-done` exists; "a finished history is not a queue". |
| backup | `services/backups.py` | Only upgrade/adopt file backups in `.ddflow/backups/`, local, gitignored. There is NO `ddflow backup` command (argparse rejects it). |
| B-gap-log-backup (open) | task | Doctor notes for unpushed shards, `doctor --restore-drill`, README bundle recipe, "no new command". Overlaps (B) partly. |
| B-logscale-blob-backup (open) | task | Backup/export/import must carry `.ddflow/blobs`; a gc of unreferenced blobs by explicit command. Prerequisite for any bundle. |
| B-gap-log-scale (done) | task | Blobs for gate evidence (`services/logblobs.py`, `core/blobref.py`, `infra/blobstore.py`), lease-renewal coalescing, read snapshot. The "folded-State checkpoint at a lamport mark" part was specified; whether it shipped is UNVERIFIED (renewed is still 40% of events). |
| B-log-snapshot-knobs (done) | task | `[log] snapshot`, `snapshot_min_events` etc. The snapshot stores PARSED events, not folded state. It is a derived cache. |
| SE-event-registry (done) | `ddflow/core/eventkinds.py`, `ddflow/templates/events/*.toml` | Per-kind `retention = keep|compact` (default keep) and payload `v`. |
| SE-event-registry.retention (open) | task, globs only `core/eventkinds.py` | "Retention is declared but nothing reads it." A grep of the templates found no kind declared `compact`. Compaction is not implemented. |
| Rewrite precedent | D-strip-coauthor-history-r2 | One-off, operator-run: message-only rewrite of 115 of 220 commits, archive refs `refs/archive/pre-trailer-strip/*` (pushed) so event-log shas still resolve. The decision records the cost: 297 event records across 55 items cite pre-rewrite shas that resolve via the archive but are "no longer ancestors of main (ancestry checks such as version cut will not find them)". Amended: "NOT a precedent"; rewrites and force-pushes only when the operator says so (D-push-operator-says). |
| Event format | `.ddflow/events/<agent>.jsonl` | One shard per agent. Event keys: `agent data id kind lamport schema subject ts`. `.gitattributes`: `.ddflow/events/*.jsonl merge=union`. |
| Lesson on merge=union | lesson "A 'last line is the max' shortcut only holds while a file has exactly one writer" | A collapse that combines shards from several clones must not rely on tail-reading. |
| Remote claims | `ddflow/infra/claimref.py`, `refs/ddflow/claims/<id>` | Each claim pushes a unique empty-tree commit. Independent of history, but a collapse that pushes with `--mirror` would touch them. |
| Gitignore | `.ddflow/.gitignore` | `index.db*`, `events.lock`, `local/`, `worktrees/`, `runs/` are not committed. `blobs/` IS committed. |
| `ddflow cleanup [--apply]` | | Worktree/branch cleanup. Not history. |
| Sensitivity | SE-sensitivity (.1 .2 .3 done), `ddflow/templates/sensitivity.toml`, `services/egress.py` | Levels public/internal/restricted/confidential with `to_model`; write-time scan; egress filter at search/recall/export/brief. SE-sensitivity-doors (open) labels the other record families and wires export and hub publish. Redaction at write (`core/redact.py`, D-unify item 6) already scrubs secrets, hosts, home paths in the committed log. |
| Knowledge packs | LP-format, LP-seed, LP-install, LP-contribute (all open) | A lessons/rules pack format and install into a fresh project. This is the nearest existing design for "seed a new project with records". |
| Spec import/export | SDD-import, SDD-export-tasks, SDD-acceptance, PL-slice, PL-native-export (open) | Spec Kit/OpenSpec/Kiro task lists. Relevant to (B) output format. |

Which commands read recorded shas (the collapse blast radius):

| Reader | Behaviour on an unresolved sha |
|---|---|
| `session.verify` | Reports a problem per item. |
| `services/gates/evidence.py` `superseded_by`, `moved_past`, `_is_ancestor` | `merge-base --is-ancestor`. Unknown commit returns None, so "the run keeps its record". Not fatal. |
| `is_tree` | Compares a recorded `tree_sha` (`<head12>+clean` or `<head12>+st:<manifest>`) with the current tree. After a collapse an old gate "ran on tree X" can still be matched only if the squash tip tree equals that tree. `+clean` evidence for the old tip would match the new tip's tree only via the tree-hash. UNVERIFIED how `is_tree` resolves `head12`. |
| version cut, `release.tagged` | Ancestry check; the D-strip decision says it fails after a rewrite. |
| `worktree.merged` fields `branch_head`, `landed_before`, `landed_after`, `sha` | Used for recover/cleanup/onboarding ("merge-base --is-ancestor is the only honest merged"). |

## 2. Git mechanics and risks

Sources. I could not fetch the pages in this session; they are cited from memory of the standard git documentation and marked UNVERIFIED for exact wording. I ran local experiments on git 2.43.0 (below).

- git-commit-tree(1), git-update-ref(1), git-replace(1), git-bundle(1), git-notes(1), git-gc(1) and gitglossary: "reachable".
- gitrevisions(7), gitformat-bundle(5), and the shallow section of git-clone(1) and git-fetch(1).
- git-filter-repo manual, sections "COMMIT MESSAGES", `--commit-callback`, `--refs`, and the `commit-map` / `ref-map` output files.
- https://git-scm.com/docs and https://github.com/newren/git-filter-repo/blob/main/Documentation/git-filter-repo.txt (UNVERIFIED, not fetched).

### 2.1 Ways to collapse

| Method | Mechanism | Keeps old shas resolvable | Old ancestry |
|---|---|---|---|
| M1 Orphan snapshot: `git commit-tree <tip^{tree}> -m ...` (no parent), then move the branch | A single new root | Only if something still points at the old tip (archive ref, tag, bundle), or until gc | Lost |
| M2 Squash with a parent: `git commit-tree tree -p <base>` per range | One commit per range/phase; the older base stays real history | Old shas below the base stay ancestors; those inside the range do not | Partial |
| M3 `git filter-repo` (`--commit-callback`, `--refs`, `--prune-empty`, `--commit-map`): rewrite everything | Emits a `commit-map` (old to new), which we need for the alias map | Not by default: filter-repo deletes the old objects and strips remotes unless `--force`. We would back up first | Rewritten |
| M4 `git rebase -i`/`--root` squash | The same as M2 interactively; not scriptable under `-i` rules | — | — |
| M5 Keep everything, only hide with `git replace --graft` | Local-only "view" objects in `refs/replace/*` | Objects stay | Faked on this clone only |
| M6 Shallow (`clone --depth N`) or `git/info/grafts` | Cuts per clone, not at the origin | Remote has all | Cut |

Keeping the old history reachable:

| Mechanism | Property |
|---|---|
| `refs/archive/<name>/...` | Used already by D-strip. Reachable so never gc'd, but NOT fetched by a default `git clone`/`git fetch` (default refspec is heads and tags). |
| Tag annotated `archive/pre-collapse-<date>` | Fetched by default (tags are auto-followed when reachable from a fetched ref only; a tag pointing at an unrelated old tip is NOT auto-followed after an orphan collapse because the tip is no longer reachable from main). Needs `--tags` or an explicit refspec. UNVERIFIED for all hosting variations. |
| Orphan branch `ddflow/archive` holding the old tip | Fetched by default, but it is real history that makes clones as big as before; it defeats size reduction. |
| `git bundle create --all` of the pre-collapse repo | One file, offline, `git bundle verify`, clonable. Best as the "kept off-box archive". |
| `refs/notes/ddflow-alias` (git-notes) | Attach old-sha to new-sha to the NEW commits. Notes refs are not fetched by default either. |
| Signed tags | A signed commit/tag signature covers the old object. A rewrite creates new objects whose signatures do not exist; old signed objects survive only in the archive. |

### 2.2 Local experiment (git 2.43.0, 4-commit repo)

Run in a throwaway directory, nothing in the ddflow repo touched.

| Step | Result |
|---|---|
| `git commit-tree HEAD^{tree}` with no parent, reset branch to it, archive ref at the old tip | The old sha: `git cat-file -e` yes. `merge-base --is-ancestor old new`: **no**. This is exactly the D-strip "resolves but is not an ancestor" case. |
| `git replace --graft NEW OLDTIP` | `merge-base --is-ancestor old new` becomes **yes** on this clone. `git log` shows 5 commits. |
| `git clone file://...` | The clone has only `refs/heads/main` and `origin/*`. **No `refs/archive/*`, no `refs/replace/*`.** So a default clone neither resolves old shas nor sees the graft. |

Conclusions: an archive ref gives resolvability only on clones that fetch it explicitly. A replace-graft gives ancestry but must be fetched with `+refs/replace/*:refs/replace/*`, and `GIT_NO_REPLACE_OBJECTS` or `--no-replace-objects` disables it. Grafted ancestry also defeats the purpose of shrinking the tip clone (the history is fetched anyway).

### 2.3 What breaks (checklist)

1. Every recorded sha in the log (see the sha table). Resolvable only via archive; not ancestors.
2. Gate evidence `tree_sha` = `<head12>+clean|+st:<manifest>`: the head12 names an old commit. The `st:` content id is independent of history and still matches a tree with the same content, so evidence for the tip tree keeps matching IF the new tip's tree is byte-identical (a collapse keeps the tip tree exactly). Evidence for older trees can only be judged "unknown". Superseded/moved-past logic returns None/False, so no fail-closed errors, but "ran on an ancestor" reasoning disappears.
3. `worktree.merged` / `item.completed` shas: informational after a collapse; `recover`/`cleanup`/onboarding ancestry questions about OLD branches answer "not merged".
4. Other clones: their `main` diverges. They must reset onto the new history; their local shards (uncommitted/unpushed) must be merged into the log BEFORE the collapse or they are lost on reset.
5. Open worktrees and `ddflow/<id>` branches: based on old commits, would re-merge ancient history. Must be merged or abandoned first.
6. Open PRs and GitHub PR/commit links: PR heads rebase; GitHub keeps old commit objects reachable through PR refs (`refs/pull/*`) and keeps `/commit/<sha>` working for a while but unreferenced objects can be GC'd (UNVERIFIED). Links in README/CHANGELOG to commits break.
7. Tags/releases: `release.tagged` (`0.2.2`) must be re-pointed or left on archive history. Release tags that point at old objects remain reachable by themselves, which silently retains the full old history. Decide per tag.
8. Remote claim refs `refs/ddflow/claims/*`: empty-tree commits with no parent history, unaffected, but a `--mirror` push or `--prune` would delete or overwrite them. Force pushes must be per-ref.
9. Hooks: the commit-msg/precommit hooks do not depend on history. Lease/worktree state in `.ddflow/local` is machine-local (gitignored); a worktree list refers to commits and breaks if they vanish (git worktree prune).
10. `merge=union` shards: a collapse that is done on one clone while another clone appends to a shard yields a union merge later. That works for plain appends (set semantics on identical lines), but event ids and lamport values of the two sides must not collide; they do not by design (event ids are content addresses). The tail-read shortcut lesson means: after a union, rebuild the high-water Lamport from the whole shard.
11. Signed commits/tags: lose signatures.
12. gc: archive refs make the old objects permanent; without them `git gc --prune=now` would delete them and `session.verify` would report every item.

## 3. Design of collapse: `ddflow history collapse`

Name note: `ddflow history` already exists (it prints events). Use a subcommand family under a new noun to avoid a clash, e.g. `ddflow archive collapse|plan|verify|restore`, or `ddflow history collapse` with `--kind collapse` dispatch. UNVERIFIED which one the CLI parser tolerates; a positional first argument `collapse` on `history` conflicts with its existing flag-only parser. Prefer `ddflow collapse ...`.

### 3.1 Principles

- Operator-only, like `chore approve`; agents may run only `plan`. Refused for an agent identity and over MCP (D-push-operator-says: rewrites happen only when the operator says so).
- Nothing is pushed (D-fallback-no-remote, D-push-operator-says). The command prints the exact `git push --force-with-lease` and per-ref archive push lines for the operator to run.
- Everything is local: archive bundle first, then rewrite. The rewrite is a new branch name, switched only after `verify` passes.
- Atomic and resumable: a plan file in `.ddflow/local/collapse/<plan-id>/` lists steps; each step is idempotent.

### 3.2 Command family

```
ddflow collapse plan [--base REF] [--range A..B | --before DATE | --phase P ...]
                     [--events-only] [--keep-last N] [--archive bundle|ref|branch]
                     [--tag-policy keep|retarget|archive]
ddflow collapse apply  <plan-id> [--archive-dir DIR] [--yes]   # operator only
ddflow collapse verify [<plan-id>]                              # proves equivalence
ddflow collapse aliases [--sha OLD]                             # resolve old -> new
ddflow collapse restore <plan-id>                               # back to the archive tip
```

`plan` is a dry run and reports:

- Commits to be replaced, how many touch code vs `.ddflow/events` vs docs/exports, size effect (`git count-objects`).
- Recorded shas that will stop being ancestors: counted from the log, by event kind (the 6 kinds above).
- Blockers: dirty primary tree; unmerged `ddflow/*` branches; live worktrees; active leases (fold says leased items); `lease.*` events newer than N minutes from other agents; open PRs (needs forge; unavailable => refuse, never pass); other clones' unpushed shards (UNVERIFIED how to know; use `refs/remotes/origin/*` vs local and `doctor`'s B-gap-log-backup note); tags pointing into the range; a `refs/ddflow/claims/*` holder.
- A refusal per blocker with the existing exit-code contract: 3 refused, 2 could not run.

Forms:

| Form | What it does | Risk |
|---|---|---|
| F1 Full | One root commit with the tip tree; whole old history to the archive | Highest churn, every clone must reset |
| F2 By range/phase | Squash `A..B` into one commit per phase, optionally per item (the `item.completed` ledger gives each item's merge commit and file list, so each phase is a natural squash unit) | Medium; ancestry kept for everything outside the ranges |
| F3 Events-only | Rewrites only commits whose diff is entirely under `.ddflow/events` or `.ddflow/blobs` (1353 of 6773 here, about 20 percent) into one "events: through <lamport>" commit per N days, leaving code commits untouched | Low-gain but safe: touches no code sha. Messages like "events: complete X" are exactly these commits. UNVERIFIED: how many have tests or docs mixed in |
| F4 Keep-last-N | Squash everything before the last N merge commits | Same as F2 with an implicit range |

Note F3: event-only commits are interleaved with code commits in a linear history, so squashing them means reordering relative to code commits. The shard files are cumulative and independent of the code, so it is safe in principle, but the resulting commits are NOT in original time order. Needs a tree-equality check on every code commit's `.ddflow/` content (dropping the event commits then re-adding their cumulative effect at the next code commit). This is the hardest form; do it last.

### 3.3 Archive (keeping the old history)

Default to all three, all local until the operator pushes:

1. `git bundle create <dir>/<project>-pre-collapse-<date>.bundle --all`, then `git bundle verify`.
2. `refs/archive/pre-collapse-<plan-id>/...` for heads, mirroring D-strip-coauthor-history-r2, so existing doctor/verify knowledge applies.
3. An annotated tag `archive/pre-collapse-<plan-id>` on the old tip (carries the plan summary in its message).

Optional: an orphan branch `ddflow-archive` for a hosted copy; costs full clone size.

### 3.4 Sha alias map as a log event

Add event kind `history.rewritten` (retention keep), payload:

```
{ plan_id, form, old_tip, new_tip, archive: {bundle, ref, tag},
  map_blob: "<sha256 of commit-map json blob>",   # old->new, can be 10^3-10^4 entries
  kept_ranges, event_log_digest_before, event_log_digest_after }
```

The old->new map goes into `.ddflow/blobs/` (content addressed, already supported by `core/blobref.py`, `infra/blobstore.py`), not inline. For squashed commits many old shas map to ONE new sha, so the map is many-to-one with the original subjects kept for traceability.

- The log is append-only: old events keep their old shas verbatim. Readers resolve through the alias map: `resolve_sha(old)` returns old if it resolves AND is an ancestor, else the mapped new sha, else "archived (bundle X)".
- `session.verify`, `superseded_by`, `moved_past`, version cut and `recover` call a shared `resolve_sha`. Then "did not resolve" becomes "resolved via alias" instead of a problem. A task: SE-style registry of sha readers so none is missed.
- Tamper evidence is unaffected: events are not rewritten.
- Needs an upcaster/version stamp and the skew guard (D-upgrade-skew-guard) because an older ddflow cannot read the new kind. Unknown-kind behaviour of the fold: UNVERIFIED.

### 3.5 Event-log side

Two independent features; only the first is required by collapse.

1. Equivalence proof, `ddflow collapse verify`: fold the log before (from the archive tip's `.ddflow/`) and after (from the new tip's), compare a canonical digest of the State (items, states, decisions, lessons, research, bugs, rules, memories, leases ignoring time, sha-bearing fields masked through the alias map). The digest function does not exist today; `replay --verify` only checks sha resolution. Add `ddflow replay --verify-state <digest|archive-ref>`. The byte digest of `.ddflow/events/*.jsonl` before and after must be IDENTICAL for F1/F2 (tip tree unchanged) and F3 (shards unchanged). That is a trivial and strong check: `git diff --stat <old_tip> <new_tip> -- .ddflow` empty.
2. Log compaction, `ddflow log compact` (optional, later): SE-event-registry.retention. Policies:
   - Drop `lease.renewed` older than the last renewal of each lease (31,043 events, 7.4 MB, none sha-bearing); fold-equivalent only if lease windows computed by core/handlers/leases.py (`_late_renewal`) come out the same; the B-gap-log-scale note warns this needs a two-clone proof.
   - Replace `gate.started` and superseded `gate.failed` bodies by their blob stubs.
   - Rewrites shards, so event ids (content addresses) stay but files change; tamper-evidence is broken for dropped events unless a `log.compacted` event records the dropped id range and digest. Move originals to `.ddflow/archive/<date>/*.jsonl.zst` (gitignored but in the bundle), never delete.
   - Prove with a replay-equivalence harness: fold(original) == fold(compacted) for status, next, board, brief on the real log copy. The harness is already specified in B-gap-log-scale HOW TO TEST.
   - Do not do this first; collapse works without it.

### 3.6 "Choose what history ddflow keeps"

A `[history]` config section (everything configurable per D-everything-configurable): `archive = bundle,ref,tag`, `bundle_dir`, `keep_kinds`, `compact_kinds` (default from the registry), `require_clean_clones = true`, `tag_policy`. Per-kind retention comes from the existing registry.

## 4. Design of extract and regenerate

### 4.1 What "regenerate" honestly means

The records hold intent, not code. From the measured payloads:

| Source | Fields present | Reproducible mechanically? |
|---|---|---|
| `task.added` | title, body (2276 of 2414), globs, needs, parent, priority, tags, fixes, source | Yes: queue, dependencies, globs, phases. Acceptance is free text inside `body` ("ACCEPTANCE:", "HOW TO TEST:"), not a structured field. |
| `decision.recorded` (249) | context, decision, alternatives, consequences, governs globs, status, decided_by, supersedes | Yes as records; they constrain the re-implementation. |
| `lesson.recorded` (74), rules (`.ddflow/rules`, `def.recorded`) | rule, why, how, tags | Yes. Rules/lessons transfer as accepted records. |
| `research.recorded` (937) | question, verdict, claim, mechanism, probe, probe_output, falsifier, sources | Yes as records. These are the strongest "oracle" content: experiments that can be re-run. |
| `gate.passed` evidence | `diff_stat`, `tree_sha`, test names, `ci.result` check lists | Only METADATA: which tests passed, file-change counts. No diffs, no source. |
| `item.completed.ledger` | files changed, tests among them | The file list per item. This is the mapping from intent to code locations. |
| `session.prompt` (983) | operator prompts verbatim | Yes. Great for intent; also the most sensitive. |
| `worktree.merged` | shas | Useless without the repository. |
| Source code | not in the log | NO. Not recoverable from records. The only exception: small diffs or snippets embedded in review findings or bug reproductions. |

Therefore:

- Mechanical: queue (phases/tasks/needs/globs), decisions, rules, lessons, research, memories, docs rendered from the log (QUEUE.md, LESSONS.md, README regions via `export`), config (`.ddflow/config.toml`, `pins.toml`), the hooks (`ddflow upgrade`/`setup`).
- Requires agents: every task's implementation, run in dependency order, each to be judged by its acceptance. This is a new implementation guided by specs, NOT a rebuild. Output is not byte-equal and not behaviour-identical, because the original was itself iterated through bug fixes, review rounds and design changes the specs only partly capture (the log records them: `bug.found`, `bug.fixed` with regression test names).
- Missing for it to work:
  1. Structured acceptance criteria and test oracles. Today acceptance is prose in `body`. Needs a field or a convention parser (UNVERIFIED how reliable; sample of 2276 bodies not parsed here).
  2. The tests themselves. `gate.passed` evidence names tests but the test code is in git, not the log. Without the original tests/fixtures/golden files the "oracle" is gone. Option: the bundle optionally carries the tests directory and golden fixtures from the tip tree (an explicit "oracle pack"). Then the new project is "reimplement against the old tests", a much stronger and honest notion.
  3. Superseded vs current records: replay of 249 decisions includes many superseded; seeding must carry status.
  4. Cross-references to files that no longer exist in the new project (globs, `governs`), so a path mapping or "stack-neutral" mode is needed for a different language.
  5. A bounded context for agents: 78k events do not fit a prompt; the seed must select by `needs` closure and budget (`recall` and `brief` already do ranking).
  6. Task state is derived by order of events; "reopen all" means ignoring `item.completed`, `gate.*`, `lease.*`, `worktree.*`, `ci.result`, `release.tagged` (process history, which does not transfer and carries shas).

### 4.2 Bundle format

A single directory or `.tar.zst` (`ddflow-seed@1`), self-contained, hash-addressed manifest:

```
manifest.json        schema, ddflow version, source project, lamport range, digest of every file,
                     sensitivity ceiling used, redaction profile, counts per kind
records/             JSONL, one file per family: phases, tasks, decisions, rules, lessons,
                     research, memories, bugs (+ regression test names), defs, links
history/             optional: the full raw shards (for faithful clone) and referenced blobs
docs/                rendered QUEUE.md, LESSONS.md, RESEARCH.md, decisions/*.md, README skeleton
spec/                per task: title, body, acceptance (extracted), globs, needs
oracle/ (optional)   tests, fixtures, golden snapshots from the chosen tree, with a manifest
config/              .ddflow/config.toml, pins, rules
```

Two modes:

- `--mode records` (portable seed): records re-stated as FRESH events in a new log (new ids, `lamport` renumbered), tasks reopened, decisions/rules/lessons/research imported as accepted records with `provenance: seeded-from <manifest digest>`. No process events, no shas. This needs a new import path; the existing KB-layer design (read-only imported layer, ranked below project operator records, never in project shards) is the right place for lessons/research/decisions that must not masquerade as the new project's own history. Tasks go in as proposed/open per PL-proposed-intake.
- `--mode faithful` (clone with history): copy raw shards and blobs verbatim. For a fork or a disaster-recovery copy. Same ids, shas stale.

### 4.3 Sensitivity and redaction

- Reuse SE-sensitivity: each record has a label (SE-sensitivity.2 done for some families; SE-sensitivity-doors open for the rest). Bundle takes `--max-sensitivity public|internal|restricted` (default `internal`), records above the ceiling are DROPPED (not masked) with a count in the manifest, and a second scan with `core/redact.py` runs at export (belt and braces, since the log was redacted at write only for new writes since D-unify 6; older events may hold unredacted hosts or paths: UNVERIFIED).
- `session.prompt` is verbatim operator text: excluded by default, opt-in `--include-prompts`.
- Egress: a new door `export-bundle` in `services/egress.py`.
- Review gate: run the existing hazard scan (SE-hazard-scan, open) on the produced bundle before it is used to seed a model (imported text is data, not instructions: ddflow already wraps records in `<ddflow-record trust=...>`).

### 4.4 Command design

```
ddflow extract plan   [--max-sensitivity L] [--mode records|faithful] [--oracle PATH...]
                      [--phase P ...] [--include-prompts] [--include-closed-tasks]
ddflow extract write  --out seed.tar.zst            # operator-run, local
ddflow extract verify seed.tar.zst                  # digests, schema, scan, round-trip
ddflow seed  plan  seed.tar.zst [--into DIR]        # dry run
ddflow seed  apply seed.tar.zst --into DIR          # creates repo: git init, ddflow init,
                                                    #   config, imported records, reopened queue
ddflow seed  work  [--stack python|...] [--oracle-first]
```

`seed apply` steps:
1. `git init`, `ddflow init`/`setup` (hooks, config).
2. Import records through the normal door (events, redaction, sensitivity), tag provenance.
3. Create phases/tasks with `needs` and priorities, all open. A new pipeline variant: for a seeded task the first gate is "oracle": it must run the seeded acceptance tests and the implementation must make them pass.
4. Render docs. Commit once.

Agents then work the normal loop (`next` → `claim` → gates → `merge` → `complete`). The new log is a fresh history; it can carry `seeded_from` links per task.

Idea fit: this is the "export / extract" from the operator's request, and overlaps LP (packs: lessons/rules only), SDD (spec task lists), PL-slice (roadmap slice export). Reuse their formats rather than a fourth.

## 5. Honest limits

- The log is not a source repository. "Regenerate ignoring the original source" produces a new implementation steered by specs and decisions; it cannot reproduce behaviour that no record or test captures. README already says "reproduces the decisions, not the bytes".
- Without the oracle pack (tests/fixtures), correctness of a regenerated project cannot be judged by anything but the new gates the agents themselves write. Say so in the manifest.
- A collapse cannot make old shas ancestors again on a default clone; archive refs and bundles exist only where somebody fetched them.
- Compaction of `lease.renewed` cannot be proved safe without the two-clone test already demanded by B-gap-log-scale.
- Records do not capture why code looks as it does line by line; research and decisions cover the main design points only.
- Sizes: 78k events and 57 MB do not fit an agent context; seeds must be budgeted and ordered by `needs`.
- Not verified: behaviour on a hosted forge (GitHub PR refs, force-push protection, unreferenced-object retention); multi-clone scenarios; the cost to fold a mixed old/new log with `history.rewritten`; whether an older ddflow rejects an unknown kind.

## 6. Ranked options

Ranking by value over risk, then feasibility today.

| # | Option | Feasible now? | Cost | Risk |
|---|---|---|---|---|
| 1 | `ddflow extract` / `seed` in records mode (B), with sensitivity ceiling, no history rewrite | Needs prerequisites: B-logscale-blob-backup (blobs), SE-sensitivity-doors (labels on all families), KB-layer or an import door, a structured acceptance field | M-L | Low: read-only on the source project, new project is separate |
| 2 | `collapse plan` (dry run only) + `resolve_sha` alias reader + `history.rewritten` event, no apply | Feasible now (new kind with upcaster, a read-only planner) | S-M | Low: nothing rewritten |
| 3 | `collapse apply` F2 by phase/range with bundle + archive ref + tag, operator-only | Needs 2 and the blocker checks | M | Medium: force-push of all clones; one-off; bundle mitigates |
| 4 | `collapse apply` F1 full single-commit | Same as 3 | S beyond 3 | Highest churn, loses ancestry entirely |
| 5 | Archive-only hygiene: `ddflow archive bundle` writes a verified `git bundle` of the repo + blobs (also satisfies B-gap-log-backup recipe and the restore drill) | Feasible now | S | Very low; value even without any rewrite |
| 6 | Events-only squash F3 | Needs the order-safety analysis | M | Medium: gain about 20 percent of commits, no code touched |
| 7 | Event-log compaction by retention | Needs SE-event-registry.retention, snapshot of folded State, two-clone test | L | Medium: fold-equivalence proof must precede |
| 8 | Faithful clone of the raw log with `--mode faithful` | Feasible | S | Low |

Always do 5 and 2 before 3 and 4.

## 7. Rejected ideas

- Rewriting event shards to remap shas in place. It breaks the content-addressed ids and tamper evidence; the log is append-only and the alias map keeps old shas as written.
- Moving the log to an orphan ref or separate branch to "keep it out of the squash". Contradicts D-log-in-main-tree and breaks `merge=union` flows; unnecessary because the tip tree holds the whole log.
- Relying on `git replace --graft` as the preservation mechanism. Local to each clone and not fetched by default (confirmed in the experiment); it also keeps full history in every clone.
- Relying on an archive ref alone. Not fetched by default; D-strip's own consequences show ancestry loss.
- `git filter-repo` on the live repo with no backup. It removes the origin remote and old objects by default (UNVERIFIED in this git-filter-repo version); only acceptable after the bundle.
- Auto-collapse by a cadence or hook. Violates D-push-operator-says and the operator-only principle.
- Regenerating source from `session.prompt` replay. Model outputs are not deterministic (README); a prompts-only seed is not a spec.
- A fourth export format. Reuse LP packs, SDD/Spec Kit tasks.md, PL-slice.
- Deleting old blobs or shards during collapse. B-logscale-blob-backup says gc only by explicit command.

## 8. Proposed phase and task list (not filed)

Phase `P-history-collapse` (A), phase `P-seed-extract` (B). Priority: the operator's choice; defaults below follow ranking above.

| Id | Title | Needs | Globs |
|---|---|---|---|
| HC-decide | Operator decisions on collapse: forms, archive default, tag policy, who may apply, surfaces (CLI only, not MCP) | none | docs/ddflow/decisions/history-collapse.md |
| HC-bundle | `ddflow archive bundle`: verified `git bundle` of repo + `.ddflow/blobs`, `bundle verify`, restore drill hook (absorbs the README recipe of B-gap-log-backup) | B-logscale-blob-backup | ddflow/services/archive.py, ddflow/api/archive.py, tests/test_archive_bundle.py |
| HC-sha-readers | One `resolve_sha` and an inventory ratchet of every code path that reads a recorded sha (`session.verify`, `evidence.superseded_by`/`moved_past`/`is_tree`, version cut, recover, cleanup) | HC-decide | ddflow/services/shaalias.py, ddflow/services/sessions.py, ddflow/services/gates/evidence.py, tests/test_sha_readers.py |
| HC-event-kind | `history.rewritten` event kind with upcaster, registry entry, blob map, skew-guard note | SE-event-registry, HC-sha-readers | ddflow/templates/events/history.toml, ddflow/core/upcasters.py, ddflow/core/handlers/, tests/fixtures/event_kinds.json |
| HC-plan | `ddflow collapse plan`: ranges/phases from the `item.completed` ledger, counts of commits and sha-bearing events, blocker list (worktrees, leases, branches, tags, forge unavailable = refuse), no writes | HC-event-kind | ddflow/services/collapse_plan.py, ddflow/api/collapse.py, tests/test_collapse_plan.py |
| HC-state-digest | Canonical folded-State digest and `ddflow replay --verify-state`, masked through the alias map; real-log golden test | HC-sha-readers | ddflow/services/statedigest.py, ddflow/api/reporting/views.py, tests/test_state_digest.py |
| HC-apply | `ddflow collapse apply` for F1/F2: bundle first, archive ref + tag, `commit-tree` rewrite on a new branch, `history.rewritten`, operator-only, no push, prints force-push lines; `collapse restore` | HC-plan, HC-bundle, HC-state-digest | ddflow/services/collapse_apply.py, ddflow/surfaces/declared/, tests/test_collapse_apply.py |
| HC-verify | `ddflow collapse verify`: `.ddflow/` tree identical, state digest equal, every cited sha resolves or is aliased | HC-apply | ddflow/services/collapse_verify.py, tests/test_collapse_verify.py |
| HC-clones | Pre-collapse check that other clones' shards are merged (unpushed shard doctor note) and post-collapse reset instructions in `brief` | HC-plan, B-gap-log-backup | ddflow/services/doctor*.py, tests/test_collapse_clones.py |
| HC-events-only | Form F3: squash event-only commits with order-safety proof | HC-verify | ddflow/services/collapse_events.py, tests/test_collapse_events_only.py |
| HC-compact | Log compaction by registry retention with archive shards and `log.compacted` event; replay-equivalence harness | SE-event-registry.retention, HC-state-digest | ddflow/services/logcompact.py, ddflow/core/eventkinds.py, tests/test_log_compact.py |
| HC-config | `[history]` section: archive kinds, dir, tag policy, clean-clone requirement; upgrade templates | HC-decide | ddflow/config_sections/history.py, ddflow/templates/upgrade/unreleased/ |
| SX-decide | Operator decisions on extract: modes, sensitivity ceiling, prompts in or out, oracle pack, bundle format | none | docs/ddflow/decisions/seed-extract.md |
| SX-acceptance-field | Structured acceptance and test-oracle fields on tasks, with a parser/ratchet that counts bodies lacking them | SX-decide | ddflow/core/kinddata.py, ddflow/templates/kinds/, tests/test_task_acceptance.py |
| SX-egress-door | Add `extract` as a door in egress, finish SE-sensitivity-doors for all families, drop above ceiling | SE-sensitivity-doors | ddflow/services/egress.py, tests/test_extract_egress.py |
| SX-bundle | `ddflow extract plan|write|verify`: manifest, digests, records JSONL, rendered docs, blobs, optional oracle pack | B-logscale-blob-backup, SX-egress-door, SX-acceptance-field | ddflow/services/extract.py, ddflow/api/extract.py, tests/test_extract_bundle.py |
| SX-seed | `ddflow seed plan|apply`: new repo, init, import records with provenance, reopened tasks, imported layer for lessons/research/decisions | SX-bundle, KB-layer | ddflow/services/seed.py, ddflow/api/seed.py, tests/test_seed_apply.py |
| SX-oracle-gate | A gate/pipeline variant for seeded tasks that runs the oracle tests first | SX-seed | ddflow/templates/pipelines/, ddflow/services/gates/, tests/test_seed_oracle_gate.py |
| SX-faithful | `--mode faithful` verbatim shard+blob clone with verification | SX-bundle | ddflow/services/extract.py, tests/test_extract_faithful.py |
| SX-acceptance | End-to-end: extract a small fixture project, seed an empty dir, an agent fake re-implements from the oracle, doctor clean, no secret in the bundle | SX-oracle-gate, SX-seed | tests/test_seed_acceptance.py |

Dependency note: `B-logscale-blob-backup` currently needs `B-gap-log-backup`, which exists (open). B-gap-log-backup should not be done by a separate command; HC-bundle can absorb its README recipe.

## 9. Operator decisions needed

1. Is the goal smaller git history, a smaller clone, or a tidier history? That decides F1 vs F2 vs F3 vs "archive bundle only".
2. Which archive survives: off-box bundle only, `refs/archive/*` pushed, tag, or orphan branch? What about release tags?
3. Which forms may be run (full single commit, per-phase, events-only), and may anything but the operator run `apply`?
4. Are other clones and open PRs a hard refusal (recommended) or a warning?
5. For extract: ceiling for sensitivity, whether operator prompts are included, whether the oracle pack (tests/fixtures from the tip tree) is part of the bundle, and whether the seeded decisions/lessons live in the imported layer or become the new project's own records.
6. Does `regenerate` mean "new implementation against old tests" (recommended) or "specs only"?

## 10. UNVERIFIED list

- Git docs and git-filter-repo wording: cited from memory, not fetched; only the local experiments on git 2.43.0 were run.
- GitHub behaviour: PR refs, unreferenced commit retention, force-push protection.
- Whether the folded-State checkpoint of B-gap-log-scale shipped.
- Whether an older ddflow rejects or skips an unknown event kind.
- Parseability of acceptance criteria inside the 2276 task bodies.
- How many committed events predate full redaction (D-unify item 6 covers new writes only).
- Exact fraction of the 1353 event-touching commits that are event-only.
- CLI parser compatibility of a `history collapse` verb with the existing `ddflow history` flags.
- How `is_tree` resolves `head12`.
- No `compact` retention kind was found in `ddflow/templates/events/*.toml` by grep on main; read `core/eventkinds.py` only for the header.
