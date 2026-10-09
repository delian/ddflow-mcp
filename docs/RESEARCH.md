# Research log

Every entry carries a **verdict**: `CONFIRMED`, `REFUTED` or `THEORETICAL`. A
`CONFIRMED` or `REFUTED` verdict must be backed by a probe whose command **and output**
appear here. `THEORETICAL` must say why no probe was possible. An entry with no verdict
is a literature summary, not research.

**Probe budget for this pass: ≤ 60 minutes wall-clock, 0 GPU-hours.** Honoured.

---

## R1 — Can SQLite be the source of truth on an NFS checkout?

**Question.** The motivating project's repository lives on NFS, and several agents on
possibly several machines share it. Conventional wisdom is that SQLite over NFS is
unsafe and that `O_EXCL` is unreliable there. Is the conventional answer right *on this
hardware*, and if so what survives?

**Claim.** SQLite WAL and POSIX locking are unsafe on this mount; a file-based scheme
using `link(2)` is required.

**Falsifier.** A concurrent-writer benchmark on the actual mount showing zero lost
updates and zero errors for SQLite/`flock`/`O_EXCL`.

**Probe.** `probes/probe_01_nfs_lock_primitives.py` — 12 processes × 40 increments,
barrier-synchronised, five primitives, run against the NFS repo path and `/tmp` (ext4)
as a control.

```console
$ python3 probes/probe_01_nfs_lock_primitives.py "$PWD/.probe-nfs" /tmp/probe/ext

=== NFS (repo): .../bridge-cse.../.probe-nfs ===
  sqlite[wal     ] committed= 480 errors=  0 counter= 480 LOST=0
  sqlite[truncate] committed= 480 errors=  0 counter= 480 LOST=0
  flock              acquired= 480 errors=  0 counter= 480 LOST=0
  oexcl              winners=  40 (must be EXACTLY 40) -> OK
  link               winners=  40 (must be EXACTLY 40) -> OK
  (arm wall-clock 65.0s)

=== ext (/tmp): /tmp/probe/ext ===
  sqlite[wal     ] committed= 480 errors=  0 counter= 480 LOST=0
  sqlite[truncate] committed= 480 errors=  0 counter= 480 LOST=0
  flock              acquired= 480 errors=  0 counter= 480 LOST=0
  oexcl              winners=  40 -> OK
  link               winners=  40 -> OK
  (arm wall-clock 1.8s)

$ findmnt -T . -o FSTYPE,OPTIONS --noheadings
nfs4   rw,relatime,vers=4.2,...,hard,proto=tcp,timeo=600,...,local_lock=none,...
```

**Verdict: REFUTED** — the claim was wrong about *correctness* and right about *cost*.

Every primitive is correct on this mount. The reason is visible in the mount options:
`vers=4.2` with `local_lock=none`, so locking goes to the server and NFSv4's stateful
`OPEN` provides the exclusive-create semantics NFSv3 lacked. The conventional warning is
about NFSv3, and repeating it here would have been cargo-cult.

But the control arm is the finding that actually shaped the design: **NFS is ~36× slower
than local** (65.0 s vs 1.8 s). So correctness permits SQLite-on-NFS while performance
forbids putting it on the hot path.

**What this changed.** The design takes the third option: the *log* is authoritative and
is appended to under a `flock` (a few hundred bytes, one lock, one `fsync`), while
SQLite is a **derived, gitignored, rebuildable index** that can live anywhere and be
thrown away. The property that survives is portability — a design that depended on NFSv4
semantics would silently break on NFSv3, whereas `flock`-around-append plus
content-addressed dedup is correct even where locking is advisory-only, because the
content hash makes a duplicated append idempotent.

**Source of the conventional claim:** [SQLite, *How To Corrupt An SQLite Database
File*, §2.1 "Filesystems with broken or missing lock implementations"](https://www.sqlite.org/howtocorrupt.html)
— "if the locking primitives do not work correctly, then two processes can write to the
database at the same time". Opened and read; it is a statement about broken lock
implementations, not about NFS per se, which is what the probe went on to test.

---

## R2 — Should the source of truth be a database, markdown, or an event log?

**Question.** Three designs are viable. Which one fails least badly?

**Candidates and their measured failure modes:**

| Design | Used by | The failure it cannot avoid |
|---|---|---|
| Markdown as source of truth | the originating project | drift — an audit script exists solely to reconcile checkboxes against commits, and found ~170 of 269 unchecked items were already shipped |
| SQLite as source of truth | TaskMaster | a binary file in git; two branches cannot be merged |
| Append-only event log, everything else derived | ActiveGraph | replay cost; no human-editable surface |

**Claim.** An append-only log sharded per agent has no merge conflicts *by construction*,
and this is worth its replay cost.

**Falsifier.** A replay cost that makes ordinary commands slow, or a merge that conflicts
anyway.

**Probe.** `probes/probe_02_fold_throughput_and_merge.py` — folds 20 000 synthetic
events, then creates two real branches that each append concurrently and merges them.

```console
$ python3 probes/probe_02_fold_throughput_and_merge.py
(a) folded 20000 events in 0.049s  (407,926 events/s), 11111 items
(b) merge exit=0: Merge made by the 'ort' strategy.
    conflicted files: (none)
    after rebuild, both branches' tasks present: ['X1', 'X2']
```

**Verdict: CONFIRMED.** Two agents on two branches touch two different shard files, so
git has nothing to conflict over — the merge is clean with no manual resolution, and
re-folding the union yields both branches' work.

Replay is also far cheaper than assumed: **407,926 events/s**, so a 20 000-event project
folds in 49 ms — well under the cost of the single `git status` that surrounds it. (My
pre-probe estimate was 47,600 events/s, low by 8.5×; the estimate was never load-bearing
because the SQLite index means the common read path does not fold at all, but it is
recorded here because an unprobed number that happens to be conservative is still an
unprobed number.)

**Residual risk, addressed 2026-09-27 — and the earlier plan here was wrong.** This
paragraph used to reserve a `log.compacted` kind for a compaction path. Two things were
mis-stated. First, *"the SQLite index means the common read path does not fold at all"*
(above) is false for `Store.ensure`, which on the fresh path returns
`fold(log.read_all())` — the index is a SEARCH projection and cannot reconstruct a full
`State`, so the state path always paid the full read. Second, the cost was attributed to
`fold`, which is **7%** of it; `Event.from_json` is **84%**. Re-measured at 20,000
events: 115 ms total, of which parsing 97 ms, fold 9 ms, sort 3.8 ms, read 3.7 ms.

So the fix was to stop re-parsing, not to shorten the log: `read_all` now re-parses only
the appended tail, with the re-used prefix re-hashed to prove it is still the same bytes.
Warm read **11.9 ms vs 121.1 ms (10.2×)** at 20,000 events, **62.8 ms vs 623.8 ms** at
100,000. An incremental *projector* is still refused (`Store.rebuild`'s docstring — it
can disagree with `fold`); an incremental *reader* cannot disagree, because each line
parses independently of every other.

**And the compaction was DECLINED, with the probe that killed it.** `ddflow progress` and
`ddflow loops` consume raw events: `progress.work` pairs `lease.acquired` with the next
release across the entire history. Leases and gate outcomes are not `PROVENANCE_KINDS`,
so the filed recipe — "every `PROVENANCE_KINDS` event plus the last state-bearing event
per subject" — leaves a `lease.released` with no acquire. Probe: a queue that fires
`repeat_claims` before compaction reports nothing after it, and six attempts become
**zero**. Under `[loops] on_detect = "block"` that is a behaviour change, not a lost
report. The kind is removed and the allowlist in
`tests/test_abandon_remove.py::test_every_declared_event_kind_can_actually_be_emitted`
is now empty. Regression tests and all eight mutations: `tests/test_log_read_cache.py`.
Budget: ≤1h, no GPU; spent ~50 min.

**Source:** [Sanders et al., *The Log is the Agent: Event-Sourced Reactive Graphs for
Auditable, Forkable Agentic Systems*, arXiv:2605.21997](https://arxiv.org/abs/2605.21997).
Opened. The line taken: "the append-only event log is the source of truth; the working
graph is a deterministic projection of that log", and its determinism contract —
replay is made sound by *recording* model responses rather than assuming they reproduce.
That caveat is why `ddflow replay` reconstructs the decision history and says plainly
that it does not reproduce the source.

---

## R15 — What config file does each coding agent actually read? (2026-09-27)

**Claim.** ddflow can register itself automatically in every popular coding agent, so
adopting a project takes one command per agent.

**Falsifier.** An agent whose MCP config path or JSON shape differs from the one ddflow
writes — because a wrong key is *valid JSON the agent silently ignores*, which is
indistinguishable from success at every layer ddflow can see.

**Probe.** Read each product's OWN documentation, fetched rather than summarised, for
sixteen agents. Budget: ≤2h, no GPU. Four parallel searcher subagents; this synthesis and
the verification are separate, per §Research-workflow rule 7.

**Verdict: CONFIRMED in weakened form.** 15 of 22 agents have a project-level MCP config
file ddflow can write. **7 do not, and that is a verified absence rather than a gap.**
Five distinct JSON shapes are in use, and three of them are NOT the common `mcpServers`
form:

| shape | who | structure |
|---|---|---|
| `mcpServers` | Claude, Gemini, Cursor, Kimi, Qwen, Antigravity, Devin, Qodo, Tabnine | `{"mcpServers": {"ddflow": {command, args}}}` |
| `servers` | **VS Code** | `{"servers": {"ddflow": {"type": "stdio", ...}}}` |
| `mcpServers` + `type`/`tools` | **Copilot CLI** | `type: "local"`, and `tools` is an ALLOWLIST |
| `mcp` → `servers` | **ZCode (GLM)** | nested one level deeper |
| `mcp` + array command | **opencode**, **Kilo** (an opencode fork; corrected 2026-09-27 — see below) | `command` is ONE array including the arguments |

**Three findings worth more than the table.**

1. **A search snippet is a pointer, and it was wrong.** Secondary write-ups state that
   Kimi Code reads a repo-root `.mcp.json` "same as Claude Code". Its official docs say
   the project config is `.kimi-code/mcp.json`. Had the snippet been trusted, ddflow would
   have written Claude's file and reported success.
2. **`.vscode/mcp.json` is VS Code's file, not Copilot's** — ddflow had it registered
   under `copilot`, conflating an editor with a vendor. Copilot's own surfaces are the CLI
   (`.github/mcp.json`, committed) and the cloud coding agent (repo *Settings*, no file at
   all). They are now separate targets, `vscode` and `copilot`.
3. **Windsurf is Devin Desktop.** `docs.windsurf.com` 307-redirects to `docs.devin.ai`
   after the Cognition acquisition, and the current page states a **global config only** —
   contradicting several third-party pages that still name
   `~/.codeium/windsurf/mcp_config.json`. Recorded as `SHAPE_NONE`, not guessed.

**The test that mattered.** The first version asserted a round trip: write with
`place_server`, read back with `get_server`. Both go through one helper, so the test proved
the writer and reader agree with *each other* — which they always will. Four planted
mutations (VS Code given `mcpServers`, opencode given the common form, ZCode's nesting
flattened, Copilot's `tools` dropped) left it **green**. Replaced with `DOCUMENTED_SHAPES`
in `tests/test_adopt.py`: the expected JSON written out **literally**, as the external
contract it is. All five mutations now fail. *Parity is not correctness — the fixture has
to come from outside the code it checks.*

**And it still missed one (2026-09-27).** `DOCUMENTED_SHAPES` is keyed by SHAPE: it pins
what each shape looks like, not which agent is assigned which. Kilo was assigned
`mcpServers` while its own delta doc (`templates/drivers/deltas/kilo-cline.md`) showed the
`mcp` form — two claims in one repo, one of them wrong. Kilo's primary docs
(`kilo.ai/docs/automate/mcp/using-in-cli`) and a probe against Kilo 7.2.20 settled it: an
`mcpServers` block in `.kilo/kilo.json` gives "No MCP servers configured"; the `mcp` block
lists the server. Kilo's CLI is an opencode fork and takes opencode's shape. Every earlier
`adopt --agents kilo` wrote a file Kilo ignored. Pinned by
`test_kilo_is_registered_under_the_key_kilo_actually_reads`, which reads the file literally.

**Instruction surfaces — the second half, and a design REFUTED before it shipped.**

Nine of the 22 read something other than `AGENTS.md` first. The obvious fix is a one-line
pointer stub in each native surface, and it is wrong: ddflow's own delta doc already
recorded *"a link is only followed if the agent chooses to follow it"*
(`templates/drivers/deltas/kilo-cline.md`). **REFUTED without a new probe — the evidence was
already in the repository**, which is the cheapest rung of all and the one I nearly skipped
by reasoning forward from "duplication is bad" instead of checking what had been learned.

So the block is **inlined** into all seven surfaces, and the duplication is owned by a
check: one generator (`project_section`), a managed block per surface, `rules_status()`
comparing each against it, `doctor` failing on drift. Probed across five break modes — an
edited block, stripped markers, a deleted file, a Cursor rule set to `alwaysApply: false`,
and an `.aider.conf.yml` that stopped listing `AGENTS.md` — all five reported, `doctor`
exit 1. Eight mutations verified in `tests/test_unified_rules.py`.

Two findings from building it. **Aider is the one agent where doing nothing is silent total
failure**: it discovers no instruction file, so without a `read:` entry the rules are in the
repository and invisible. And **a substring assertion is not a semantic one** — appending a
second `read:` key leaves both filenames in the file while YAML resolves duplicates to the
last, so the operator's entry is present in the text and gone from the parsed config; the
test now counts the keys and parses the list.

**What remains per-agent.** Nine of the 22 read something other than
`AGENTS.md` first — `QWEN.md`, `.clinerules/`, `.tabnine/guidelines/`, `replit.md`,
`.goosehints`, `.cursor/rules/*.mdc`, Aider's `read:` list — and Cody's is undocumented.
ddflow writes `AGENTS.md` plus a native rule for Cursor only, so for the rest the delta doc
asks the operator to add a pointer by hand. That is prose where it should be a generated,
drift-checked artefact; filed as **B170**.

**Sources.** Every path and shape above traces to a fetched official page; the per-agent
URLs are in the four research transcripts and quoted in each
`ddflow/templates/drivers/deltas/*.md`.


## R16 — Can an autonomous queue work where merges need approval? (2026-09-27)

**Operator question.** Make ddflow usable where teams run gitflow, merge only through
approved pull requests, and tag versions — without giving up what ddflow is for: agents
working task to task, phase by phase, in parallel, with as little human interaction as
possible.

**Claim.** Approval-gated merging can be absorbed by the queue with the agent's loop
unchanged (`next` → `claim` → work → gates → `merge`), and with a human needed ONLY for the
decision humans are there to make — approving — not for bookkeeping around it.

**Falsifier.** Any one of: (a) an agent has to wait (poll, sleep, hold a lease) for a
human before it can take other work; (b) a merged request needs a person to tell ddflow it
merged; (c) review feedback needs a person to relay it to an agent; (d) ddflow can land
unapproved work on a protected branch.

**Probe.** The forge CLI contracts, read from the primary documentation rather than from
memory (§6.5 of HANDOFF — a snippet is a pointer, never a source):
`cli.github.com/manual/gh_pr_view` (the full `--json` field list: `reviewDecision`,
`statusCheckRollup`, `mergeCommit`, `headRefOid`, `latestReviews`, …), `gh_pr_create`
(prints the URL; says nothing about an existing request → ddflow looks first),
`gh_pr_merge` (`--match-head-commit`, `--auto`, merge-queue behaviour);
`gitlab-org/cli` docs for `glab api` (placeholders `:id`, `:fullpath`, …) and
`glab mr create`; the GitLab REST merge-request API (`state` ∈ opened|closed|merged|locked,
`merge_commit_sha`, `squash_commit_sha`, `detailed_merge_status`). Then an end-to-end
test against a fake `gh` whose pushes and merges land in a REAL bare remote
(`tests/fakeforge.py`), so every "it is on main" assertion is a statement about git.

**Verdict: CONFIRMED, for all four falsifiers, with stated limits.**

| falsifier | how it is refuted |
|---|---|
| (a) agent waits | `merge` in PR mode releases the lease and parks the item in a new derived state, **REVIEW** — not RUNNING (that is the shape of a crash) and not BLOCKED (nothing is wrong). Dependents may **stack** on the unmerged branch. |
| (b) merged needs a person | `pr sync` (run by `next` itself while anything is in review) records the merge, passes the `merge` gate on the forge's evidence, completes the item, retargets stacked requests. |
| (c) feedback needs relaying | "changes requested" returns the item to the queue with review bodies AND line comments; `brief` leads with them; the next claim resumes the same tree and the next `merge` updates the same request. |
| (d) unapproved landing | ddflow merges only on an explicit APPROVED decision with no failing checks, pinned with `--match-head-commit` to the head that was approved; a stacked request is never merged into its dependency's branch; the forge's branch protection is the final authority and its refusal is reported, not worked around. ddflow holds no token — it drives `gh`/`glab` as the operator. |

**Findings the design rests on.**

1. **Two axes, not one.** Branching model (trunk | gitflow) and integration (local merge |
   pull request) are independent; GitHub flow is trunk + PR. One knob would make the
   combination nobody listed inexpressible.
2. **"Approved" on GitLab needs a named approver.** The approvals endpoint reports
   `approved: true` on a project with NO approval rules — "nobody had to approve", not
   "somebody did". Treating it as approval would merge unreviewed work. Pinned by a test.
3. **A GitHub status rollup has two shapes.** CheckRuns carry `status`/`conclusion`;
   legacy commit statuses from external CI carry `state`. Reading one shape calls every
   external CI "passing" by omission.
4. **Merge commits must not count as releasable commits.** Gitflow's back-merge of the
   tag into develop is a commit after the tag, so develop always looked one commit ahead
   of its release. `--no-merges`. Found by the gitflow version test, mutation-verified.
5. **The back-merge must come FROM production.** The tag sits on production's merge
   commit; back-merging the release branch leaves the tag unreachable from develop and
   the next version is computed from the previous tag.
6. **Pre-existing defect found on the way:** `merge_strategy = "squash"` never committed —
   `git merge --squash` stages the result and ignores `-m`, so ddflow reported a merge
   with the work left uncommitted in the primary. Fixed, with a failing-first test.
7. **Merging without a checkout generalises.** The primary can be on only one branch, and
   gitflow lands hotfixes and releases on two. A target checked out nowhere is merged in a
   throwaway worktree (only the ref moves); one checked out in another linked worktree is
   refused (someone may be working in it).
8. **Open a request only for work that could complete once merged.** The verdict is
   computed as if `merge` had passed; anything left refuses the request, because a
   refusal after the merge can no longer stop anything and a reviewer's time is the
   scarce resource.

**What the adversarial review of the first version found (same day).** A subagent told
to refute it found five defects with running probes and two from documentation; all
seven are fixed, each with a regression test that fails when the fix is reverted:

* **"Pinned to the approved head" was false.** `--match-head-commit` pinned the head ddflow
  last SAW. An item in REVIEW could be claimed, a push added, and the old approval merged
  it — GitHub does not dismiss stale approvals by default. Now a claim of an item in
  review is refused, and an approval (or change request) counts only if the review's own
  `commit.oid` is the current head.
* **An answered change request bounced forever.** The forge keeps reporting
  CHANGES_REQUESTED until the reviewer looks again, so every sync after the fix-up push
  reopened the item with the old feedback. Same fix: a decision on an older head is stale.
* **A stacked request merged by a person into its dependency's branch was marked DONE**
  while the target lacked its code. Merged is not landed: it now waits for its dependency,
  or is parked if the dependency will never land.
* **Abandoning an item with an open request** left the request mergeable and unrecorded.
  Refused without `--force`. Adding this found a PRE-EXISTING crash: `abandon` on a DONE
  item raised TypeError (`reason` passed twice to `O.refused`) instead of refusing.
* **A closed release request was re-polled forever** and blocked its version number.
* **`reviewDecision` is null without a required-review rule**, so approvals and change
  requests were invisible in exactly the lightly protected repos. Derived from
  `latestReviews` when it is null.
* **A hotfix back-merge from the hotfix branch fails where the forge auto-deletes merged
  branches.** It is now opened from production, like the release back-merge.
* And `next` asked an unreachable forge once per request, each with a 120 s timeout; it
  now stops at the first.

**Not built, and why** (filed as B171–B177): a hotfix's back-merge request is reported but
not tracked on the item; merge queues are honoured only as the forge's own refusal/queue
(no queue-position reporting); Bitbucket/Gitea/Azure DevOps have no adapter; `version cut`
writes no CHANGELOG file and bumps no version string in `pyproject.toml` and friends;
there is no automatic release per completed phase; review comments are carried as text,
not as individually resolvable threads.

---

## R17 — Several release lines, and choices that belong to the operator (2026-09-27)

**Operator question.** Support, besides gitflow, trunk-based development and GitHub flow,
projects with several major trunks receiving fixes; make how a fix travels between them
configurable per project; ASK the agent or operator which way they want, and when they do
not care, start with a default and follow it. "The tool should be flexible enough for any
flow and project, and let the operator or agent choose the most suitable."

**Claim.** Release lines fit the existing model without a new kind of object: a line is a
target branch, a fix for several lines is one item plus generated PORT items that are
ordinary tasks, and a choice is an event in the same log as everything else.

**Falsifiers.** (a) a port that starts before what it carries has landed, or that ports
something other than what landed; (b) two agents refused on the same file on different
lines, or allowed on the same file on one line; (c) a fix landing on a line nobody asked for,
or a typo'd line silently read as "current"; (d) a choice made by ddflow with no record of
it, or a recorded default that a later ddflow silently changes; (e) a maintenance line
tagged into the next major.

**Verdict: CONFIRMED against all five, by `tests/test_lines.py`** (real repositories;
branch contents checked with `git show`).

**Design decisions and why.**

1. **Two strategies, because teams genuinely split.** Forward-merge (fix the oldest line,
   merge each line into the next — git.git's own maint→master) keeps newer lines supersets
   of older ones by ancestry and needs no per-fix bookkeeping, but it drags everything on
   the older line along and conflicts grow as lines diverge. Cherry-pick (fix the newest,
   backport) is what projects with diverged lines do. Neither is right for every project,
   so it is a choice, and the default is the lower-maintenance one.
2. **Forward-merge passes through every line between.** A merge cannot skip a line; the
   plan includes the intermediates and says so.
3. **A cherry-pick port applies what LANDED** — the target's `before..after` range recorded
   at merge time — not the task branch's commits, which differ by merge strategy (a squash,
   a no-ff merge, a fast-forward). A rebase-merge on a forge lands several commits whose
   first parent is not the old target; that case is filed (B178), not guessed.
4. **A conflicting port is work.** Markers are left in the port's own tree and the claim
   names the files; only a port that cannot start is "failed", and then the tree is left
   clean. A human is needed only if the agent cannot resolve it — like any task.
5. **Choices are events, overlaid where the config is silent.** The config file is the
   operator's and wins; a recorded choice (agent or person, with a reason) comes next; a
   default is applied at the first moment it matters AND recorded, so it is followed from
   then on and is visibly "a default nobody chose". A brief lists relevant undecided
   choices, so the agent asks at the start, not after the work.

**What the adversarial review of the first version found.** Five defects with running
probes, two by reading; all fixed, each with a regression test that fails when reverted:

* **"" and the current line's NAME compared unequal** — both land on main, so two agents
  were allowed onto one file. Every port to the current line carries the name, so this was
  the common case. Conflicts now compare the resolved line (`line_key`).
* **Moving a fix re-aimed its forward-merge ports**: `update FIX --line 3` after ports were
  planned made `FIX@2` merge all of main into `maint/2.x`. The line of a planned port, or of
  an item already forked, can no longer change; `merge` also refuses a branch forked from
  another line's base.
* **`claim --force` on a port before its source landed recorded "clean"** — a
  forward-merge of a branch without the fix is "Already up to date" — and the port was
  never applied again. It is now not recorded until it can be applied, a re-claim applies
  it, and a forward-merge verifies the source's landed commit is actually in what it merged
  (which also catches a stale ref after a failed fetch in PR mode).
* **A line removed from config silently became the current line.** Its items are now
  held with the reason.
* **A fix on two lines appeared in only one line's release notes**: releases now exclude
  items only when released on the SAME line.
* **A port in an adopted tree, or with no tree, was silent** — the claim now explains it
  is a port and what to run.
* And a **pre-existing** fold defect it led to: `complete` without `--sha` ERASED the sha
  `merge` recorded, so no finished item was ever found on any branch — every version's item
  list and item-based bump were empty (R16's own version test checked only commits).

**Filed:** B178 (rebase-merge landing range), B179 (`task_add` wants a named record, as
decisions have), B180 (a port's fix changed after the port was generated — the port
carries the version that landed, which is right, but nothing re-offers a port when the fix
is amended by a follow-up), B181 (the MCP handshake does not yet list undecided choices;
`brief` does).

---

## R18 — GitLab flow's environment branches (2026-09-28)

**Operator request.** Support GitLab flow's environment branches (`main -> pre-production
-> production`), after R17 showed its release-branch variant was already covered by
release lines with `port_strategy = "cherry-pick"`.

**Claim.** Promotion is a special case of what already exists: a promotion is an ordinary
task whose tree starts from the environment branch with the upstream branch merged in (the
forward-merge port mechanism), landing by merge or merge request.

**Falsifiers.** Production receiving anything pre-production does not have; a promotion
landing somewhere other than its environment; two promotions to one environment at once;
promotions blocked forever by gates meant for authored code; an environment silently
auto-promoted that the operator did not list.

**Verdict: CONFIRMED** by `tests/test_environments.py` and a PR-mode test in
`tests/test_flow.py` (the request goes INTO the environment branch and is merged only after
approval; checked on the forge's bare remote).

**Found while building it.** The reviewer-independence check blocked every promotion: its
pipeline rightly has no review gate, so no reviewer could ever satisfy it. A promotion
authors nothing -- the work it moves passed that check as the tasks that produced it -- so
promotions are exempt, and only promotions.

**Not built:** B182 (a hotfix straight on an environment branch is not refused by the
commit hook -- ddflow never targets one, but a person can), B183 (no "deployed at"
timestamps beyond the promotion's completion time).

---

## R3 — Is MCP sufficient to make this agent-agnostic?

**Claim.** Shipping only an MCP server makes the workflow portable across agents.

**Falsifier.** Any target agent that cannot consume MCP, or any workflow step that MCP
cannot express.

**Probe.** Checked what each target actually reads, and whether a non-MCP path is needed.

```console
$ # Instruction files each agent loads (from each project's own docs):
  Codex, Copilot, Cursor, Gemini CLI, Jules, Aider, Zed, Windsurf, Devin -> AGENTS.md
  Claude Code                                                            -> CLAUDE.md
$ # MCP transport support: all of the above support stdio MCP servers.
$ # But: CI jobs, Makefiles, git hooks and humans consume none of it.
```

**Verdict: REFUTED as stated, CONFIRMED in weakened form.** MCP is sufficient for *agents*
but not for the workflow, because a meaningful share of the steps (a pre-commit gate, a
CI check, an operator inspecting a crashed worktree at 2 a.m.) have no MCP client.

**What this changed.** ddflow ships **both** surfaces over one implementation: the CLI is
primary and complete, and `mcp_server.py` maps each tool onto the same `cli.main()` call
in-process. Two tests pin that they cannot diverge
(`test_mcp.py::test_a_tool_call_returns_the_cli_result`, and the demo's
"both doors must give the SAME answer" step comparing board and scheduler output).

The MCP SDK was **not** taken as a dependency: the stdio transport is newline-delimited
JSON-RPC 2.0, roughly 200 lines, and a portability tool that only installs where a
package index is reachable is not portable. `python3` and `git` are the entire runtime.

**Source:** [AGENTS.md](https://agents.md/) — the cross-tool instruction format, donated
to the Linux Foundation's Agentic AI Foundation in December 2025, read by 30+ agents.
This is why `ddflow adopt` writes `AGENTS.md` and only *points* `CLAUDE.md` at it.

---

## R4 — Retrieval: full-text, embeddings, or both?

**Claim.** Lesson retrieval needs embeddings; BM25 will miss paraphrases.

**Falsifier.** BM25 ranking the right lesson first for queries sharing no keyword with it.

**Probe.** Three paraphrased queries against a seeded corpus, FTS5 BM25 only.

```console
q='reviewer unavailable clean'   -> 'Never treat an unavailable reviewer as a passing review'
q='empty list assertion'         -> 'Empty collections make assertions vacuously true'
q='parallel agent stash'         -> 'Git worktrees must not share a stash stack'

$ # and from the polyglot demo, with NO shared keyword at all:
q='cutting a url slug short leaves a dangling hyphen'
  -> 'Truncating a slug can leave a trailing separator'
```

**Verdict: REFUTED for this corpus size.** BM25 with a `porter` stemmer ranked the
correct lesson first in every case, including the last, where "cutting/short/dangling
hyphen" shares no stem with "truncating/trailing separator" except via the stemmer and
the co-occurring domain terms.

The honest reading: lesson corpora are small (hundreds of entries, not millions) and
lessons are *written* with their trigger words in them, which is the regime BM25 is
strongest in. Embeddings would add a model dependency, a download, and an index to keep
warm, for a ranking improvement this probe could not detect.

**Accepted limit:** a genuinely synonym-only query ("automobile" for "car") will miss.
The mitigation is a documented one-line convention — write the rule's trigger words into
the title — not a vector database. `lessons.search_backend` exists so a future project
can switch without a code change.

**Source:** [Willison, *Hybrid full-text search and vector search with
SQLite*](https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/)
— opened; the RRF hybrid pattern it describes is what `search()` would grow into if the
limit above ever bites.

---

## R5 — Should an expired lease be reclaimed automatically?

**Claim.** Auto-reclaiming an expired lease is safe, because a crashed agent's worktree
holds nothing worth keeping.

**Falsifier.** Any case where a crashed agent's worktree contained work existing nowhere
else.

**Probe.** The originating project's own history, plus a constructed scenario.

```console
$ # From that project's operational memory, verbatim:
  "A killed session leaves FINISHED work uncommitted in its worktree (found
   2026-08-15, work recovered on 08-17, commit f9c9f44b, existing nowhere else)."
$ # And the counter-case, from the same source:
  "A dirty agent worktree is NOT automatically unshipped work: all 3 dirty
   worktrees held EARLIER drafts of phases already in master."
```

**Verdict: REFUTED.** Both records are true at once, which is exactly why the decision
cannot be automated: a dirty worktree is sometimes irreplaceable and sometimes garbage,
and nothing in the metadata distinguishes them. Only a diff does.

**What this changed.** `lease.reclaim_policy` defaults to `report`. `ddflow recover`
*measures* each tree (uncommitted files, unmerged commits) and prints the exact `git
diff` command, but never deletes and never steals. `recover --apply` expires only trees
it measured as empty. Pinned by
`test_lease_and_recovery.py::test_sweep_never_touches_salvageable_work` and by the
crash-recovery demo scenario.

---

## R7 — Distribution: vendored scripts, or a published MCP package?

**Question (operator, 2026-09-24).** "Make the project management code portable, using
MCP... everything auto-installable... publish and register the MCP so they can be
downloaded and installed automatically — so every project needs only a minimal startup
language description."

**Claim.** Shipping as a published package invoked by `uvx` removes every adoption step
except one line of MCP config, and shrinks the per-project instruction text enough to
matter.

**Falsifier.** Any adoption step that survives; or a per-project text that does not
actually get shorter.

**Probe.** Built the wheel, installed it into a clean venv, adopted a fresh repo, and
measured the resulting artefacts.

```console
$ uv build && pip install dist/ddflow_mcp-0.1.0-py3-none-any.whl
$ cd /tmp/fresh-repo && ddflow adopt --agents cursor
  wrote docs/ddflow/drivers/implement-phase.md
  registered ddflow in .cursor/mcp.json
  wrote .cursor/rules/ddflow.mdc (always-applied project rule)

$ cat .cursor/mcp.json
{ "mcpServers": { "ddflow": { "command": "uvx", "args": ["ddflow-mcp"] } } }

$ # the ENTIRE per-project instruction text:
$ awk '/DDFLOW:BEGIN/,/DDFLOW:END/' AGENTS.md | wc -w
232
```

**Verdict: CONFIRMED.** Adoption is one line of MCP config; `uvx` fetches and runs the
package on first use, so there is no clone, no virtualenv, no `PYTHONPATH` and no
install step to forget. The per-project text is **232 words**, because the MCP tool
descriptions already carry the how — and a second copy of that in every project is a
copy that drifts from the one the model reads at call time.

**A defect this probe caught that nothing else could.** `templates/` lived BESIDE the
package, so `adopt` worked perfectly from a source checkout and raised
`FileNotFoundError` for every installed user. A source tree is exactly where that bug is
invisible. Fixed by moving templates inside the package; pinned by
`tests/test_packaging.py`, which builds the real wheel and looks inside it.
Mutation-verified by moving the directory back out (2 tests red).

**Runtime dependencies are an allowlist, and the allowlist is short.** Zero was the
original design: it is what lets the server install inside a sandbox with no reachable
package index, a CI image, or another tool's ephemeral container. Jinja2 is now the one
deliberate exception (prompts are Jinja templates; leaving it undeclared shipped a second,
untested renderer). `test_the_package_declares_only_the_dependencies_we_chose` asserts
the built wheel's metadata against `ALLOWED_RUNTIME_DEPS`, so a dependency cannot creep in
by accident. (An earlier zero-dependencies assertion was replaced by it
in 48aae97.)

**Registry.** `server.json` follows the
[MCP registry schema](https://modelcontextprotocol.io/registry/quickstart)
(`io.github.delian/ddflow-mcp`, PyPI `ddflow-mcp`, `runtimeHint: uvx`), published by
`.github/workflows/publish.yml` on a version tag via OIDC trusted publishing — no stored
tokens. The workflow refuses when tag, `pyproject.toml` and `server.json` disagree about
the version; `test_the_declared_versions_agree` pins the same invariant locally.

---

## R8 — Can a local model serve as the cross-family critic?

**Question (operator, 2026-09-24).** "Maybe locally there is a QWEN model running, can
you check if you can use it as cross critic?"

**Probe — tier 0, existence check first.**

```console
$ ss -ltnp | grep :8000
LISTEN 0 4096 0.0.0.0:8000 ...
$ curl -s http://127.0.0.1:8000/v1/models
{"object":"list","data":[{"id":"Qwen/Qwen3.8-Flash-Next-FP8","max_model_len":262144,...}]}
$ nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
23335, VLLM::Worker_TP0, 123190 MiB      # TP4, ~123 GB/worker
```

**Verdict: CONFIRMED — and it is genuinely cross-family.** Qwen is Alibaba-pretrained;
the author here is Anthropic. That is the only property this reviewer is selected for.

**But the first real run returned UNAVAILABLE, and the failure is worth recording**,
because it is the exact shape this project is built to refuse to paper over:

```console
=== UNAVAILABLE === 0/5 chunks reviewed, 5 off-contract in 201s
reason: chunk 1: empty completion
```

**Diagnosis, measured on one 30 KB chunk:**

| `max_tokens` | `finish_reason` | content chars | reasoning tokens | wall |
|---|---|---|---|---|
| 6 000 | `length` | **0** | 6 000 | 39 s |
| 32 000 | `stop` | 2 396 (valid verdict) | 28 381 | 190 s |
| 4 000, `enable_thinking=false` | `stop` | 18 (`STATUS: FINDINGS 1`, no findings body) | 0 | 0.6 s |

A **reasoning model spends the token budget thinking before it emits anything**, so a
budget sized for the answer alone yields a truncated reply with an *empty content field*.
Disabling thinking is fast and useless: a verdict with no findings behind it.

**What this changed.** `[[reviewer]].max_tokens` now defaults to 32000 and
`max_chunk_chars` to 30000, both with the measurement in the comment; and a truncated
completion is reported as `TRUNCATED: the model consumed all N tokens (M of them
reasoning) before emitting any answer — raise max_tokens or lower max_chunk_chars`
rather than as "empty completion", which sends an operator hunting a healthy endpoint.

**The part worth keeping:** at no point did the pipeline report a pass. An endpoint that
was up, answering, and burning 6 000 tokens per request still produced `UNAVAILABLE,
5/5 off-contract` — because length is not a verdict, and the absence of a `STATUS:`
block is the absence of a review. That is the failure mode this system exists to make
loud, encountered against itself.

---

## R9 — Does the orchestration actually work end to end over MCP?

**Question (operator, 2026-09-24).** "Create a simulated project and test the tool as
MCP and verify the orchestration end to end."

**Claim.** The pieces are individually tested, so the whole works.

**Falsifier.** Any defect that only appears when the pieces are composed.

**Probe.** `demos/scenario_mcp_orchestration.py` — an invented two-phase Python library
(`taskmetrics`: duration parsing, statistics, a CLI over both) built by two agents, each
a separate MCP client process, **entirely over JSON-RPC with no CLI call at all**. 24
steps, 57 assertions, ~4 minutes: bootstrap an unadopted repo, configure it, discover a
local reviewer, fill a two-phase queue, fan out, get refused by the enforcement hook,
run real pytest suites, run the real cross-family critic, merge, close both phases, and
reconstruct from the log.

**Verdict: REFUTED, decisively.** The composed run found **eight defects that 213 unit
tests and four existing scenarios did not**, and two of them made core features useless
out of the box:

| # | Defect | Why the unit tests missed it |
|---|---|---|
| 1 | **The default agent identity was `{host}-{pid}`**, stable for exactly one process. So `ddflow claim` and the `git commit` hook seconds later were different agents: the hook **refused the holder's own commit and told them their lease belonged to somebody else**. Out of the box, enforcement rejected correct behaviour and blamed the user. | Every enforcement test passed `agent=` explicitly. The default path was never exercised. |
| 2 | **Merely starting the MCP server created `.ddflow/events/`**, so a handshake wrote to any repository an agent connected to — and the "is this project adopted?" check then answered yes about a directory the server had just created itself. | No test asked whether a read-only operation mutated the repo. |
| 3 | **A JSON-RPC notification deadlocked the client.** `notifications/initialized` correctly gets no reply; a client that reads one anyway blocks forever while both processes sit at 0% CPU looking healthy. | The server was right and tested; nothing had ever *sent* a notification. |
| 4 | **The coverage gap was suppressed in JSON mode.** "gate X never ran" printed only for humans, so an agent over MCP — always JSON — completed an item and was never told a gate had not run. | The human path was asserted; the JSON payload was not. |
| 5 | **Four CLI commands had no MCP tool** (`show`, `update`, `release`, `block`). The canonical driver *instructs* the agent to run `ddflow update --globs` before writing outside its claim — an instruction impossible to follow over MCP. | Nothing compared the two surfaces for completeness. |
| 6 | **No way to abandon or remove an item.** `item.abandoned`, `task.removed` and `phase.removed` were declared in the handler registry and handled by the fold, and nothing emitted any of them. A task created speculatively held its phase open **forever**, because completion counts any non-`done` task as unfinished and nothing could ever finish it. | A vocabulary with no way to say the words; no test tried to say them. |
| 7 | **`replay` dropped the phase/task BODY**, reducing a phase to "P1: Core" — the acceptance criteria and context, the part a rebuild most needs, were absent from the reconstruction brief. | The replay test used items with no body. |
| 8 | **The board counted abandoned tasks as outstanding**, so a completed phase rendered "3/4 tasks" — unfinished work that no longer exists. | Abandonment did not exist until #6 was fixed. |

**A ninth, corrected by probe rather than found by the scenario.** The scenario's merge
kept failing on a dirty `config.toml`, and the pre-check's stated reason was *"a merge
would mix them into the result"*. That premise is false:

```console
$ git merge --no-ff -m "merge feat" feat      # with an UNRELATED file dirty
exit=0 · Merge made by the 'ort' strategy.
  is my local edit still uncommitted?   M other.txt
  did it get into the merge commit?     0
$ git merge --no-ff -m "merge feat2" feat2    # branch touches the dirty file
exit=2 · error: Your local changes to the following files would be overwritten by merge:
	shared.txt
```

Git refuses precisely and only when the merge would overwrite a locally-modified file,
and names them. The blanket pre-check was both wrong in its reasoning and over-broad,
refusing safe merges — routinely including one blocked by ddflow's own freshly-written
config. Removed; git's own check is the better one.

**The pattern, stated plainly.** Every one of these lived in a *seam*: between two
processes (1, 3), between a read and a write (2), between two output surfaces (4, 5),
between a declared vocabulary and its callers (6), between a record and its projection
(7, 8). Unit tests exercise functions; seams only appear when the real pieces are
composed the way a user composes them. **The scenario is worth more than its assertion
count suggests, and "the parts are tested" is not evidence that the whole works.**

**Cost:** ~4 minutes per run, including a real cross-family review against a local
Qwen3.8-Flash-Next. Runs in the default `demos/run_all.py` sweep.

---

## R6 — Bugs this project found in itself

Each was found by a probe, fixed, and ships with a mutation-verified regression test.
They are recorded because the *classes* recur, not because these instances matter.

| # | Defect | Found by | Class | Regression test |
|---|---|---|---|---|
| 1 | A missing binary exits 127 under `shell=True` and was classified `failed`, so a tool that stopped being installed looked like a check that ran and found problems | bug-hunting `gates.py` | unavailable-as-failure | `test_gates.py::test_a_missing_tool_is_unavailable_not_failed` |
| 2 | `acquire` dropped `worktree`/`branch` when renewing an agent's own lease, so recovery reported **"nothing to salvage"** over a tree holding uncommitted work | crash-recovery demo | silent-knob-drop | `test_lease_and_recovery.py::test_recovery_never_says_nothing_to_salvage_over_real_work` |
| 3 | A legitimately *failing* command gate crashed the CLI, because `record` demands a reason and the command path supplied none | polyglot demo | error-path-untested | `test_cli.py::test_a_failing_command_gate_is_recorded_not_crashed` |
| 4 | `acquire` did not refuse an already-`done` item, so an agent with a stale snapshot re-did finished work — **50 claims for 32 tasks** | 8-process stress test | check-then-act on stale state | `test_lease_and_recovery.py::test_a_completed_item_cannot_be_reclaimed_by_a_stale_agent` |
| 5 | Untracked files made the primary checkout look dirty, so `merge` refused forever in any repo with a build directory | parallel-phase demo | over-broad precondition | covered by the demo; `worktree.dirty(untracked=False)` |
| 6 | The reconstruction brief named each rejected approach but omitted the probe **output** that killed it | reconstruct demo | evidence-dropped | `test_store_and_session.py::test_a_rejected_approach_carries_the_measurement_that_killed_it` |
| 7 | Auto-generated ids used `int(time.time())`, so anything created in the same second **silently overwrote** its predecessor — 7 lessons added, 2 survived | dead-knob probe | collision-by-timestamp | `test_cli.py::test_auto_generated_ids_do_not_collide_within_one_second` |
| 8 | Four documented knobs were **never read** by any code | `test_ratchet_no_dead_knobs.py` | dead config knob | the ratchet itself; allowlist may only shrink |
| 9 | `lease.py` had forked the git layer; its branch resolver fell back to the literal `"HEAD"`, so on a repo whose default branch is `trunk`/`develop` the unmerged count became `rev-list HEAD..HEAD` = 0 and recovery advised **deleting a worktree holding unmerged work** | roborev `analyze duplication` | duplicate-then-drift | `test_lease_and_recovery.py::test_recovery_is_correct_on_a_repo_whose_default_branch_is_not_main` |
| 10 | A stale `lease.renewed` from a **former** holder overwrote the current holder's worktree path — every destructive path then targets the wrong tree | adversarial rubber-duck | stale-event-applied-unchecked | `test_model_and_schedule.py::test_a_stale_renewal_cannot_hijack_the_current_holders_worktree` |
| 11 | `worktree.remove(force=False)` passed an empty-string argv element, so `git worktree remove` exited 129 — **the safe removal path could never succeed**, training everyone to pass `--force` | adversarial rubber-duck | argv-construction | `test_lease_and_recovery.py::test_the_safe_worktree_removal_path_actually_works` |
| 12 | `_measure` encoded measurement failure as `-1`, and `salvageable = dirty > 0 or unmerged > 0` collapsed **unknown into clean** — an unreadable worktree was reported "safe to remove" | roborev `analyze architecture` | three-valued-collapsed-to-two | `test_lease_and_recovery.py::test_an_unmeasurable_worktree_is_never_reported_as_safe_to_remove` |
| 13 | `lease._alternatives` had forked the readiness rules and ignored `unknown_dep_policy`, so a refused agent was told to take an item the scheduler would also refuse | roborev `analyze architecture` | third-copy-drift | `test_lease_and_recovery.py::test_suggested_alternatives_are_exactly_what_the_scheduler_would_offer` |
| 14 | `render.board` re-typed the ten default gate ids, so a project that trimmed its pipeline got ten columns under a caption naming gates it does not run | roborev `analyze duplication` | hardcoded-copy-of-config | `test_cli.py::test_the_board_renders_the_CONFIGURED_pipeline_not_a_hardcoded_one` |

**Reviewer accounting for this pass** — each named, none silently omitted:

| Reviewer | Family vs author | Status | Found |
|---|---|---|---|
| Self bug-hunt (dead-knob probe, id-collision probe) | same | ran | #1, #7, #8 |
| Demo scenarios (4, end-to-end) | n/a — executable | ran | #2, #3, #5, #6 |
| Stress test (8 processes) | n/a — executable | ran | #4 |
| Adversarial subagent rubber-duck | **same family (Anthropic)** — does NOT satisfy cross-family independence | ran, 2 confirmed / 3 refuted | #10, #11 |
| roborev `analyze duplication` + `analyze architecture` | same family (`claude-code`) | ran (jobs 753, 754) | #9, #12, #13, #14 |
| Cross-family critic (`deepseek` @ LAN vLLM) | different family | **UNAVAILABLE — endpoint returned HTTP 000; a concurrent session was sweeping it** | — |

**The cross-family gate is NOT satisfied for this work.** Recorded as `unavailable`, never as
a pass — which is the same rule this system enforces on its users, applied to itself. A
reviewer from a different pretraining family should review `ddflow/` before it is
adopted for anything load-bearing.

**The pattern worth keeping:** defects 2, 3, 4 and 6 were invisible to unit tests and to
reading, and were all surfaced by *scenarios that used the system the way a user would*.
Defect 4 in particular does not reproduce below about six concurrent processes. Testing
the happy path of each function would have found none of them.

---

## R10 — the 2026-09-24 review pass: what a second adversarial reading found

**Question.** After 307 tests, five end-to-end scenarios, a cross-family critic run and
a roborev pass, what is left? Specifically: is the *requirements* surface as sound as
the *implementation* surface, or has the effort gone into making the code correct
against a specification nobody re-read?

**Budget.** Two adversarial subagents (one gap-audit against the operator's original
requirements, one bug-hunt with mandatory probes), plus one new end-to-end scenario
written deliberately to exercise the requirements rather than the code.

**Verdict: REFUTED.** The requirements surface was *not* as sound. **Twenty-two
defects** — fifteen from the two adversarial subagents and the new scenario, seven more
from roborev's duplication and architecture passes — of which the two most serious were
not code bugs at all but **features that were present, tested, documented and inert**.

### The two that matter most

**1. The phase dependency graph was decorative. CONFIRMED.**

`P2 needs P1` blocked `P2` — an item nobody claims, because phases complete when their
tasks do — and permitted every task *inside* P2, which is what an agent actually picks
up. The same hole appeared one level down once sub-tasks existed: an umbrella declaring
`needs A, B` had children with empty `needs`, handed out while A and B were open.

```console
$ ddflow next
Ready (4 ready, 0 running, 2 blocked):
  P1.T1  money
  P1.T2  account
  P1.T3.a  posting rules        <-- its umbrella needs P1.T1 AND P1.T2
  P2.T4  summary                <-- its phase needs P1, which is 0/5 done
```

**Why it survived five scenarios and 307 tests.** The pre-existing end-to-end scenario
*did* assert "P2's task is withheld while P1 is open", and that assertion passed —
because the scenario declared `needs="P1"` **on the task by hand** as well as on the
phase. The test restated the thing under test as its own input, so it was true for a
reason that had nothing to do with the phase graph. This is the sharpest instance yet
of the rule that a test which supplies the property it is checking proves nothing;
`tests/test_inherited_deps.py` never re-declares an inherited dependency, and says so
in its docstring.

**2. `ddflow claim` never looked at dependencies at all. CONFIRMED.**

```console
$ ddflow next
  (blocked) T2: deps — T1 is open
$ ddflow claim T2
claimed T2 (lease 1800s, renew every 300s)
```

`acquire` checked removed / done / leased / glob-overlap, and stopped. So an agent
picking work by id — which is what "implement phase X" does when it walks a plan —
bypassed the dependency graph entirely, and the failure is invisible: the work happens,
just in the wrong order, against files that do not exist yet. Fixed by routing both
callers through one `plan_blocker`, which is what `item_blocker`'s own docstring had
claimed for months.

### A third, found while writing a test for something else

**An unidentified reviewer satisfied the independence requirement. CONFIRMED.**

`gates.record` defaults the reviewer to the agent id, and `family_of` returned the
unmatched name back — so a `standards` gate recorded with no `--model` arrived as family
`"host-12345"`, compared unequal to the author's `"anthropic"`, and established
cross-family independence on its own. The check whose entire purpose is to refuse
*unverified* independence was passed by the **absence of information**.

It was found because a fixture meant to set up a refusal produced a pass — which is the
usual way, and an argument for writing the negative case first.

Under it sat the older defect: **two `family_of` implementations**, one in `reviewer.py`
with 23 substrings returning the model's own name for an unknown, one in `gates.py` with
9 returning `"unknown"`. A Phi reviewer was `microsoft` to the layer that ran it and
`phi-4` to the layer that decided whether it counted. Third instance of duplicate-then-
drift in this package; eliminated rather than guarded.

### The rest, by class

| # | Defect | Class |
|---|---|---|
| 1 | `replay` dropped every `decision.recorded` — the two renderers were unreachable | provenance loss |
| 2 | `split` appended children as it validated them; a collision on the second left the first written | non-atomic multi-write |
| 3 | `remove` checked for children only on phases, orphaning a task umbrella's sub-tasks | hierarchy half-applied |
| 4 | `block` was the one mutating command with no existence check; a typo created a phantom task the scheduler then offered | missing existence check |
| 5 | a gate in `gates.required` but in no pipeline made the requirement *disappear* | vacuous truth |
| 6 | `_h_lesson` rebuilt the object, dropping `superseded_by`; retired advice returned to the brief | fold reorder |
| 7 | the parallelism cap was measured against the queried phase, not the queue | scope mismatch |
| 8 | three loop detectors kept firing on removed items | stale finding |
| 9 | the two search backends disagreed on the shortest usable term by one character | two implementations of one rule |
| 10 | **a subprocess inherited the MCP server's stdin** | see below |
| 11 | unknown tool arguments were silently ignored despite `additionalProperties: false` | silent knob drop |
| 12 | `ddflow_phase_add` had no `globs`; 15 more CLI flags unreachable over MCP | surface divergence |
| 13 | `ddflow gate skip` and `bug found` had no MCP tool at all | surface divergence |
| 14 | adding a sub-task to a *claimed* task left the umbrella holding a lease that blocked its own children | transition reachable by two paths, guarded on one |
| 15 | a protocol-level refusal returned exit 0 | exit vocabulary broken at the boundary |

### #10 is the one to remember

ddflow runs as an MCP server **over stdio**: the JSON-RPC session *is* the process's
stdin and stdout. `subprocess.run(...)` with no explicit `stdin=` hands the child that
same pipe. All twelve subprocess call sites did this, and one of them is `gate run`,
which executes an arbitrary command from the project's own config.

The failure mode is as quiet as it gets:

```console
setup: {"jsonrpc": "2.0", "id": 2, "result": ...}
resp:  EMPTY
rc: 0   STDERR:
```

Exit **zero**, empty stderr, closed stream, nothing to explain it. Found because a
companion-detection probe added to `ddflow setup` ended the session on the *next* tool
call. Fixed with one `proc.py` whose default is `stdin=DEVNULL`, plus a ratchet that
fails if any module calls the stdlib directly.

**The mutation test for it passed at first, and proved nothing** — under pytest the
parent's own stdin is already empty, so the child read `''` either way. The real test
spawns a parent with a pipe carrying bytes; under mutation it now prints
`CHILD_SAW='PROTOCOL-BYTES\n'` / `PARENT_KEPT=''`, which is the defect itself.

### What changed in how this project is tested

Three ratchets, each mechanically checkable, each mutation-verified:

- **no module may call `subprocess` directly** (`tests/test_stdio_safety.py`);
- **every CLI *subcommand*** must have an MCP tool, not just every command — the old
  ratchet passed while `gate skip` had none, because `gate` was "covered" by
  `gate run`;
- **every CLI *flag*** must be reachable from its tool, with an exemption list that
  carries reasons. This one found 15 divergences on its first run.

And one scenario: `demos/scenario_full_lifecycle.py`, which drives a two-phase project
with sub-tasks from the operator's first English sentence to a rebuild-from-log, over
MCP. It was written to exercise the *requirements*, and it found defects 12, 13, 14 and
15 before it finished passing once.

### The seven roborev added

Its duplication analysis found four **duplicate-then-drift pairs**, which is the third
time that class has produced a real bug here, and the reason the house rule is
*eliminate* a duplicate rather than guard it twice. What makes them hard to see is that
both copies read as correct on their own — the defect exists only in the difference.

| Pair | The drift, and what it cost |
|---|---|
| three TOML overlay loaders | companions **silently dropped** unknown keys while gates and reviewers raised, and read only its own file while the others also read `config.toml`. A misspelt `commmand` wrote a launch line that fails mid-task — the silent-knob-drop class, in a package whose config loader raises on a typo'd *section* to prevent exactly that. Now one `tomlcfg.py` with one policy. |
| four HTTP call sites | three rewrote loopback for containers; the fourth is the only path `kind="anthropic"` and `kind="gemini"` use, so container support covered a third of the backends |
| two "is this on PATH" checks | the weaker copy faced a *reviewer*: it split `FOO=bar claude -p` into a head of `FOO=bar` and reported a false UNAVAILABLE, had no builtin allowlist, and let `shlex.split`'s `ValueError` escape a function contracted never to raise |
| two `family_of` wrappers | `resolved_family` used the shipped map while `reviewer_independence` used `[agent].families` — a project teaching the map its in-house model name had it honoured by the gate that decides whether a review counted and ignored by `reviewers list`. Half-unified earlier in this same pass; the wrappers re-opened the seam one level up |

Its architecture pass added a fifth and a sixth: `resources/read` had **a second data
path** folding the log directly in a module whose premise is "one implementation, two
doors" (with a dead, shadowed table entry beside it, which is how a second path stays
hidden — nothing reads the line, so nothing contradicts it), and `Store.__init__` ran
`mkdir`, so a read-only `ddflow status` **created `.ddflow/` in a repository that had
never adopted the tool**:

```console
$ git init -q /tmp/orchprobe && python -m ddflow --repo /tmp/orchprobe status
exit=0
$ ls -a /tmp/orchprobe   →   .  ..  .git  .ddflow
```

The rest of its architecture reading is structural debt rather than defect, filed as
B35–B40 with its measurements — including a profiled demonstration that `plan()` is
roughly quadratic in item count because `State` has no parent index (74% of `plan()` at
n=800 is calls into `children`).

**Two of my own mutation tests passed and proved nothing**, both for the same reason:
they asserted on the *source text* rather than on behaviour. One grepped the function
body for `rewrite_localhost` and survived removal of the call, because the import and
the comment stayed. The other asserted that a reader and a writer agree — which they
still do when both are wrong in the same way. Rewritten to assert the URL actually
requested, and the field VS Code actually reads.

---

## R11 — the importer against a real 400-day corpus, not a fixture

**Question.** `ddflow import` passes fourteen tests against a fixture and one
end-to-end scenario. Does it actually work on a project that has been running for four
hundred days — or does it only work on a file written by the person who wrote the
parser?

**Falsifier, stated first.** If the scan produces a queue whose ids match the ids the
project has been using in its own commit trailers, whose declared dependencies all
resolve, and which is unchanged by a second run, the importer works. Any one of those
failing kills it.

**Budget.** ≤2 hours, CPU only, on a corpus already on this machine.

**Corpus.** `run_nemo_run`, the repository ddflow lives in: `docs/todo.md` (35,078
lines, 4,799 checkboxes), `docs/todo/open/*.md` + `archive/*.md` (9 files),
`docs/lessons.md` (14,362 lines), `docs/RESEARCH.md` (7,534 lines), `docs/log/*.md`
(7 files), `docs/adr/` (5 files) and a 47-record OptMem store. Copied into a scratch
repository first — an import writes events, and writing them into the project being
read is not a test, it is an accident.

**Verdict: REFUTED, then fixed.** The importer did not work. It ran, it reported
success, and what it produced was unusable in eleven distinct ways. Every one of them
was invisible to the fixture tests, and all eleven now ship with a mutation-verified
regression test in `tests/test_import_real_project.py`.

### What the first run actually produced

```console
$ ddflow --repo /tmp/rnr-import import --max-tasks 5000
  790 phase(s):
    [ ] SESSION-DRIVERFIX-THE-DE   Session DRIVERFIX — the defects ...  docs/todo/open/DRIVERFIX.md:1
    [ ] 160A-THE-DRAFT-SCORER-SC   160.A — the draft scorer: score ...  docs/todo/open/PHASE160.md:22
  [no tasks at all, and no explanation next to them]
  358 research(s):
    [ ] R-sources-opened-not-snippet-cited  Sources (opened, not snippet-cited)
```

Measured, before and after:

| | before | after |
|---|---|---|
| declared dependencies that resolve to an imported id | **8 / 47** | **45 / 45** |
| colliding ids (silent data loss on fold) | **42 pairs** | **0** |
| research entries vs. fragments of entries | 358 | 72 |
| journal entries dated by when they happened | 0 | 1,727 |
| phases carrying the project's own id | ~0 | 218 / 314 |
| phases proposed with no task under them | 459 | 0 |

### The eleven, each with the mechanism

1. **`142.A` was not an id.** The id pattern required a leading *letter*, so every
   numeric-dotted phase id — the shape this project has used for two hundred phases —
   was slugged to `142A-THE-SCALING-LAW-ADV`. That alone broke 39 of the 47 declared
   dependencies: they pointed at `142.A`, which then existed nowhere. Unknown
   dependencies are treated as unmet *by design*, so the work imported permanently
   blocked while the import reported success.
2. **An id inside a spanning bold was not an id.** `- [ ] **DRIVERFIX.1 — step 1 picks
   …**` matched neither the delimited pattern (which needs `**` straight after the id)
   nor the bare one (anchored at `^`, blocked by the `**`).
3. **The child-prefix rename destroyed the ids it existed to recover.** `### 142.A` has
   children `142.1`, `142.2`, so the derived prefix is `142` — and taking it renamed the
   phase out from under every `Needs: 142.A` in the file. A heading that declares its
   own id now outranks the inference.
4. **Derived task ids voted in that inference**, and one of them made the vote
   unanimous-with-nobody: a checkbox with no id was given `<phase-slug>.<title-slug>`,
   which disagrees in its first component, so the common prefix came out empty and 96
   phases kept a prose slug they did not need.
5. **42 pairs of ids collided.** Two lessons whose titles agree in their first 32
   characters produce the same slug; the second `lesson.recorded` folds over the first,
   one disappears, and the import reports both as written.
6. **One research entry became five.** The section splitter matched `#{2,6}`, so the
   `###` sub-parts of an entry — "Sources", "Known gaps" — became siblings of it. Four
   of the five fragments meant nothing standing alone.
7. **`docs/adr/README.md` imported as a decision** whose body was a table of contents.
   Every ADR directory has one.
8. **Every journal entry was dated the day the import ran**, destroying the one thing a
   journal is for. The dates are in the headings (`… (2026-04-30)`, `2026-04-22 — …`)
   and in the filenames.
9. **The fold dropped every note field but three.** `seq`, `ident` and `source` went in
   and never came out — so the idempotency check read `ident` back as `""`, compared it
   against `""` and reported "already imported" for everything. A projection silently
   deciding a field does not exist.
10. **Each memory printed twice.** A memory is one line and has no title; the note
    writer concatenated its title with its body, and the title *was* an excerpt of the
    body.
11. **Over the cap, 790 phases were proposed with zero tasks** — which reads as "this
    project has 790 phases of work", the opposite of true — and the human preview listed
    five of the eight kinds, so a repository whose history is a journal and a memory
    store printed a header with nothing under it.

### What it now reports rather than resolves

Two findings the scan can make and must not act on, because either answer could be the
wrong one:

- **32 phase headings say `SHIPPED` over unticked checkboxes.** Independently
  corroborated: memory `#3` in the same repository's OptMem store reads *"docs/todo.md
  checkboxes DRIFT: many `[ ]` items are actually done"*. One-sided risk — if the
  heading is right, the queue is about to hand out finished work.
- **A dependency on an id nothing produced** stays unmet, deliberately, so a typo
  surfaces as blocked work rather than as work that starts early. But it is now named:
  "never offered" otherwise looks exactly like "nobody has got to it yet".

### The end state

```console
$ ddflow --repo /tmp/rnr-import import --max-tasks 5000 --apply
Imported: 4 decision, 1727 journal, 442 lesson, 47 memory, 314 phase, 72 research, 1170 task, 9 task_done

$ ddflow --repo /tmp/rnr-import recall "H200" --max-chars 20000
## PROMPT/NOTE  — the operator asked, or an agent recorded, something like this
  [s-imported-memory#n2] 2026-07-31 the agent noted:
      Hardware: 8x H200 GPUs on this box, usually idle. GPU-owed test items in
      docs/todo.md can actually be run; check nvidia-smi first ...

$ ddflow --repo /tmp/rnr-import doctor ; echo "exit=$?"
Healthy.
exit=0

$ ddflow --repo /tmp/rnr-import import --max-tasks 5000 ; echo "exit=$?"
Nothing to import.
exit=2
```

3,789 events, 1,484 items, scan in 0.65 s and apply in 6.4 s.

**The generalisable finding**, and the reason this belongs here rather than in a commit
message: *a parser tested only against a fixture is tested against its own author's
assumptions.* Fourteen fixture tests and one end-to-end scenario were all green while
39 of 47 dependencies were broken. The corpus was free, already on the machine, and
found eleven defects in under two hours. `tests/test_import_real_project.py` keeps both
halves — a miniature carrying every real shape, plus a `@pytest.mark.slow` canary that
runs the whole scan against the host project when there is one and skips otherwise.

### R11 addendum — what the three reviewers found, and how disjoint they were

The rulebook requires a cross-family critic, a subagent rubber-duck and roborev on every
non-trivial change, on the argument that they find different things. Measured on this one:

| Reviewer | Family | Findings that survived a probe | Overlap with the others |
|---|---|---|---|
| Own double-check | — | 4 (dead `outcome.py`, duplicate glob reads, an over-permissive date regex, the offer counting 3 of 7 sources) | 0 |
| roborev (`analyze duplication`, then `review <sha>`) | same | 5 (`cmd_merge` bypassing `_require_item`; the three-copy section scanner; the incremental-reimport note overwrite; `id_from_source` after a rejected id; the critical-path cycle guard) | 0 |
| Cross-family critic | different | 8 (`head()`'s two-pass fingerprint; the `rebuild` fingerprint ordering; `gate verify` certifying an already-red gate; its two-channel return; the silent primary-checkout fallback; the incremental-import false alarm; the cross-file annotation leak; the underscore stripped from ids) + 3 refuted by probe | 0 |

**Zero overlap across seventeen findings.** The two labelled `THEORETICAL` by the critic
were the two worth acting on — one was a real defect (`head()`), one was refuted by a
five-line AST probe and left behind a ratchet. The reviewer that found the most
consequential bug — `ddflow merge` landing work the operator had explicitly dropped —
found it while looking for something else entirely, which is the standing argument for
running the duplication pass even when nothing feels duplicated.

**The post-commit `roborev review <sha>` earned its place too**, and is the cheapest
reviewer here: run on the landed commit it found three more defects in ~4 minutes,
including the only one in this whole pass that loses user data on the module's own
advertised path — re-importing after a new journal entry silently overwrote the
previously imported notes in the search index, because the note numbering came from the
caller instead of from the fold. Reviewing the COMMIT, not the dirty tree, is what let
it see the two fixed session ids and the merge-not-replace handler together.

The critic's `CONFIRMED`/`THEORETICAL` labels were again not a ranking of importance.
Six of its eight real findings were labelled `THEORETICAL`, and every one of them was a
genuine defect with a deterministic regression test — including `rebuild`'s fingerprint
ordering, which permanently loses events from `recall` if the log then stops growing.
Its two refuted claims were also `THEORETICAL`, so the label carried no signal in either
direction; what separated them was a probe, in every case costing under ten minutes.

Its two other `THEORETICAL` claims died on inspection and are recorded because a
reject re-checked is worth as much as a claim confirmed (§Research-rules 4): `gate
verify` might dispatch to the `run` handler and silently execute the gate — refuted
because `tests/conftest.py::run_cli` spawns a real `python -m ddflow` subprocess, so
every one of the thirteen `gate verify` tests drives the actual parser; and `_csv`
losing its `(v or "")` guard — refuted because argparse never calls `type=` with `None`.

Its single `CONFIRMED` was the best finding of the pass and deserves its own line:
**`gate verify` — the anti-vacuous-pass check — was itself vacuous.** A gate already red
for an unrelated reason reports `failed` for every mutation, so every mutation reads as
"detected" and the gate is certified as able to fail when nothing has shown any such
thing. The check written to catch the class contained the class. It now runs a green
baseline first and refuses without one.

One more measurement, from the tail of the same run: **the critic's last chunk — the one
reviewing `importer.py`, the file this whole change is about — produced three of its
eight real findings**, and the run then hit its 45-minute wall clock at 17 of 18 chunks
(exit 124 = PARTIAL, recorded as such rather than as a pass). Chunked review is not
uniformly valuable across a diff and the most valuable chunk was near the end; a budget
that cuts it off loses exactly the part that was worth paying for. Next time: review the
changed MODULE first and the incidental diff after, or raise the budget to match the
diff (158 KB over 18 chunks here).

---

## R12 — how should an agent be allowed to change the workflow it works under?

**Question.** The operator asked for the workflow to be editable per project, "from the
agent (via MCP) or directly, as config" — and asked for the best way to be researched
rather than assumed.

**Falsifier, stated first.** If the MCP specification provides a mechanism that makes a
mutating tool safe on the CLIENT side — an annotation a client is obliged to honour —
then the right design is to declare it and rely on it. If it does not, the safety has
to be in the server and the design must not depend on the client at all.

**Budget.** ≤30 minutes, primary sources only.

**Verdict: REFUTED — the client-side mechanism exists and is explicitly untrustworthy.**

[MCP specification 2026-07-28, Server/Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools),
the one line that decided the design:

> For trust & safety and security, clients **MUST** consider tool annotations to be
> untrusted unless they come from trusted servers.

So `destructiveHint` and its siblings are a display hint, not a guard. Three further
lines from the same page shaped what was built:

> Servers **MUST**: Validate all tool inputs

> **Tool Execution Errors** contain actionable feedback that language models can use to
> self-correct and retry with adjusted parameters … reported in tool results with
> `isError: true`

> `outputSchema`: Optional JSON Schema defining expected output structure … Servers
> **MUST** provide structured results that conform to this schema.

**What that produced here:**

1. **Validation in the server, before the write.** Every workflow edit composes the
   change, validates the RESULT, then replaces the file atomically. Probed:

   ```console
   $ ddflow workflow pipeline task research,implment,merge
   no gate is defined for 'implment' (did you mean 'implement'?). Every item entering
   this pipeline would block on it forever.
   $ echo $?
   1
   ```

   The refusal is a tool *execution* error carrying the near miss — which is exactly
   the "actionable feedback a model can self-correct from" the spec describes, and not
   a protocol error, which the spec says models rarely recover from.

2. **The human stays in the loop through the tool DESCRIPTION, not an annotation.**
   Each mutating tool says it WRITES, says to ask the operator first, and offers
   `dry_run`. A test asserts all three, because an untrusted annotation cannot.

3. **`outputSchema` is not used by this server at all** — a real gap, filed rather than
   rushed: every tool returns text plus `_meta.exit`, and clients are told to validate
   structured results they are never given. Worth a pass of its own.

**Two defects this research surfaced before a line was written**, both of which the new
feature would have driven straight through, both now fixed with mutation-verified
regressions: a pipeline naming an undefined gate was a silent permanent block (B74),
and the config write paths validated the config already on disk rather than the merged
result (B75).

**The generalisable finding:** the spec question was not "what mechanism is available"
but "what is the mechanism *worth*". The answer was a single MUST-level sentence saying
not to trust it, and reading it turned a two-line change (declare the annotation) into
the correct one (validate server-side, describe the risk, offer a dry run).

## R13 — Multi-agent identity, load behaviour, and two rival workflow MCPs (2026-09-25)

**Budget declared up front:** ≤90 min total, ≤1 machine-hour, no GPU. Two strands
(rival-server reading, local concurrency audit) fanned out in parallel; synthesis and
every probe in one context.

### Claim 1 — "Several agents sharing one repository are distinguishable." REFUTED

*Mechanism.* Identity would come from the agent, so events could be attributed.
*Falsifier.* Two connections in one tree writing under one identity.
*Minimal decisive test.* Two `Server` objects on one repo, claim two items, read the
lease holders.

Run against the pre-fix code path (the `--agent` threading reverted), two `Server`
objects on one repo, each declaring a distinct name, each claiming one item:

```
$ python - <<'EOF'   # two Servers, identify as agent-a / agent-b, claim T1 / T2, fold
holders: {'T1': 'Monster3-ddf-ident', 'T2': 'Monster3-ddf-ident'}
EOF
```

Both claims landed under the tree-derived identity; the declared names went nowhere. With
the fix restored the same script prints `{'T1': 'agent-a', 'T2': 'agent-b'}`.

`ddflow/infra/log.py:96` already documented the
cause — *"a harness running several agents inside ONE tree must set [`DDFLOW_AGENT`] —
there is no signal that can distinguish them otherwise"* — and over MCP there was no way
to set it, because the env var is process-wide and the process is shared with nothing
that varies per connection. **REFUTED**, and the consequence is not cosmetic: reviewer
independence compares `agent != author`, so two subagents in one tree satisfy the review
gate by reviewing their own work. Fixed in B80 via `ddflow_identify`; mutation-verified
(dropping the threading turns three tests red).

### Claim 2 — "The fold is fast enough to re-run on every call." CONFIRMED

*Mechanism.* `Ctx.state()` is `fold(read_all())` with no snapshot, so cost is O(events)
per call; the question is the constant.
*Falsifier.* Superlinear growth, or a constant large enough to matter at realistic sizes.

```
  events  read_all ms    fold ms   total ms   us/event
     500          8.3        3.1       11.5       22.9
    2000         24.6        8.1       32.6       16.3
    5000         32.8       13.2       46.0        9.2
   10000         63.4       25.8       89.2        8.9
   20000        123.7       51.0      174.7        8.7
```

Clean linear, converging on **~8.7 µs/event**. 20,000 events — beyond what this
project's own 400-day corpus produced — costs ~175 ms per state-reading call.
**CONFIRMED** for realistic sizes; ~100k events would be ~0.9 s, which is where B86
starts to matter. Note the in-test figure of 212 µs/event at 200 events measures fixed
per-call overhead (server construction, config load, arg parsing), not the fold — a
reminder that a per-unit number taken at a small N mostly measures the constant.

### Claim 3 — "Concurrent agents can deadlock or lose appends." REFUTED under test

*Mechanism.* `EventLog.append` takes an exclusive `flock` and `flock` is per
open-file-description, so a lock taken twice in one process blocks against itself.
*Falsifier.* 12 concurrent agents completing with every event present and correctly
attributed.

12 agents × 15 writes through real JSON-RPC: 180/180 events present, zero repeated
Lamport values within an agent, zero misattributed events, readers never starved by
writers, and a read-modify-write loop under contention lost nothing. **REFUTED** (the
failure did not reproduce) — and the point of `tests/test_mcp_load.py` is that it stays
refuted. Per-agent shards are why: writers do not contend for a file, and the lock is
held only to allocate a clock value, never across a full read.

### Strand 2 — two existing workflow MCP servers

Sources opened: `https://mcpmarket.com/server/workflows-2` →
`https://raw.githubusercontent.com/dx-zero/mcpn/main/README.md`;
`https://raw.githubusercontent.com/pimzino/spec-workflow-mcp/main/README.md` and its
`docs/TOOLS-REFERENCE.md` (13 tools). The GitHub HTML pages rendered only file trees;
every substantive claim below comes from the raw files above.

**Adopting (filed, not built):** a human-approval gate with a review surface (B82);
named parameterised prompt macros with a bound tool subset (B83); a diff statistic on
gate evidence (B84).

**Declining, with the reason, so this is not re-researched:**

- **`toolMode: situational`** (mcpn) — the model picks freely from a bound tool set with
  no recorded ordering or rationale. That is strictly less reproducible than a gate
  pipeline whose every outcome is an event. Declined.
- **An un-enumerated tool surface** (mcpn) — everything is YAML-defined and dispatched
  through a generic wrapper, so there is no fixed tool list to parity-test. Fine for a
  personal prompt library; incompatible with the CLI/MCP parity discipline here.
- **A hard-sequenced Requirements→Design→Tasks phase order** (spec-workflow) — a good
  default, but this project's per-kind pipeline with explicit skip-with-reason is
  already the more flexible shape. Declined as a hard-coded order.
- **Steering docs** — mostly covered by the existing doc import plus project config.
  Declined as a separate concept.

**Verifier separate from searcher:** the strand-2 subagent reported the feature list;
the claims above were re-checked against the raw sources before being written down, and
the one thing it could not establish (mcpn's actual MCP tool names, as opposed to its
workflow names) is recorded here as unestablished rather than filled in.

### R13 addendum — what review did to these claims (2026-09-25)

Recorded because two of the three claims above were weaker than they read, and the
correction is the useful part.

**Claim 1 was right about the defect and wrong about the fix being complete.** The entry
quoted `log.py:96` — *"`DDFLOW_AGENT` overrides both"* — as authority. roborev checked
the code rather than the docstring: `default_agent_id` reads neither the env var nor
`[agent].id`, so that sentence was already false when it was written, and the fix built
on it inherited the error. Identity reached the argv path and not the typed one, and
`ddflow_identify` itself reported the wrong answer. **A docstring quoted as evidence is
not evidence** — the same lesson as `is_installed` promising three-valued detection
while returning `False` for a timeout, and it recurred here inside the commit that cited
it. Fixed in B88 by resolving the precedence once, in one function, called from both.

**Claim 3 ("concurrent agents can deadlock or lose appends" — REFUTED under test) rested
partly on a test that exercised nothing.** The read-modify-write arm derived its expected
count from the workers' own success counts, so zero successes compared equal to zero
survivors and reported "nothing was lost". Asserting failures were zero turned it red at
once: every write in that arm had been failing on a wrong argument name. The product
behaviour was fine — re-verified once the worker was fixed — but the REFUTED label was
supported by less evidence than it appeared to be. The claim stands; the evidence for it
is now real.

**Claim 2 (fold cost) survived review unchallenged**, and the measured table above is
unchanged.

**On reviewer availability.** roborev needed a repo-local `.roborev.toml` — ddflow has
been its own git repository since the extraction, so it fell through to the machine's
global `default_agent = codex`, which is not installed here. It reported that and
**exited 0**, which is the failure mode this project names most often, in the tool whose
job is to catch it. The config is now pinned in the repository, where a clone gets it.

## R14 — Could the MCP server run remotely, with no direct filesystem access? (2026-09-25)

**Operator question**, four parts: can a user ask what the workflow is and change it per
project; could the config live in a file the AGENT reads and writes on the server's
behalf; could the other stores work the same way so the server needs no filesystem; and
how is persistence implemented at all.

**Budget:** ≤60 min, no GPU. One fan-out audit of every filesystem/process touchpoint,
its citations re-verified by hand (§Research-rule 7), plus two spec fetches.

### Claim 1 — "MCP has a primitive for asking the client to read or write a file." REFUTED

*Mechanism.* If it existed, the server could stay stateless and delegate all I/O.
*Falsifier.* The spec's server→client feature list contains no file operation.

Opened `https://modelcontextprotocol.io/specification/2025-06-18/client` and
`.../client/elicitation`. There are exactly three server→client primitives:

| Method | Purpose |
|---|---|
| `roots/list` | Client tells the server which directories it may use. The URI **MUST** be a `file://` URI. |
| `sampling/createMessage` | Server asks the client to run an LLM completion. |
| `elicitation/create` | Server asks the **user** for structured input. |

`elicitation` is explicitly unusable as a transport: its `requestedSchema` is *"limited
to flat objects with primitive properties only"* — no nested structures, no arrays of
objects — and *"Servers **MUST NOT** request sensitive information."*

**REFUTED**, and `roots` settles the design intent: handing the server `file://` URIs
means the protocol's model is **server does the I/O, client scopes it**. The inverse is
not a supported pattern, and building it would be a private convention on tool results.

### Claim 2 — "Delegating the event log to the agent would still be correct." REFUTED, on design

The log is the sole source of truth: append-only, Lamport-ordered, `flock`+`fsync` per
append. If durability depends on the agent writing back what the server returned, then a
dropped write is **silent data loss in the source of truth**, the append-only guarantee
is gone, and the server cannot coordinate concurrent agents because it no longer knows
what landed. That is a strictly worse form of the prompt-level-trust problem B108 had
just removed from `companions_add`. **Config is different** — small, idempotent,
low-frequency, and a lost write is visible on the next read.

### Claim 3 — "The filesystem coupling is concentrated enough to put a backend behind." REFUTED as stated

*Falsifier.* An existing abstraction, or a small number of choke points.

```
$ grep -rn 'class.*Protocol\|ABC\|abstractmethod\|Backend' ddflow/ --include=*.py
(no output)
```

There is **no storage abstraction of any kind**. Two genuine choke points exist —
`EventLog` (`infra/log.py`) for events and `tomlcfg` (`infra/tomlcfg.py`) for TOML — and
most services go through them for those two artifacts. But `infra/store.py` is a third,
independent I/O implementation with its own atomic-publish, and at least seven modules
(`adopt`, `enforce`, `sessions`, `companions`, `gates`, `cli`, `views/markdown`) call
`Path.write_text`/`mkdir` directly for one-off writes. **CONFIRMED-partial:** concentrated
for events and config, scattered for everything else.

### Claim 4 — "Removing filesystem access is the hard part." REFUTED. Git is.

The audit surfaced something that changes the analysis, verified directly:

```
$ grep -rn 'merge=union' ddflow/
ddflow/surfaces/cli.py:216:    line = ".ddflow/events/*.jsonl merge=union\n"
$ cat <repo>/.gitattributes
.ddflow/events/*.jsonl merge=union
```

**Concurrent-branch safety is delegated to git's own union merge driver.** Two agents on
two branches append to their own shards; git unions them on merge with no conflict. That
is not a filesystem detail a backend can swap out — it is the conflict-resolution
strategy, and it only exists because the log is a file in the repo.

The rest compounds it. `fcntl.flock` (exactly two files: `log.py:197`,
`tomlcfg.py:114`) is POSIX single-machine. Gate and reviewer commands run with
`shell=True` against a local worktree (`gates.py:776`, `review.py:485`, `review.py:782`).
Every git call is `git -C <local-path>`. So: **remote-with-no-filesystem is not a
storage-backend problem, it is a "give up the git-integrated half" problem.**

### What already works, and what the honest split is

Docker is real and shipped — `Dockerfile:4` documents `-v "$PWD:/repo"`, and
`infra/container.py` handles detection, worktree relocation inside the mount, and
rewriting `localhost` reviewer endpoints to `host.docker.internal`. Worktree paths are
stored RELATIVE to the repo root (`worktree.py:341-377`) specifically so the log stays
valid when the repo is at `/repo` instead of its authoring path.

So **"containerised but bind-mounted" is supported today.** "Remote with no filesystem"
is not, and the useful shape is a split rather than a port:

* **Remote-capable** — the queue as pure data: items, dependencies, gates, lessons,
  decisions, research, recall. No git.
* **Local-required** — worktrees, merge, tree-fingerprint gate evidence, command gates.

That is a different product (a shared team queue with local execution agents), not the
same server reached over a wire. Worth building on purpose or not at all; filed as
B109–B111 rather than started, because the decision is the operator's.

### Answered, for the record

**Workflow explanation and per-project modification already exist.** `ddflow_workflow`
returns the live pipeline — every gate in order with its prompt, required/evidence/
reviewer flags, the phase pipeline, and every knob with its SOURCE (`[default]` vs
`[file]`), which is what makes "is this ddflow's choice or ours?" answerable.
`ddflow_help` adds topics. Changing it: `ddflow_workflow_pipeline`,
`ddflow_workflow_gate`, `ddflow_workflow_drop`, `ddflow_configure` — all with `dry_run`.

### R14 addendum 2 — re-checked against the 2026-07-28 spec via context7 (2026-09-26)

R14's central claim was made against `2025-06-18`. Re-queried through the context7 MCP
(`/websites/modelcontextprotocol_io_specification_2026-07-28`), which is what that
companion is for: the `research` gate asks for a falsifiable claim probed against
reality, and a model's memory of a protocol version is exactly the claim that is cheap to
check and often wrong.

**The claim HOLDS.** The newer spec still defines exactly three server→client primitives —
`roots/list`, `sampling/createMessage`, `elicitation/create` — and still no file-I/O
primitive. So "delegating reads and writes to the agent is not a supported pattern" is
true of current documentation, not only of the snapshot originally read. First claim this
session that survived re-checking unchanged.

**Two things that are new, and were not in the 2025-06-18 reading:**

* `elicitation/create` gained a **`mode`** parameter: `"form"` or **`"url"`**, the latter
  "for out-of-band interaction". Previously form-only, restricted to flat objects of
  primitives. Filed as B148, because it is a plausible mechanism for the human-approval
  gate and needs evaluating rather than assuming — the property to preserve is that no
  MCP tool RECORDS a human outcome, and an out-of-band approval might or might not.
* An **`InputRequiredResult`** / MRTR pattern, permitted only for `prompts/get`,
  `resources/read` and `tools/call`.

Also observed: `2026-07-28` carries `_meta.io.modelcontextprotocol/clientInfo` on each
tool call rather than only at the handshake. This does NOT revisit B80 — `clientInfo` is
still the harness, so every subagent of one harness reports the same string, which is the
exact collapse `ddflow_identify` exists to prevent. Recorded so nobody reads the per-call
availability as a solution to it.

## R-hx — Capability matrix for every coding agent ddflow adopts (2026-10-09)

**Question.** For each coding agent ddflow targets, what are the MCP config path and
shape, instruction files, lifecycle hooks, plugin mechanism, commands, skills and
subagent dirs, MCP `instructions`/prompts support, worktree dir and headless entry?
This is the data the harness descriptors (B-hx-descriptor) and the hook core
(B-hx-hook-core) are built from. Starting points: R41a0cc059c and R-agent-compat.

**Claim.** The hook systems of the agents that have one are expressible as a small number
of styles: (A) the Claude shape `{hooks:{Event:[{matcher,hooks:[{type:command}]}]}}` with
snake_case stdin carrying `session_id` (Claude, Codex, Qwen, Kimi, Devin, OpenHands,
Copilot PascalCase form; Grok Build is the same nesting and event names but camelCase
stdin `sessionId`, so it needs its own normalizer inside style A); (B) Gemini nesting with different event names
(Gemini, Tabnine); (C) own shape: Copilot camelCase, Cursor, Cline, Windsurf Cascade,
Antigravity, Goose, Crush; (D) in-process plugins with no command hooks (opencode, Kilo,
Amp). Session start context injection works natively on a subset only, so the fallback
ladder (hook > plugin > MCP first call > MCP instructions > instruction text) is needed.

**Falsifier.** An agent whose hook contract cannot be written as one of a style, an event
map, a stdin normalizer and an output emitter; or an agent with a command hook that
injects context at prompt time that the matrix marks NONE.

**Probe.** Raw vendor docs and repos fetched 2026-10-09 (URLs per agent below); local
probes where a binary was on PATH. `which claude codex gemini qwen opencode kilo
cursor-agent aider goose amp crush grok copilot cline` found only `claude` and `kilo`:

```console
$ claude --version
2.1.285 (Claude Code)
$ kilo --version
7.2.20
$ kilo mcp --help        # add, list|ls, auth, logout, debug  (add is an interactive wizard)
$ kilo agent --help      # create, list
$ kilo run --help        # --command --continue -s --fork -m --agent --format default|json -f --auto --dangerously-skip-permissions
$ kilo debug paths       # config ~/.config/kilo  data ~/.local/share/kilo  state ~/.local/state/kilo
$ claude mcp add --help  # -s local|user|project, -e K=V, --transport stdio|http|sse, `--` required
```

Legend: V = read on a primary page, S = source tree, NV = NOT VERIFIED, NONE = documented
as absent. A summarising fetch tool mis-stated one Claude fact (it denied
`CLAUDE_CODE_SESSION_ID`); the raw env-vars page shows it, so every row below comes from
raw markdown or source, not from a summary.

### Matrix 1 — MCP and instructions

| Agent | MCP project file (key) | MCP user file | stdio entry | Add by CLI | AGENTS.md | CLAUDE.md | Own instruction file |
|---|---|---|---|---|---|---|---|
| Claude Code | `.mcp.json` (`mcpServers`) | `~/.claude.json` | `{type,command,args,env}` | `claude mcp add -s ... -- cmd` | since 2.1.277, only if no CLAUDE.md (or `@AGENTS.md`) | native | `.claude/rules/*.md` |
| Copilot CLI | `.mcp.json`, `.github/mcp.json` (`mcpServers`) | `~/.copilot/mcp-config.json` | `{type:"local",command,args,env,tools}` | `copilot mcp add` | yes | yes | `.github/copilot-instructions.md`, `.github/instructions/**` |
| Copilot VS Code | `.vscode/mcp.json` (`servers`), `.mcp.json` | user profile `mcp.json` | `{command,args}` | UI only | yes (`chat.useAgentsMdFile`) | yes | same as CLI |
| Copilot cloud agent | repo settings UI (`mcpServers`, `tools`+`type` required) | NONE | `{type,command,args,env,tools}`; secrets `COPILOT_MCP_*` | UI | yes (nearest) | root only | same |
| Codex CLI | `.codex/config.toml` (`[mcp_servers.N]`, trusted projects) | `~/.codex/config.toml` | `command,args,env,env_vars,cwd` | `codex mcp add` | native, override file | only via `project_doc_fallback_filenames` | `AGENTS.override.md` |
| Gemini CLI | `.gemini/settings.json` (`mcpServers`) | `~/.gemini/settings.json` | `{command,args,env,cwd,timeout,trust}` | `gemini mcp add` | only via `context.fileName` | no | `GEMINI.md` |
| Qwen Code | `.qwen/settings.json` (`mcpServers`) | `~/.qwen/settings.json` | `{command,args,cwd,env,timeout}` | `qwen mcp add` | yes | NV | `QWEN.md`, `.qwen/rules/` |
| opencode | `opencode.json` (`mcp`) | `~/.config/opencode/opencode.json` | `{type:"local",command:[...],environment}` | `opencode mcp add` (wizard) | primary | fallback if no AGENTS.md | `instructions:[globs]` |
| Kilo CLI/VS Code | `kilo.json`, `.kilo/kilo.json` (`mcp`) | `~/.config/kilo/kilo.json` | opencode shape | `kilo mcp add` (wizard, V by probe) | primary | also supported | `.kilo/rules` |
| Cursor | `.cursor/mcp.json` (`mcpServers`) | `~/.cursor/mcp.json` | `{command,args,env,envFile}` | `agent mcp` has no add | yes (root, nested) | CLI yes, IDE NV | `.cursor/rules/*.mdc` |
| Windsurf (Devin Desktop) Cascade | NV | `~/.config/devin/mcp_config.json` | `{command,args,env}` | edit file | yes | no | `.devin/rules`, `.windsurf/rules` |
| Devin CLI / Devin Local | `.devin/mcp_config.json` (`mcpServers`) | `~/.config/devin/mcp_config.json` | `{command,args,env}` | `devin mcp add -s` | yes | yes | `.devin/rules/*.md` |
| Antigravity | `.agents/mcp_config.json` (`mcpServers`) | `~/.gemini/config/mcp_config.json` | `{command,args,env,cwd}`; remote `serverUrl` | `/mcp` overlay only | yes (or GEMINI.md) | no (stated) | `.agents/rules/*.md` |
| Cline | NV (none documented) | CLI `~/.cline/data/settings/cline_mcp_settings.json` | `{command,args,env,disabled,autoApprove}` | `cline mcp add` | yes | no | `.clinerules/` |
| Roo Code (shut down 2026-05-15) | `.roo/mcp.json` (`mcpServers`) | `mcp_settings.json` | `{command,args,cwd,env,alwaysAllow}` | NONE | yes | no | `.roo/rules/` |
| Aider | NONE | NONE | NONE | NONE | not read | not read | `--read` / `.aider.conf.yml read:` |
| Goose | NV | `~/.config/goose/config.yaml` (`extensions:`) | YAML `{cmd,args,envs,type:stdio}` | `goose configure`, `--with-extension` | yes | no (`CONTEXT_FILE_NAMES`) | `.goosehints` |
| Amp | `.amp/settings.json` (`amp.mcpServers`; needs `amp mcp approve`) | `~/.config/amp/settings.json` | `{command,args,env}` | `amp mcp add` | yes | fallback | `AGENT.md` |
| Crush | `.crush.json`/`crush.json` (`mcp`), `crushrc` builtin | `~/.config/crush/` | JSON field names NV | crushrc `mcp add` | yes | yes | `CRUSH.md` |
| Grok Build | `.grok/config.toml` (`[mcp_servers.N]`), `.mcp.json` | `~/.grok/config.toml` | `command,args,env,enabled,startup_timeout_sec` | `grok mcp add -- cmd` | yes | yes | `.grok/rules/` |
| grok-cli (superagent-ai) | NONE (user file only) | `~/.grok/user-settings.json` `mcp.servers[]` | `{id,label,enabled,transport,command,args,env}` | `/mcps` TUI | yes | NV | NONE |
| Kimi Code | NV | `~/.kimi/mcp.json` | `{command,args,env}` | `kimi mcp add` | yes | NV | NONE |
| GLM (ZCode) | NV | NV | NV | NV | NV | NV | NV |
| Qodo / Tabnine | Tabnine `.tabnine/agent/settings.json`; Qodo NV | `~/.tabnine/agent/settings.json` | `{command,args,env,cwd}` | `tabnine mcp add` | Tabnine via `context.fileName` | NV | `TABNINE.md` |
| OpenHands | NV | `~/.openhands/mcp.json` | `{command,args,env}` | `openhands mcp add` | yes | yes | skills in `.agents/skills` |
| Replit | UI, remote HTTPS only | NONE | NONE | UI | NV | NV | `replit.md` |
| Cody | NV | NV | NV | NV | NV | NV | `.sourcegraph/*.rule.md` (NV) |

### Matrix 2 — hooks

| Agent | Hooks? | Config file | Events usable by a bridge | Session id | Context injection | Block |
|---|---|---|---|---|---|---|
| Claude Code | yes, 33 events | `.claude/settings.json`, `settings.local.json`, plugin `hooks/hooks.json` | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop, SessionEnd | stdin `session_id`; env `CLAUDE_CODE_SESSION_ID` | stdout text or `hookSpecificOutput.additionalContext` on SessionStart, UserPromptSubmit (10k cap) | exit 2, `decision:block` |
| Codex CLI | yes, 12 events | `.codex/hooks.json`, `.codex/config.toml`; trust review needed | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop, SessionEnd | stdin `session_id` | stdout text / `additionalContext` on SessionStart, UserPromptSubmit | exit 2, `decision`, `permissionDecision` |
| Gemini CLI | yes, 11 events | `hooks` in settings.json | SessionStart, BeforeAgent, BeforeTool, AfterTool, PreCompress, SessionEnd | stdin `session_id`; env `GEMINI_SESSION_ID` | JSON only `hookSpecificOutput.additionalContext` (SessionStart, BeforeAgent) | exit 2, `decision` |
| Copilot CLI | yes, 14 events (camelCase; PascalCase switches to Claude payload) | `.github/hooks/*.json`, `~/.copilot/hooks/` | sessionStart, userPromptSubmitted, preToolUse, postToolUse, preCompact, agentStop, sessionEnd | stdin `sessionId`; no env | only sessionStart, subagentStart, postToolUse; userPromptSubmitted output DROPPED | preToolUse JSON (fail-closed), agentStop `decision` |
| Copilot VS Code | yes (Preview), 8 events, no SessionEnd | `.github/hooks/*.json`, `.claude/settings.json` (flag) | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop | stdin `session_id` optional | `additionalContext` on PreToolUse, PostToolUse, SessionStart | exit 2 |
| Copilot cloud agent | yes | `.github/hooks/*.json` default branch | sessionStart, userPromptSubmitted, preToolUse, postToolUse, agentStop, sessionEnd | NV | sessionStart | preToolUse (ask = deny) |
| Qwen Code | yes, 24 events | `hooks` in `.qwen/settings.json` (trusted folder) | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop, SessionEnd | stdin `session_id`; env `QWEN_CODE_SESSION_ID` (process) | stdout text SessionStart; `additionalContext` UserPromptSubmit | exit 2, `decision` |
| opencode | NO command hooks; JS plugin | `.opencode/plugins/*.ts`, `plugin:[]` | `event` (session.created/idle/compacted), `chat.message`, `tool.execute.before/after`, `shell.env` | `sessionID` argument | `experimental.chat.system.transform` (`output.system`), `chat.message` parts | throw in `tool.execute.before` |
| Kilo | NO command hooks; same plugin API | `.kilo/plugin/`, `plugin:[]` | as opencode | `sessionID` | as opencode | throw |
| Cursor | yes, 21 events (camelCase) | `.cursor/hooks.json`; also reads `.claude/settings.json` | sessionStart, beforeSubmitPrompt, preToolUse, postToolUse, preCompact, stop, sessionEnd | stdin `conversation_id`; `session_id` on start/end | sessionStart `additional_context`, postToolUse; beforeSubmitPrompt cannot; CLI coverage NV (forum: shell hooks only) | exit 2, `permission:deny` |
| Windsurf Cascade | yes, 12 events, no session/compact/stop | `.devin/hooks.json`, `~/.codeium/windsurf/hooks.json` | pre_user_prompt, pre_run_command, post_cascade_response | stdin `trajectory_id` | NONE | exit 2 on pre_* only |
| Devin CLI / Local | yes, 8 events (Claude shape) | `.devin/hooks.v1.json`, `.devin/config.json`, `.claude/settings.json` | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SessionEnd | stdin `session_id` | `hookSpecificOutput.additionalContext` on SessionStart, UserPromptSubmit, PostToolUse | exit 2, `decision` |
| Antigravity | yes, 5 events | `.agents/hooks.json`, `~/.gemini/config/hooks.json` | PreToolUse, PostToolUse, PreInvocation, PostInvocation, Stop | stdin `conversationId` | PreInvocation `injectSteps`; no SessionStart/prompt event | `decision` field |
| Cline (CLI) | yes, file hooks (extension and CLI differ) | `.cline/hooks/<Name>`, `.clinerules/hooks/<Name>` | prompt_submit, tool_call, tool_result, agent_start, agent_end | stdin `taskId` | `contextModification` (next request, 50KB) | `cancel:true` |
| Roo Code | PreToolUse/PostToolUse existed; config NV | NV | NV | NV | NV | NV |
| Aider | NONE | - | - | - | - | - |
| Goose | yes, 12 events (Open Plugins) | `<plugin>/hooks/hooks.json` under `.agents/plugins/` | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SessionEnd | stdin `session_id` | NOT SUPPORTED (banner unverified) | exit 2, `decision` on PreToolUse, Stop |
| Amp | NO command hooks; TS plugin | `.amp/plugins/` | `session.start`, `agent.start`, `tool.call`, `tool.result`, `agent.end` | `event.thread.id` | `agent.start` returns `{message}` | `tool.call` reject |
| Crush | yes, PreToolUse only | `hooks` in `crush.json` | PreToolUse | stdin `session_id`; env `CRUSH_SESSION_ID` | `context` field in stdout JSON | exit 2, `decision:deny` |
| Grok Build | yes, 15 events (Claude + Cursor names) | `.grok/hooks/*.json`, `.claude/settings.json`, `~/.grok/config.toml`; needs folder trust | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop, SessionEnd | stdin `sessionId`; env `GROK_SESSION_ID` | `additionalContext` on PreToolUse/PostToolUse only; UserPromptSubmit and SessionStart stdout DISCARDED | exit 2, `decision` |
| grok-cli | yes, 17 events, user-level file only | `~/.grok/user-settings.json` | PreToolUse acted on; others fire-and-forget | stdin `session_id` optional | not wired into the model (source) | exit 2 on PreToolUse |
| Kimi Code | yes, 13 events (beta) | `~/.kimi/config.toml` | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop, SessionEnd | stdin `session_id` | stdout added to context (exit 0) | exit 2 |
| Tabnine | yes, Gemini-style 11 events; CLI in maintenance | `hooks` in settings.json | SessionStart, BeforeAgent, BeforeTool, AfterTool, PreCompress, SessionEnd | stdin `session_id` implied | `additionalContext` | exit 2, deny |
| OpenHands | yes, 6 events | `.openhands/hooks.json` | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SessionEnd | stdin `session_id` | `additionalContext` | exit 2, `decision` |
| GLM, Qodo, Replit, Cody | NV (treat as NONE for planning) | - | - | - | - | - |

### Matrix 3 — plugins, commands, skills, subagents, MCP instructions and prompts, worktree, headless

| Agent | Plugin | Commands | Skills dir | Subagents dir | MCP `instructions` shown | MCP prompts as commands | Worktree | Headless |
|---|---|---|---|---|---|---|---|---|
| Claude Code | `.claude-plugin/plugin.json`; skills, commands, agents, hooks, MCP | `.claude/commands/*.md` | `.claude/skills` | `.claude/agents/*.md` | yes (2048 char cap) | yes `/mcp__srv__prompt` | `claude -w` -> `.claude/worktrees/<name>` | `claude -p`, `--session-id`, `--output-format json` |
| Copilot CLI | `copilot plugin ...`; `plugin.json` | `.claude/commands`, skills | `.github/skills`, `.agents/skills`, `.claude/skills` | `.github/agents/*.agent.md` | only allowlisted unless flag | NV | `copilot -w` -> `<repo>.worktrees/` | `copilot -p`, `--output-format json` |
| Copilot VS Code | agent plugins (Claude/Copilot formats) | `.github/prompts/*.prompt.md` | `.github/skills`, `.claude/skills` | `.github/agents`, `.claude/agents` | NV | yes `/server.prompt` | Agents window only | NV |
| Copilot cloud agent | NV | NONE | `.github/skills` | `.github/agents` | NV (tools only; prompts unsupported) | no | NONE (ephemeral `/workspace`) | always; `COPILOT_AGENT_PROMPT` |
| Codex CLI | `plugin.json`; skills + MCP (+ hooks) | `~/.codex/prompts` (deprecated) | `.agents/skills` | `.codex/agents/*.toml` | yes | NV | NONE in CLI (app only) | `codex exec`, `--json` thread id |
| Gemini CLI | extensions `gemini-extension.json`; MCP, commands, hooks, skills, agents | `.gemini/commands/*.toml` | `.gemini/skills`, `.agents/skills` | `.gemini/agents/*.md` | yes | yes `/prompt` | `gemini -w` (experimental) -> `.gemini/worktrees/` | `gemini -p`, `stream-json` init has id |
| Qwen Code | extensions `qwen-extension.json`; converts Claude/Gemini | `.qwen/commands/*.md` | `.qwen/skills` | `.qwen/agents/*.md` | yes (source) | yes | `qwen --worktree` -> `.qwen/worktrees/` | `qwen -p` |
| opencode | JS plugins (tools, hooks, config) | `.opencode/commands/*.md` | `.opencode/skills`, `.claude/skills`, `.agents/skills` | `.opencode/agents/*.md` | yes (source) | yes | experimental service, no flag | `opencode run` |
| Kilo | npm plugins; `kilo plugin` | `.kilo/command/*.md` | `.kilo/skills`, `~/.claude/skills` (V by probe) | `.kilo/agent/*.md` | yes (source) | yes (source) | VS Code Agent Manager: `.kilo/worktrees/`; no CLI flag | `kilo run --auto --format json` |
| Cursor | `.cursor-plugin/plugin.json` or Agent Plugins | `.cursor/commands` (legacy) | `.cursor/skills`, `.agents/skills` | `.cursor/agents`, `.claude/agents` | NV | NV | `agent -w` -> `~/.cursor/worktrees/<repo>/<name>` | `agent -p --output-format json` |
| Windsurf / Devin Local | Devin CLI `devin plugins`; Cascade NONE | Cascade `.devin/workflows/*.md`; CLI skills | `.devin/skills`, `.windsurf/skills`, `.agents/skills` | CLI `.devin/agents/*.md` | NV | CLI yes | Cascade `~/.windsurf/worktrees/<repo>/` | `devin -p` |
| Antigravity | `plugin.json`; MCP, hooks, skills, agents, rules | workflows (deprecated 2026-10-19), skills | `.agents/skills` | `.agents/agents/*.md` | NV | NV | IDE only, dir NV | `agy -p`, `conversation_id` in JSON |
| Cline | SDK/CLI plugins only | workflows `.cline/workflows` | `.cline/skills`, `.agents/skills`, `.claude/skills` | `.cline/agents` (CLI) | NV | NV | `cline --worktree` -> `~/.cline/worktrees/` | `cline --json`, `--id` |
| Roo Code | NONE | `.roo/commands/*.md` | `.roo/skills` | custom modes `.roomodes` | NV | NV | settings UI | NV |
| Aider | NONE | built-in only | NONE | NONE | n/a | n/a | NONE | `aider -m ... --yes-always` |
| Goose | `goose plugin install`; skills + hooks | recipes via `slash_commands` | `.agents/skills`, `.goose/skills`, `.claude/skills` | recipes, no dir | likely yes (source) | NV | NONE | `goose run -t`, `--output-format json`; env `AGENT_SESSION_ID` |
| Amp | TS plugin `.amp/plugins/` | `.agents/commands` (removal planned) | `.agents/skills` | automatic only | NV | NV | runners only | `amp -x`; no session env |
| Crush | NONE found | `.crush/commands/*.md` (source) | `.crush/skills`, `.claude/skills`, `.agents/skills` | NONE | yes (`<mcp-instructions>`) | palette entries | NONE | `crush run` |
| Grok Build | plugins: skills, commands, agents, hooks, MCP | `.grok/commands/*.md` | `.grok/skills`, `.agents/skills`, `.claude/skills` | `.grok/agents/*.md` | NV | NV | `grok -w` -> `~/.grok/worktrees/<repo>/<name>` | `grok -p`, `--output-format json`, `--yolo`, `--trust` |
| grok-cli | NONE | NV | `.agents/skills` | `subAgents` in user settings | NV | NV | NONE | `grok -p`, `--format json` |
| Kimi / Devin CLI / Tabnine / OpenHands | see sheets | - | Devin: `.devin/skills` | Devin: `.devin/agents` | NV | Devin: yes | NONE found | Kimi `--print`; Devin `-p`; OpenHands `--headless` |

### What this changes for the design

1. **Session start context.** Native hook injection exists for Claude, Codex, Gemini,
   Qwen, Devin, Kimi, OpenHands, Tabnine, Cursor (`additional_context`), Copilot CLI
   (sessionStart JSON), Copilot VS Code (`additionalContext` on SessionStart) and the
   Copilot cloud agent (sessionStart). It does not exist for Grok Build (SessionStart stdout is
   discarded), Windsurf Cascade, Crush, Goose, Roo, Aider; opencode, Kilo and Amp need a
   plugin. The ladder must therefore reach MCP `instructions` (Claude, Codex, Gemini,
   Qwen, Crush, opencode, Kilo) and then instruction text.
2. **Prompt-time injection.** Works on Claude, Codex, Gemini and Tabnine (BeforeAgent),
   Qwen, Devin, Kimi, OpenHands, Cline and Amp's plugin. Copilot CLI drops the output; Cursor's
   beforeSubmitPrompt and Grok's UserPromptSubmit cannot inject. Prompt CAPTURE (the
   hook only reads stdin) is still possible wherever a prompt event exists.
3. **Identity.** Most agents give a session id on hook stdin; env vars exist only for
   Claude (`CLAUDE_CODE_SESSION_ID`), Gemini (hooks), Qwen (`QWEN_CODE_SESSION_ID`),
   Goose (`AGENT_SESSION_ID`), Crush and Grok (hooks only). Copilot CLI, Codex, Cursor,
   opencode and Kilo expose no env var, so identity there rests on `ddflow_identify`.
4. **MCP shape.** Four shapes cover most file-based agents: `mcpServers` (the majority),
   `servers` (VS Code), `mcp` with array `command` and `environment` (opencode, Kilo),
   TOML `[mcp_servers.N]` (Codex, Grok Build); plus Goose YAML `extensions`, Amp
   `amp.mcpServers`, Crush `mcp`, and grok-cli's `mcp.servers[]` array of `{id,label,transport,...}`. Matches the shapes `adopt.AGENT_TARGETS` already has,
   except `amp`, `goose`, `crush`, `grok` which it lacks.
5. **Trust gates.** Project config is inert until trusted: Claude `.mcp.json` approval,
   Codex hooks hash review and trusted projects, Grok folder trust, Amp `mcp approve`,
   Qwen trusted folders, Copilot CLI folder trust, Devin workspace trust. Headless
   conformance runs must pass the trust flag or they report `unavailable`.
6. **Facts that changed since the older notes.** Windsurf is now Devin Desktop (Cascade
   plus Devin Local sharing a harness with Devin CLI); Roo Code was shut down 2026-05-15
   (fork ZooCode, not researched); sst/opencode moved to anomalyco/opencode; block/goose
   moved to aaif-goose/goose; Crush's primary config is a `crushrc` script with JSON
   deprecated; Antigravity workflows stop 2026-10-19; Amp deprecates custom commands;
   Tabnine CLI is in maintenance until 2026-12-31; Cody Free/Pro are discontinued.

### Still NOT VERIFIED (open probes; none of these CLIs is on this machine)

Copilot session-id env var; Codex session-id env var and CLI worktree; Cursor CLI hook
coverage and IDE CLAUDE.md; Cline project-level MCP file; Goose project MCP file and
hook context injection; Amp hook-free session id; Crush stdio JSON field names; Grok
Build UserPromptSubmit/SessionStart payload field names and MCP instructions; GLM
(ZCode) entirely (docs at zcode.z.ai not fetched); Qodo, Replit and Cody hooks; Roo hook
format; MCP `instructions` for Cursor, Windsurf, Antigravity, Cline, Amp. B-ac-matrix
does the live probes; the descriptor must carry NV as an explicit value, not a guess.

### Sources (opened 2026-10-09)

Claude: code.claude.com/docs/en/{hooks,mcp,memory,plugins-reference,skills,sub-agents,worktrees,headless,env-vars}.md.
Copilot: docs.github.com (github/docs content: hooks-reference, add-mcp-servers,
add-custom-instructions, cli-plugin-reference, configure-mcp-servers) and
microsoft/vscode-docs (agent-customization, DateApproved 2026-10-07).
Codex: developers.openai.com/codex/{mcp,hooks,plugins,skills,subagents,noninteractive,custom-prompts}.md and
guides/agents-md.md. Gemini: github.com/google-gemini/gemini-cli docs/{hooks,tools/mcp-server,cli/*,extensions/reference}.md.
Qwen: github.com/QwenLM/qwen-code docs/users and source. opencode:
github.com/anomalyco/opencode packages/web docs and packages/plugin. Kilo:
github.com/Kilo-Org/kilocode packages/kilo-docs and local `kilo` 7.2.20. Cursor:
cursor.com/docs/{mcp,hooks,rules,skills,subagents,cli,configuration/worktrees}.
Devin/Windsurf: docs.devin.ai/{desktop/cascade,cli}/*. Antigravity:
antigravity.google/docs/{mcp,rules,hooks,plugins,skills,subagents,cli/headless}.
Cline: docs.cline.bot and github.com/cline/cline (.clinerules/hooks/README.md, sdk/examples/hooks).
Roo: docs.roocode.com. Aider: aider.chat/docs. Goose: goose-docs.ai. Amp: ampcode.com/docs.
Crush: github.com/charmbracelet/crush docs/ and source. Grok Build:
github.com/xai-org/grok-build user guide, docs.x.ai/build/*, x.ai/news/grok-build-cli.
grok-cli: github.com/superagent-ai/grok-cli source. Kimi: moonshotai.github.io/kimi-cli.
Tabnine: docs.tabnine.com. OpenHands: docs.openhands.dev. Replit: docs.replit.com.

**Verdict: CONFIRMED** for the claim's structure (every documented hook contract fits
style + event map + normalizer + emitter; none contradicted it), with the NV list above
as the explicitly unverified remainder. The falsifier was looked for and not found:
the closest case is Goose (hook runs but context injection is unsupported), which is
the ladder's job, not a contract mismatch.
