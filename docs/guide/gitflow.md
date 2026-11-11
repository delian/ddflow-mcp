# Gitflow, pull requests and tags

Branches, pull requests, version tags, release lines and environment branches. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

## Gitflow, pull requests and version tags

Two independent axes in `[flow]`, because teams combine them freely:

| | `integration = "merge"` (default) | `integration = "pr"` |
|---|---|---|
| **`model = "trunk"`** (default) | ddflow as it always was | GitHub flow |
| **`model = "gitflow"`** | gitflow, merged locally | gitflow behind approvals |

Which workflow that makes, and what is not covered:

| Workflow | Supported | Configure |
|---|---|---|
| Trunk-based development | yes | `model = "trunk"`, `integration = "merge"` — short-lived task branches landed straight on trunk |
| Trunk-based with reviews / GitHub flow | yes | `model = "trunk"`, `integration = "pr"` |
| Gitflow (develop, feature/bugfix/hotfix, release branches, tags) | yes | `model = "gitflow"`, either integration |
| Several major trunks, fixes carried between them | yes | `[flow.lines]` + `port_strategy` — see [below](#several-release-lines-fixes-to-older-majors) |
| GitLab flow with environment branches (main → pre-production → production) | yes | `[flow].environments` — see [below](#environment-branches-promoting-downstream) |
| GitLab flow with release branches (upstream first, cherry-picked into stable branches) | yes | `[flow.lines]` + `port_strategy = "cherry-pick"` |

Each of these is a [workflow choice](#workflow-choices-asked-recorded-defaulted-on-the-record):
the operator sets it, or an agent records it, and when nobody does the default is applied
at first use and followed from then on.

**The agent's loop does not change.** `next` → `claim` → work → gates → `merge`. What
`merge` *means* changes with the repository's policy:

* **`merge` in PR mode pushes the branch and opens (or updates) a pull/merge request**,
  then releases the lease and parks the item in **REVIEW**. The agent is free at once and
  takes the next task — nobody waits for a human. It refuses to open a request for work
  whose own pipeline is unfinished: a reviewer's time is the scarce resource, and a
  refusal after the merge could no longer stop anything.
* **`pr sync`** turns what reviewers did back into queue state. `next` runs it for you
  while anything is in review (`sync_on_next`):

  | the forge says | ddflow does |
  |---|---|
  | merged | passes the `merge` gate on the forge's evidence (URL, merge sha), completes the item, retargets anything stacked on it, removes its tree |
  | changes requested | returns the item to the queue **with the review text** (bodies and line comments); `brief` leads with it; a re-claim resumes the same tree and a re-`merge` updates the same request |
  | closed | parks it for a person — a "no" is not something to retry |
  | approved, checks green | merges it (`pr_merge = "on_approval"`, pinned to the approved head) |
  | enqueued in a **merge queue** (GitHub; `gh pr merge` on a queue-protected branch enqueues) | stays in review and is said to be queued: `queued` with its position, `pr status` shows `[merge queue #N]`, and it is not asked to merge again. A request that was queued and no longer is, still open, was **ejected** (`queue_ejected`); the queue landing it is an ordinary `merged` |

* **Review threads are data.** Feedback still arrives as text in `brief`, but each line
  conversation is also a record with the forge's own id: `pr threads T1` lists them live
  (id, `file:line`, author, the opening comment, resolved or not), and `pr threads T1
  --thread ID --reply "renamed in abc123" --resolve` answers one and marks it resolved —
  so the reviewer sees which comments were addressed instead of re-reading the diff.
  GitHub (GraphQL review threads) and GitLab (resolvable discussions); the write is the
  forge's own, made as the operator who is logged in. MCP: `ddflow_pr_threads`.

* **Stacking keeps work moving through review.** While `T1` waits in review, a task that
  needs it may start **on top of `T1`'s branch** (`stack = true`); its request targets
  `T1`'s branch and is retargeted to the real base when `T1` merges. ddflow never merges
  a stacked request first — that would land it unreviewed inside `T1`'s merge. A task
  depending on two unmerged branches waits: one branch cannot sit on two.
* **Who presses merge** is `pr_merge`: `on_approval` (default — a person's approval is
  still required, and branch protection still applies), `auto` (ask the forge to
  auto-merge when its own rules are met), or `human`.

**Gitflow.** Tasks fork from `develop` as `feature/` or `bugfix/` branches (by tag); a
task tagged `hotfix` forks from production and lands on production **and** develop.
Merges never switch a checkout: a target that is not checked out is merged in a throwaway
worktree, and one checked out in someone else's tree is refused.

**Versions.** `version show` reads the highest `v1.2.3` tag reachable from the release
branch and computes the next version from Conventional Commits (`feat` → minor, `fix` →
patch, `!`/`BREAKING CHANGE` → major; below 1.0.0 a breaking change bumps minor) and from
the tags of items finished since (`breaking`, `feature`, `bug`, `hotfix`). `version cut`
tags it — annotated, with generated release notes. Under gitflow it cuts `release/X` from
develop, merges it into production, tags it and merges the tag back into develop; in PR
mode it opens the release request instead, and `pr sync` tags the merge commit once a
person merges it and opens the back-merge request.

**A hotfix's back-merge is tracked.** Under gitflow + pull requests the hotfix item
completes once production has the fix, but the production → develop request it opens is
recorded (`pr status` lists it under `back_merges`) and every later `pr sync` re-asks the
forge until it merges ("back_merge: merged into develop") or is closed without merging,
which is refused loudly: develop would not have a fix production already ships.

**Version files.** `[flow.version_files]` maps a path to a regex with exactly one capture
group, the version text — `'pyproject.toml' = '^version = "([^"]*)"$'` — and `version cut`
replaces that group with the new version (no tag prefix) and commits it as `chore: bump
version to X`, so the tag's commit says what the tag says. The commit is on the branch the
tag names: the release branch under gitflow (in pr mode it travels in the release request),
the release source for a trunk or maintenance cut. Everything is checked against that
source first and a missing file, a pattern matching nothing or matching twice refuses the
whole cut (exit 3) rather than guessing which line is the version; a trunk cut with
`integration = "pr"` is refused too, because the bump would have no request to travel in.

**Changelog with the cut.** `version cut --changelog` also writes the new version's
section into `CHANGELOG.md` (the path of `[export.changelog]`, default `CHANGELOG.md`)
through [`ddflow export changelog`](customisation.md#exporting-documents): the Unreleased entries
become `## [x.y.z] - date` with the right compare link, and an empty Unreleased remains. The
file is committed on the branch the tag names, so the tag's commit holds it; in gitflow with
pull requests it is a commit on the release branch, part of the release request, and the
request body is the same section (the tag, cut later by `pr sync`, carries its usual
message); otherwise the tag message is the section. Mode `whole` (the default) rewrites the
file; mode `region` rewrites only the marked region and puts the new section below it,
updating the `[Unreleased]:` link line when the file keeps one. Nothing is written without
the flag (the tag message is then the commit-derived notes as before), `--dry-run` shows what
would be written, a hand-edited file or uncommitted changes to it are refused (exit 3) unless
`--force`, and a git or log failure is exit 2. Trunk or maintenance cuts with pull requests
refuse `--changelog` (no request to carry the commit). The MCP tool takes `changelog` and
`force`. Bumping version files is a separate task.

An item counts as shipped on a line when its landing merge commit **or the branch it merged**
(the merge's second parent) is reachable from it: a hotfix landed on production reaches
`develop` by the back-merge as that branch, with production's merge commit nowhere in it. A
squash or fast-forward landing is the one commit it made. A squash landing has no merge
parent to follow, so it is also found on another line by its change: a commit there with the
same patch-id (a cherry-pick, or the back-merge squash), or the `back-merge <id> into ...`
commit ddflow wrote for it -- so a hotfix squashed onto production counts as shipped on
`develop` once it is back-merged. Only the newest 1000 commits of the line are searched.

ddflow holds no token: it drives `gh` or `glab`, logged in as the operator, so every
permission question is answered by the forge. A forge that cannot be reached is exit 2 —
"could not ask" is never reported as "nothing changed".

```toml
[flow]
model = "gitflow"          # or "trunk"
integration = "pr"         # or "merge"
pr_merge = "on_approval"   # or "auto" | "human"
pr_reviewers = ["alice"]
```

The research behind this is [RESEARCH R16](../../docs/RESEARCH.md).

### Several release lines: fixes to older majors

Projects that keep older majors alive — `main` is 3.x while 2.x and 1.x still get fixes —
declare them as **release lines**, oldest first. The newest line is always the *current*
one and follows `model` as above; a maintenance line lands straight on its branch.

```toml
[flow]
current_line = "3"
port_strategy = "cherry-pick"      # or "forward-merge" (the default)

[flow.lines]                        # oldest first
"1" = "maint/1.x"
"2" = "maint/2.x"
```

* **An item belongs to a line**: `task add T --line 2`, or `phase add P --line 2` and
  every task in it inherits it. A line that does not exist is refused, never read as
  "the current one".
* **A fix for several lines** is one command: `task add FIX --lines 1,2,3`. ddflow writes
  it where the strategy says and generates a **port** task `FIX@<line>` for each other
  line — an ordinary task with its own branch, gates and merge or pull request. A port
  starts only once what it carries has *landed* (review is not enough):

  | `port_strategy` | the fix is written on | each port… | ports run |
  |---|---|---|---|
  | `forward-merge` (default) | the **oldest** line | merges the previous line's branch into its own (and passes through every line in between — a merge cannot skip one) | one after another |
  | `cherry-pick` | the **newest** line | applies exactly what the fix landed (the target's before→after range, whatever the merge strategy) with a three-way apply | in parallel |

  What a fix *landed* is a range on the target, exact for a merge commit and a squash
  (`merge_sha^1` is the old target). On GitHub a **rebase-merge** lands N commits, so ddflow reads
  the target's tip before it merges and checks the range against the request's commit
  count; a merge a person (or a queue) did is recognised from the change itself — the
  request's own diff has the same patch-id as the last N commits. If neither can be
  proven the old `merge_sha^1` stays (GitLab reports no commit count, so it always does).

  A port carries what its fix *landed*. A follow-up fix to the same bug is a new item:
  `task add FIX2 --port-of FIX` gives it the lines FIX reached (naming one of FIX's ports
  works too), so FIX2's own ports carry what FIX2 lands.

* **A conflicting port is work, not a failure.** The claim leaves the conflict markers
  in the port's tree and names the files; the agent resolves, commits and carries on.
* **Lines never collide.** The same file on 2.x and on 3.x is two branches, so two agents
  may hold them at once; on the same line the glob check applies as always.
* **Versions per line.** `version show --line 2` reads the highest tag reachable from
  `maint/2.x`; `version cut --line 2` tags it there, and refuses a bump that would leave
  the 2.x major — a breaking change belongs on the current line.

### Environment branches: promoting downstream

GitLab flow's environment branches — each mirroring what is deployed there — are declared
in order, downstream of the current line's target (the base branch; under gitflow,
production, so an environment receives what was *released*):

```toml
[flow]
environments = ["pre-production", "production"]
auto_promote = ["pre-production"]   # optional: continuous delivery to staging
```

* **Work reaches an environment only by promotion, one step at a time.**
  `ddflow promote add pre-production` files a task that merges `main` into
  `pre-production`; `promote add production` merges `pre-production` into `production`.
  So production only ever receives what the environment before it already has
  ("upstream first"). Nothing else targets an environment branch.
* **A promotion is an ordinary task**: claim it and its tree is made from the environment
  branch with the upstream branch already merged in (a conflict is left for the agent,
  like a port's). It runs `gates.promotion_pipeline` — `unit_tests` and `merge` by
  default, since what it carries already passed its own pipeline; add a human gate there
  for a person's sign-off on each deploy, and name it in `gates.required` to make that
  sign-off mandatory (every `gates.*_pipeline` counts, so a gate only the promotion
  pipeline runs is not an inert requirement). Where environments exist, `ddflow workflow`
  lists the promotion pipeline beside the task and phase ones. It needs no cross-family
  reviewer: it authors nothing.
* **With `integration = "pr"`** the promotion lands through a merge request into the
  environment branch — the approval *is* the deploy approval — and `pr sync` completes it.
* **One open promotion per environment**, and none when there is nothing to carry (exit 2).
* **`auto_promote`** lists environments `ddflow next` promotes to by itself when the
  branch upstream moves. Empty by default: a deploy is the operator's call.
* `ddflow promote status` shows each environment's head, how many commits it is behind
  the branch upstream of it, and any open promotion. The branch says what was *promoted*,
  not what is *running*: have the deploy hook call `ddflow promote deployed production
  --sha "$GIT_SHA"` (default: the branch head) and `status` adds `live <sha>` and how many
  commits of the branch are not yet deployed — "what is live" answered exactly.

### Workflow choices: asked, recorded, defaulted on the record

ddflow supports several ways of working and never picks one silently. Each decision —
`model`, `integration`, `pr_merge`, `on_changes_requested`, `stack`, `port_strategy` — is a
**choice**, and its value comes from, in order:

1. **the operator's config** (`.ddflow/config.toml`, the machine-local
   `.ddflow/local/config.toml`, or env), which always wins;
2. **a recorded choice** — `ddflow flow choose port_strategy cherry-pick --reason "2.x has
   diverged"`, by the operator or by an agent the operator left it to, attributed in the log;
3. **the default** — applied the first time the choice matters (the first claim, the
   first pull request, the first fix filed across lines) **and recorded**, so the project
   keeps following it even if a later ddflow ships a different default.

Until then, a relevant choice nobody made heads `ddflow brief` under *Open workflow
choices*, so an agent asks at the start rather than discovering at the end that the
project wanted something else. `ddflow flow show` lists every choice with its value, its
options, and who decided — config, a named agent or person with their reason, or "DEFAULT
(nobody chose)". A recorded choice that any config layer overrides is shown as such — and `flow choose`
answers `in_effect: false` naming that layer — never silently ignored.

Research: [RESEARCH R17](../../docs/RESEARCH.md).

---
