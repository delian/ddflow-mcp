# Commit message management: standards, signing, push policy, templates, and how ddflow layers it

Task `CM-research` (phase `P-commit-messages`), 2026-10-22. Operator request (verbatim in the session
log, prompt of 2026-10-22 "Separately - can you make a research and create capabilities how ddflow can
orchestrate, configure and manage the git commit messages ..."). Proposals for the operator:
`docs/ddflow/decisions/commit-messages.md`. Nothing here is decided.

Marks: **VERIFIED** = fetched from the primary source or probed on this machine (git 2.43.0) today;
**UNVERIFIED** = from memory or a secondary source, check before building on it; **CONFIRMED (repo)** =
read in this checkout.

## 1. Summary

1. Commit message rules are of three kinds that must not be mixed: a **template** (how a message is
   produced), a **policy** (what any message must satisfy: lint, required and forbidden trailers,
   signing, push) and a **context** (which situation a message is for: a task commit, a merge, an
   event-log commit, a release). The policy is the contract; a template is just one producer of text that
   must pass it. That makes layering safe: a project may replace a company template, but its output
   still has to pass the merged (tighten-only) policy.
2. ddflow already generates about 14 kinds of commit text (section 6) and has the start of a policy
   (`[enforce].require_item_trailer`, `item_trailer_keys`, `forbidden_trailers`, `trailer_waivers`, the
   commit-msg hook `ddflow hooks check-msg`, `[log].commit_events_message`). It has **no** signing code
   at all (CONFIRMED (repo): no `-S`, and `gpgsign` appears only in a scratch repo that turns it off), no
   push policy beyond the "outward" knob classification, and the text of every site is a string built in
   Python.
3. ddflow already has the machinery to manage this without a new subsystem: a layered config
   (`D-config-layer-order`), data record kinds with a generic `ddflow record <kind> ...` tool
   (list/show/add/edit/remove/search/revise/history), the overlay loader for shipped text, a sandboxed
   Jinja environment with caps (`services/export/registry.py`), the org bundle and policy design
   (`P-org-config`: pin/floor/ceiling/require/allow), workflow command prompts, and the tool-diet
   families. The new code is the renderer, the lint engine, the sign and push guards and the migration of
   the 14 sites.
4. MCP: **no new tool**. CRUD of templates and policies rides the existing `ddflow_record` (family
   `doctor`, action `record`) with two new data kinds: zero schema delta. Four evaluator verbs (render,
   lint, test, status) become new `action` values of the existing `doctor` family. See section 9.
5. The agent writes fields, the template renders them, a deterministic lint decides; the model never
   judges conformance and no model runs in a git hook (section 8).
6. Two operator rules already in force are policy data, not code: "short and clean, never a
   Co-Authored-By trailer" (memory `commit-message-style`, decision `D-strip-coauthor-history-r2`, today
   `[enforce].forbidden_trailers`) and "no model names" (`agent_brief.md` line 44).

## 2. Standards and best practice

### 2.1 Subject and body

- Chris Beams (cbea.ms/git-commit, **VERIFIED**): seven rules: blank line between subject and body;
  subject limited to 50 characters; capitalized; no trailing period; imperative mood; body wrapped at 72;
  body says what and why, not how. 50 is a rule of thumb (GitHub warns past it); 72 is where GitHub
  truncates a subject and where git's own indented output stays under 80 columns.
- Linux kernel (submitting-patches, **VERIFIED**): imperative; summary at most 70-75 characters; body
  wrapped at 75; describe the problem and its impact before the fix; one logical change per patch; tags
  are exempt from wrapping.
- Angular (**VERIFIED**): `type(scope): summary`; summary imperative present tense, lowercase start, no
  trailing period; body required for every type except `docs` and at least 20 characters; footer
  `BREAKING CHANGE:` / `DEPRECATED:` / `Fixes #n`; a revert is `revert: <header>` plus "This reverts
  commit <SHA>".
- Conventional Commits 1.0.0 (**VERIFIED**): `<type>[optional scope][!]: <description>`, optional body,
  optional footers (`token: value` or `token #value`, hyphenated tokens except `BREAKING CHANGE`,
  synonym `BREAKING-CHANGE`); `fix` = PATCH, `feat` = MINOR, any breaking change = MAJOR; other types
  allowed and unspecified; elements case-insensitive except `BREAKING CHANGE`. The spec has no SHOULD
  rules and no length limits: those come from the tooling (below).
- The practical numbers (50 / 72 / 75) are conventions, so they are **knobs with presets**, never
  constants (`r-everything-configurable`).

### 2.2 Trailers

- `git interpret-trailers` (**VERIFIED**): a trailer is `Key: value`, key ASCII alphanumerics and hyphens;
  the block is at the end of the message after a blank line and is all trailers, or at least 25 % trailers
  with one git-recognised one; `trailer.<alias>.key|where|ifexists|ifmissing|cmd` configure insertion;
  `--parse` reads existing ones; `--in-place` edits a file. `git commit --trailer "K: v"` works
  (**VERIFIED** by a probe on git 2.43.0; `git log --format='%(trailers:key=Item,valueonly)'` read it
  back). This is the deterministic way to add `Item:`, `Refs:` and `Signed-off-by:`; ddflow's
  `W.commit` builds `Item:` by hand today.
- Kernel tags (**VERIFIED**): `Signed-off-by` certifies the Developer Certificate of Origin and the chain
  of handlers; `Co-developed-by` must be directly followed by that person's `Signed-off-by`; `Reported-by`
  followed by `Closes:`; `Suggested-by`, `Tested-by`, `Reviewed-by`; `Fixes:` takes at least 12 characters
  of the SHA and the quoted subject on one line; `Link:`; `Cc: stable@...`.
- DCO 1.1 (developercertificate.org, **VERIFIED**): the contributor certifies origin or right to submit
  under the licence, and that the record (including the sign-off) is public and kept indefinitely. The
  page itself does not define the `Signed-off-by` line; the kernel document does. **Consequence for
  agents:** a sign-off is a person's certification. An agent adding `Signed-off-by` for a human is a
  policy question, not a formatting one (proposal D-CM-8: person-only, like `chore approve`).
- `Co-authored-by` is a GitHub convention, not a git one (**UNVERIFIED** here). The operator forbids
  attribution trailers on this project; the policy must be able to forbid, require or allow it per layer.
- Issue-key policy (Jira-style `ABC-123`, GitHub `Closes #n`) is a regex plus where it may appear
  (subject prefix, branch name, trailer). No single tool was verified to do all of it; commitlint
  `references-empty` and gitlint `T7`/`B8` regex rules (below) are the verified building blocks.
- Gerrit-style `Change-Id` trailers are added by a commit-msg hook (**UNVERIFIED**, from memory); noted
  only as precedent that adding a trailer in a hook is an established pattern.

### 2.3 Linting and hooks

- git hooks (githooks, **VERIFIED**): `commit-msg` gets the message file, runs on `git commit` and
  `git merge`, non-zero aborts, skipped by `--no-verify`; `prepare-commit-msg` gets the file, a source
  (`message`, `template`, `merge`, `squash`, `commit`) and optionally a SHA, can edit the message, is
  **not** skipped by `--no-verify`; `pre-merge-commit` is skipped by `--no-verify`; `pre-push` gets remote
  name and URL and one stdin line per ref (`local-ref local-sha remote-ref remote-sha`), non-zero aborts
  the push (the page does not say whether `--no-verify` skips it; **UNVERIFIED**, believed yes);
  `reference-transaction` (preparing/prepared/committed/aborted) sees every ref update, plumbing included.
- Probes on this machine (**VERIFIED**): a hook in the primary `.git/hooks` runs for a commit made in a
  **linked worktree** (the message path was `.git/worktrees/w/COMMIT_EDITMSG`), so one installed hook
  covers every agent worktree; `git merge --no-ff -m` also runs `commit-msg` (file `MERGE_MSG`).
  `git commit-tree` **ignores `commit.gpgsign`**: with the config on, `commit-tree` produced `%G?` = `N`
  while `commit-tree -S` produced `G`, and porcelain `merge --no-ff` under the same config produced `G`.
  ddflow makes commits by plumbing in `eventcommit._rebuild_on`, `claimref`, `review` and
  `upgrade_dryrun`; any "all commits signed" promise has to name them (section 7). That plumbing does not
  run hooks is documented git behaviour (**UNVERIFIED** by probe).
- ddflow's own commits that pass `--no-verify` (`merge_refresh.py:30` "refresh generated documents")
  bypass `commit-msg`; the engine must therefore also run **in process** on text ddflow generates
  (D-CM-6).
- commitlint (**VERIFIED** rule list): rules are `[level, 'always'|'never', value]`; header-max-length 72,
  subject-case never sentence/start/pascal/upper, type-enum build chore ci docs feat fix perf refactor
  revert style test, scope-enum, body-max-line-length, body-leading-blank, footer-leading-blank,
  signed-off-by, trailer-exists, references-empty, breaking-change-exclamation-mark. The level number is
  not on the fetched page (**UNVERIFIED**; 0 off, 1 warn, 2 error from memory).
- gitlint (**VERIFIED** rule table): T1 title-max-length 72, T2-T4 whitespace/punctuation/tab, T5 banned
  words (`WIP`), T7 title regex, T8 min length 5, B1 body line length 80, B5 body min length 20, B6
  body-is-missing, B7 changed-file mention, B8 body regex, M1 author email, I1-I4 ignore rules; `.gitlint`
  sections per rule; user-defined rules are Python classes (page not fetched).
- Commitizen (secondary sources only, **UNVERIFIED** against the live docs): `cz_customize` under
  `[tool.commitizen.customize]` has `message_template` (Jinja2, variables named by the questions),
  `schema_pattern` (regex used by `cz check`), `bump_map`. It is the closest precedent for "a Jinja
  template plus a regex contract", the template/policy split proposed here.
- pre-commit framework (**VERIFIED**): `pre-commit install --hook-type commit-msg`, or
  `default_install_hook_types: [pre-commit, commit-msg]`; a hook runs at that stage only if its `stages`
  includes `commit-msg`; it receives one filename; `prepare-commit-msg` and `pre-push` stages exist and
  export `PRE_COMMIT_COMMIT_MSG_SOURCE`, `PRE_COMMIT_FROM_REF`, `PRE_COMMIT_TO_REF`,
  `PRE_COMMIT_REMOTE_NAME`. ddflow already emits such a hook (CONFIRMED (repo): `services/precommit.py`
  line 233, `ddflow hooks check-msg`, stage `commit-msg`).
- `commit.template` (git config, **UNVERIFIED** here: the config page fetch truncated before it): a file
  used as the initial message in the editor; it does not apply to `-m`. ddflow can write one rendered for
  the current context but cannot rely on it; `prepare-commit-msg` is the reliable seam.

### 2.4 Signing

- Formats (**VERIFIED** by probe plus fetched docs): `gpg.format` openpgp (default), ssh, x509. With
  `gpg.format=ssh`, `user.signingkey` (a public key file), `gpg.ssh.allowedSignersFile` and
  `commit.gpgsign=true`, a commit was signed, `git log --format='%G? %GS'` printed `G` and the signer, and
  `git verify-commit HEAD` printed `Good "git" signature for t@e.x with ED25519 key SHA256:...`
  (git 2.43.0, `ssh-keygen`). `%G?` letters G (good), B (bad), U (good, unknown validity), X/Y/R
  (expired/revoked), E (cannot check), N (none) are standard git (**UNVERIFIED** beyond G and N, which the
  probe showed).
- Other config (**UNVERIFIED**, from memory because the page fetch truncated): `tag.gpgSign`,
  `gpg.program`, `gpg.ssh.program`, `gpg.minTrustLevel`, `push.gpgSign` (signed push certificates; needs
  server support, `receive.certNonceSeed`), `merge.verifySignatures` / `--verify-signatures`.
- gitsign / Sigstore (**VERIFIED** README): keyless, OIDC login in a browser, short-lived certificate,
  Rekor transparency log; `gpg.x509.program=gitsign`, `gpg.format=x509`; verify with `gitsign verify
  --certificate-identity ... --certificate-oidc-issuer ...` (plain `git verify-commit` only checks
  integrity); needs a browser or a device-flow/non-interactive token provider in headless use; **GitHub
  does not mark gitsign commits Verified** (its trust root lacks the Sigstore CA). An unattended agent
  cannot use the interactive flow; this is a "supported with caveats" backend, not a default.
- GitHub (**VERIFIED**): verifies GPG, SSH and S/MIME; vigilant mode adds Partially verified / Unverified
  for unsigned commits; rebase-and-merge in the web UI produces modified copies GitHub cannot sign for the
  committer (signatures are lost; the page suggests merging locally); web-UI commits are signed by
  GitHub's own key. Protected branches (**VERIFIED**): "require signed commits" refuses unsigned pushes
  and can block a squash merge because the head branch's unsigned commits are checked; "require linear
  history", "restrict who can push" and "block force pushes" are separate rules. GitLab equivalents are
  **UNVERIFIED**.
- Key custody for agents: git config is shared by all worktrees of a repository, so a signing key set once
  signs every agent worktree (the `user.*` and hook behaviour in the linked-worktree probe is consistent
  with this; signing itself was probed in the primary only). The risk is an interactive pinentry or a
  browser flow hanging an unattended lane. ddflow must never store, generate or copy a private key; it may
  only (a) read `git config`, (b) run signing with a timeout, (c) report `unavailable` (exit 2) when signing
  cannot run, never silently produce an unsigned commit when policy says `required`.

### 2.5 Push policy

- Server side is where enforcement is real: protected branches, required signatures, linear history, no
  force push (GitHub, **VERIFIED** above). Client hooks (`pre-push`) are advisory because `--no-verify`
  and plumbing skip them (ddflow's own `forbidden_trailers` doc says the same about `commit-msg`).
- ddflow already treats pushing as an *outward* act: `[flow].integration = "pr"` and `[flow].claims =
  "remote"` are `outward` knobs whose `strictest` value is the safe one, so a typo never pushes (CONFIRMED
  (repo): `config_sections/flow.py`, decision `D-fallback-no-remote`). ddflow's own pushes are in
  `infra/worktree.py:1015`, `services/flow.py:1431` and `infra/claimref.py` (claim refs, force-with-lease on
  its own refs). The operator rule is "never push unless asked"; the push policy generalises it to a
  per-project, per-layer knob with a protected-branch refusal and an optional signature requirement,
  checked by one guard function that every ddflow push calls, plus an optional generated `pre-push` hook
  for pushes ddflow does not make.

## 3. Jinja in ddflow

- CONFIRMED (repo): `jinja2>=3.1.6` is a **runtime dependency** (`pyproject.toml`), kept because a project
  venv once lacked it. `services/prompts.py` renders with `StrictUndefined`, `trim_blocks`,
  `lstrip_blocks`, `autoescape=False` and has a loud stdlib fallback; `services/export/registry.py` has a
  `SandboxedEnvironment` subclass with extra filters (`wrap(width)` ...) and a render time cap
  (`_render_capped`); `services/overlay.py` is the one loader (config path, project file, builtin; eject
  with a digest marker so an unedited copy refreshes and an edited one never does).
- Jinja sandbox (**VERIFIED**): `SandboxedEnvironment` blocks private attributes, calls to `unsafe`
  callables and mutation (the immutable variant also blocks list/dict mutation); `SecurityError`; the docs
  say it "is not a solution for perfect security", a small template can produce huge output, so enforce CPU
  and memory limits and pass only the data the template needs. So: sandbox + a curated read-only context
  of plain dicts and strings + output and time caps + no callables with side effects.
- Decision `D-config-engine` / `D-jinja-trust-reaffirmed` (CE-jinja, open): Jinja allowed in config,
  sandbox on, `[config].template_trust = any|digest|off`. Commit templates are *data* (instruction text),
  never executable extensions; they reuse the same sandbox and honour `template_trust`.
- The hook path must not get slower (CE-jinja note). The commit-msg lint therefore must not import jinja
  (pure `re`/stdlib); jinja is imported only when a template is rendered.
- Three sandbox setups already exist (prompts, export, planned CE-jinja); a fourth would be a duplicate,
  so CM-sandbox first extracts one shared environment factory.

## 4. How ddflow layers things today (CONFIRMED (repo))

- Layer order (`D-config-layer-order`, operator 2026-10-18, carried by CE-layer-stack): packaged default <
  company defaults < user < project (conf.d, then config.toml) < machine-local < env < CLI < session
  overlay < company ENFORCED. **User sits below project**, not above (this differs from the operator's
  phrasing "project, user, company"; flagged as an open question). Operator-only keys are read only from
  user, machine-local, env and enforced layers.
- Org policy (B-org-policy): items `pin / floor / ceiling / require / allow` with `enforce | audit`; a
  violating write from CLI/MCP/prompt is refused naming item, source and rev; a project can tighten, not
  loosen. Executable parts from a company source stay inert until a person approves their digest
  (B-org-trust); instruction text is data. Signatures are verified with `verify-commit` against
  `gpg.ssh.allowedSignersFile` (B-org-trust) - the same machinery as commit signature verification.
- Org kinds (B-org-kinds): rules, prompts, commands, help, export templates register with the overlay
  loader and sync from a bundle; a new data kind joins the same way.
- Agent guard (B-org-agent-guard): knobs with `tier=policy` written by an agent become pending proposals
  until a person approves. Commit policy knobs and records must be `tier=policy`.
- Everything-managed rules (`r-everything-configurable`, `r-everything-managed`, `r-company-workflows`):
  every tunable is a `[section]` knob settable by file, CLI, MCP and prompt through `configwrite`; every
  definition is a record with add/edit/remove/history; shipped defaults are copied, never edited in place.
- Requirements layering (RQM-layers, `requirements-management.md` section 5): org enforced > org defaults
  > team > project > local > task; a lower layer may only **tighten**; loosening needs a scoped, expiring
  waiver. The commit policy uses the same tighten-only functions (section 5.3).
- Record kinds as data: `ddflow/templates/kinds/*.toml` (e.g. `requirement.toml`: name, text fields,
  `[lifecycle]`, `[[field]]`, `[[link]]`) generate the CLI and the generic `ddflow record <kind>
  list|show|add|edit|remove|search|revise|history`, which is MCP `ddflow_record` (tool-diet family
  `doctor`, action `record`). Nothing new is needed for CRUD of a new kind.
- Tool diet (`D-mcp-tool-diet`): 24 advertised tools = 9 hot singles + 15 noun families with an `action`
  enum; a command declares its family or import fails; old names stay as hidden aliases; profiles lean 15 /
  families 24 / hot+families 36; `admin` and `task` already have the most union params (up to 42 before
  pruning). The `rule` family is small (7 actions, ~13 params). The `doctor` family holds `doctor`,
  `hooks`, `precommit`, `record` (research mapping table). Open tool-diet tasks on the same files:
  `MTD-family-spec` (running; `ddflow/surfaces/registry.py`, `tools/families.py`), `MTD-family-schema`
  (`ddflow/surfaces/mcp.py`, `tools/families.py`), `MTD-profiles` (`tools/tiers.py`, `mcp.py`,
  `config_sections/mcp.py`).
- Prompts and workflow commands: `ddflow prompts list|show|get|eject`; shipped command prompts
  (`templates/prompts/commands/*.md`: implement, onboard, bug-hunt ...) are served as MCP prompts and by
  `ddflow prompts get <name>`; an eject copies one into `.ddflow/prompts/` where it wins.

## 5. Proposed model

### 5.1 Vocabulary

- **Context**: the situation a message is written for. Built-in: `task` (an agent's work commit), `fix`
  (a bug fix), `docs`, `chore`, `decision`, `merge` (an item landed), `bring_in_base` (base merged into a
  task branch), `backmerge`, `events` (event-log commit), `release_bump`, `release_changelog`,
  `release_merge`, `release_tag`, `housekeeping` (generated documents, pins, snapshot), `port`, `revert`,
  `squash`, `pr_title`, `pr_body`. Internal ref commits (`claim`, delta base, upgrade rehearsal) are
  **exempt**: not history a person reads. Users add contexts (5.5).
- **Template**: a named, layered record holding Jinja text for one or more contexts plus the sample used by
  `test`.
- **Policy**: layered rules every message in a context set must satisfy.
- **Fields**: the structured input that fills a template (`type`, `scope`, `summary`, `body`, `refs`,
  `breaking`, and the context's own: `item`, `version`, `reason`).

### 5.2 Records and knobs

Two data kinds (`ddflow/templates/kinds/commit_template.toml`, `commit_policy.toml`) and one config
section:

```
commit_template:  id, title, contexts[], body (jinja), sample (json), enabled, tags, priority, note
commit_policy:    id, title, contexts[] (or "*"), rules{...}, trailers_required[], trailers_forbidden[],
                  enforcement = enforce|audit, tags, note
[commit]          style = "plain"            # preset: plain | conventional | kernel | angular | jira
                  hook = true                # commit-msg + prepare-commit-msg installed by `hooks install`
                  lint_generated = true      # lint ddflow's own messages in process, even under --no-verify
                  draft_retries = 2          # bounded agent redraft after a lint refusal
                  sign = "off"               # off | auto | required      (policy tier)
                  sign_scope = "merge"       # merge | tips | all
                  sign_verify = "git"        # git | gitsign
                  signoff = "never"          # never | person | policy    (policy tier)
                  push = "never"             # never | branch | allowed   (outward + policy tier)
                  push_protected = ["main", "master", "release/*"]
                  push_require_signed = false
                  push_require_lint = true
                  template_dir = ".ddflow/commit"
                  packs = []                 # org/company packs by id; pinned in org.lock
```

Policy rules (all optional; absent = not checked): `subject_max` (72), `subject_min` (5),
`subject_pattern`, `subject_case`, `subject_imperative_hint` (heuristic, audit only), `no_trailing_period`,
`body_wrap` (72), `body_required` (per context), `body_min`, `blank_after_subject`, `types` (closed list),
`scope_required`, `scope_list`, `issue_key_pattern`, `issue_key_where` (subject | trailer | branch),
`banned_words` (WIP, model names), `forbidden_patterns`, `trailers_required`, `trailers_forbidden`
(`Co-Authored-By`), `trailer_values{key: regex}`, `max_lines`. Existing `[enforce].require_item_trailer`,
`item_trailer_keys`, `forbidden_trailers`, `trailer_waivers` stay as input aliases of the default project
policy (migration in CM-config); nothing is removed in the first release.

### 5.3 Tighten-only merge

`effective(context)` merges every applicable policy by layer. Per field: required/forbidden lists
**union**; `*_max` **minimum**; `*_min` **maximum**; regexes **all must match** (conjunction), forbidden
regexes **any refuses**; closed lists (`types`, `scope_list`) **intersection**; `sign` **maximum** of
off < auto < required; `push` **minimum** of allowed > branch > never; `enforcement` **maximum** of
audit < enforce. A lower layer's value that would loosen the result is ignored and reported (the same
"conflict, reported and ignored" rule as requirements) unless a scoped, expiring waiver exists. Company
ENFORCED is applied last and cannot be overridden by env, CLI or session (CE-layer-stack acceptance).

Templates select by `(context, layer, priority)`: the highest layer that defines an enabled template for
the context wins, except a company template marked `enforced`, which only an `extend` can add trailers
to. Whatever template wins, its output is linted against the merged policy; a template that cannot
satisfy the policy for its own sample fails `commit test` and is reported by `doctor`.

### 5.4 Rendering and safe variables

Sandboxed Jinja (section 3) with `StrictUndefined` (an undefined variable raises, never renders empty -
the rule `prompts.py` states), the export renderer's time cap plus an output cap (8 KB), no callables.
Context passed in (read-only plain values): `context`, `fields.*`, `item.{id,kind,title,phase,tags,
priority}`, `bug.{id,title,scope}`, `phase.{id,title}`, `version`, `reason`, `door`, `base`, `branch`,
`agent.id` and `model_family` (present so a *policy* can ban it; shipped templates never use it),
`repo.{name,default_branch}`, `trailers`, `vars.*` (static user variables, 5.5), `config.commit.*`. Not
passed: environment, paths outside the repo, secrets, git objects, any module. Filters: `wrap(width)`,
`subject(n)` (truncate at a word), `oneline`, `trailer(key, value)`, `issue_keys`, `slug`. Shipped
templates are written in the subset both Jinja and the fallback renderer agree on.

### 5.5 Extension points

- **User contexts**: `[[commit.context]] name, doors|match (door name, item kind, tag, glob), template,
  vars`; selected by the explicit `--context` first, then the committing door, then item kind (`bug` ->
  `fix`), then tags, then `task`.
- **Custom variables**: `[commit.vars]` static strings, plus whitelisted read-only item/phase/bug fields
  through the same curated context; a company pack may declare more (`ticket_prefix = "ENG"`).
- **Org packs** (CM-packs): a company ships `commit/` inside its org bundle (B-org-bundle: manifest roots
  prevent two bundles owning the same enforced item), pinned by digest in `.ddflow/org.lock`, data only
  (templates and policies; **no** command or hook content), signature verified when present, refreshed by
  the org sync and shown in brief/doctor with the source named. A pack may also be a local file or a git
  source; a bare URL without a pinned digest is refused (D-CM-10).
- **Presets**: shipped copies, never edited in place: `plain` (the ddflow default: imperative subject <=
  72, body wrap 72, no attribution trailer, `Item:` trailer on task commits), `conventional`, `angular`,
  `kernel` (75 columns, `Signed-off-by` required, Fixes format), `jira` (issue key in subject).

## 6. Inventory: every place ddflow generates commit text (CONFIRMED (repo), grep and reading)

| # | Context | Site | Text today | Notes |
|---|---|---|---|---|
| 1 | task / chore / decision | `infra/worktree.py:480` `commit()`; callers `api/chore.py:133`, `api/decide.py:147` | caller message + `\n\nItem: <id>` (extra trailers dict) | trailers already structured; decision text `decision {id}: {title}[:120]` |
| 2 | merge | `infra/worktree.py:485,568` `merge()`/`_merge_here`; `api/lifecycle/merge.py:175`, `api/mergequeue_run.py:163`, `services/cleanup.py:731` | `merge {item}: {title}`, `merge {item}`, `merge {x} (cleanup)` | |
| 3 | squash | `infra/worktree.py:590-604` | the merge text via `commit -m` | |
| 4 | backmerge / release merge | `api/lifecycle/merge.py:221`, `services/flow.py:1406,1416` | `back-merge {item} into {extra}`, `release {version}`, `back-merge {tag}` | |
| 5 | bring_in_base | `worktree.py:331`, `api/mergequeue_run.py:69,76`, `services/branchsync.py:262`, `services/ci.py:269` | git's own `merge --no-edit` ("Merge branch ...") | not controlled by ddflow today |
| 6 | events | `services/eventcommit.py:133,201-235`, knob `[log].commit_events_message` | `events: {reason}` (`str.format`, one variable) | the only existing template knob |
| 7 | release_bump / changelog | `services/flow.py:1315,1368` via `version_files.py:141`, `changelog_cut.py:301` | `chore: bump version to {v}`, `docs: changelog for {v}` | already Conventional-Commits-shaped |
| 8 | release_tag | `infra/worktree.py:1069` `tag()` | annotated tag message from `flow` | |
| 9 | housekeeping | `api/mergequeue_run.py:104` | `regenerate pins: a, b` | |
| 10 | housekeeping | `api/lifecycle/merge_refresh.py:30` | `refresh generated documents`, `--no-verify` | bypasses commit-msg: needs in-process lint |
| 11 | housekeeping | `services/backups.py:205` | `ddflow: snapshot before upgrade` | |
| 12 | port | `services/ports.py:118` | `port {src} to this line: {title}\n\nItem: ..\nPorted-from: ..` | two trailers |
| 13 | pr_title / pr_body | `services/flow.py:188-189,477-478,1455-1456,1580-1581`; `infra/forge.py:464,593` | `{id}: {title}`, `pr_body()`, release notes | not a commit, same policy applies |
| 14 | agent guidance | `templates/prompts/agent_brief.md:44` | "Commit messages: short, no Co-Authored-By trailer, no model names." | policy text hard-coded; becomes a rendering of the effective policy |
| - | exempt | `infra/claimref.py:71` claim ref; `api/review.py:1566` delta base; `services/upgrade_dryrun.py:796` scratch repo | machine text on non-history objects | listed so a ratchet test knows them |

Commits by agents themselves (`git commit` in a task worktree) are not generated by ddflow: they are
reached by (a) the guidance rendered into the brief, (b) `prepare-commit-msg` pre-filling the context
skeleton and trailers, (c) `commit-msg` lint, (d) the `commits` gate.

## 7. Signing and push design (proposal; the operator decides)

- `[commit].sign`: `off` (default; ddflow never touches signing), `auto` (sign iff git is already
  configured to: `commit.gpgsign` true or `user.signingkey` set; ddflow's plumbing commits add `-S`
  explicitly because plumbing ignores the config - **VERIFIED**), `required` (every in-scope commit must
  verify; ddflow refuses to produce an unsigned one and the `commits` gate fails otherwise).
- `sign_scope`: `merge` (commits ddflow itself creates on the base: merge, squash, event-log, release),
  `tips` (also the head of each task branch), `all` (every commit base..head). A squash or rebase merge
  replaces the author's commits, so per-commit signatures from a task branch do not survive into the base
  (GitHub web-UI behaviour **VERIFIED**; a local squash is signed by whoever merges); `merge` is the
  portable promise.
- Verification: `git verify-commit` / `git log --format=%G?` against `gpg.ssh.allowedSignersFile` (reuses
  B-org-trust's mechanism); `sign_verify = gitsign` calls `gitsign verify` with identity and issuer knobs.
  `G` good; the other letters map to refuse (required) or warn (auto); "cannot run" is exit 2 /
  `unavailable`, never a pass.
- Not ddflow's job: generating, storing or rotating keys, pinentry, ssh-agent. `doctor` reports `commit
  status` (format, key configured, allowed-signers file present, test signature) and names the operator's
  own fix.
- Push (`[commit].push`): `never` (default; ddflow makes no push beyond what `flow.integration=pr` and
  `flow.claims=remote` already do because the operator set them - the guard names that setting as the
  reason it allows), `branch` (only the task branch, never a protected pattern), `allowed`. One guard
  `pushguard.check(ref, range)` is called by every ddflow push site (`worktree.py:1015`, `flow.py:1431`;
  `refs/ddflow/claims/*` exempt); it refuses protected branches, a force push, an unsigned range under
  `push_require_signed` and a range failing lint under `push_require_lint`. `ddflow hooks install` can add a
  generated `pre-push` hook calling the same guard for pushes ddflow does not make (advisory:
  `--no-verify` skips it). Server-side protection stays the real enforcement and `doctor` says so.

## 8. What the LLM writes and what the template renders

1. The agent never writes the final message string for ddflow-owned commits. It supplies `fields`; the
   template renders; the in-process lint runs; a refusal returns precise, machine-readable violations
   (rule id, line, limit) the agent can fix; at most `draft_retries` redrafts, then the operation fails
   with the violations (no silent fallback to an unlinted message).
2. For commits the agent makes with plain `git commit`, `prepare-commit-msg` pre-fills the context
   skeleton (type/scope placeholders as comments, `Item:` from the claimed item) and `commit-msg` lints;
   the agent learns the policy from its brief (rendered from the effective policy, so the sentence in
   `agent_brief.md` becomes data) and from `ddflow_doctor action=commit_status`.
3. No model runs in a hook and no model decides conformance: lint is regexes and counts. An optional
   "does it say why?" review is a reviewer-gate lens, advisory, never the contract.
4. A user managing messages by prompt ("make merge commits name the phase", "ban emoji", "show me the
   policy and delete the DCO rule") is served by a workflow command prompt `commit-policy`; every change
   goes through `ddflow record` / `ddflow config` (so it is an event, shows in history, can be
   reverted) and policy-tier writes by an agent become pending proposals until a person approves.

## 9. MCP surface (tool-diet aware)

- CRUD for templates and policies: existing `ddflow_record` (family `doctor`, `action=record`) with
  `kind=commit_template|commit_policy`. **Schema delta: none.** `revise` and `history` give review,
  change and restore.
- Config: existing `ddflow_configure` (family `admin`, `action=configure`). **Delta: none.**
- Evaluators (stateless, need no record): new CLI `ddflow commit render|lint|test|status`, declared with
  `family="doctor"`. Delta on `ddflow_doctor`: four new `action` values (`commit_render`, `commit_lint`,
  `commit_test`, `commit_status`; exact spelling follows MTD-family-spec's rule "the CLI `noun verb` with
  the family noun stripped") and optional params `context`, `fields` (object), `message` (string), `range`
  (string), `template` (string). **No new tool; +4 actions, +5 optional params (roughly 0.6 KB; an
  UNVERIFIED estimate, MTD-budget-test measures it).** CLI sugar `ddflow commit
  list|show|add|edit|remove` is a thin alias of `ddflow record commit_template ...` and is CLI-only.
- Why `doctor`: it already holds the hooks check (`hooks check-msg`), `precommit` and `record`; `admin`
  carries the most union params and `rule` is for guidance text. Alternatives are in the decision doc.
- The lean profile (15 tools) may not advertise these actions; those clients reach them through the
  `ddflow_search`/`ddflow_call` facade (**UNVERIFIED**: depends on MTD-profiles' final membership).

## 10. Risks and unknowns

- File clashes: CLI/MCP declarations and `ddflow/config.py` are shared hot files; CM-cli, CM-mcp and
  CM-config wait for the tool-diet and config tasks that hold the same files.
- `CE-layer-stack`, `B-org-layers` and `B-org-kinds` are open: v1 layering is default < user < project <
  machine-local with the existing overlay loader; org layers plug in when those land (CM-packs waits).
- Imperative-mood checks are a heuristic (no reliable deterministic test); audit-only.
- `--no-verify` and plumbing can always bypass client hooks; only the in-process lint and server rules
  are firm.
- Squash/rebase merges change committers and drop signatures (section 7); `sign_scope=merge` is the honest
  default if the operator wants `required`.
- UNVERIFIED above, to probe first in CM-sign and CM-push: commitizen specifics, GitLab, `push.gpgSign`,
  `%G?` letters other than G/N, whether `pre-push` honours `--no-verify`, `commit.template` with `-m`.
- The Beams 50-character subject limit conflicts with ddflow's own subjects (`merge <id>: <title>`
  regularly exceeds 50); presets default to 72 and 50 is opt-in.
