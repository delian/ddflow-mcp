# Agent compatibility registry: versions, features, drift

Operator question (verbatim): "as we have noticed that claude code have changed properties in their settings files (and for example removing co-authoring tag is not enforced anymore) is it possible for ddflow to maintain compatibility version list for the coding agents which ddflow supports, with minimal version for example, max version, and potentially different features depending on the coding agent version (aka opencode 1.x, opencode 2.x)? Do a deep research and propose phases and tasks to fill up the gaps."

Answer: yes. ddflow has pieces (per-agent descriptors, a ranged MCP client table, hook version markers) but nothing says which settings keys an agent takes at which version, and nothing reads an agent's version at runtime. Section 4 is the plan; the MVP is three tasks.

## 1. What ddflow does today

Per-agent facts live in `ddflow/harnesses/<id>.toml` (23 descriptors, loaded by `services/harnessreg.py`). Each has `[mcp]` (file, shape, approve key), `[hooks]` (style, file, event names), `[instructions]`, commands/skills/subagents dirs, `[env]`, and `[verified]` (date, sources, record, optional free-text `probe`, `unverified` list).

| Agent | Settings ddflow writes | Notes |
|---|---|---|
| Claude Code | `.mcp.json` (`mcpServers`); `.claude/settings.json`: `hooks` (SessionStart, PreCompact, UserPromptSubmit, PreToolUse; each command wrapped in `# ddflow:begin hooks/<name> ddflow=X fmt=1 sha=...` markers, `services/claudehooks.py`) and `enabledMcpjsonServers`/`disabledMcpjsonServers`; `.claude/commands/implement.md` | never writes `attribution` or `includeCoAuthoredBy` |
| opencode | `opencode.json` `mcp.ddflow` (`type: local`, `command` as one array, `enabled`); plugin hooks under `.opencode/plugins/`; `plugin: []` | v1 `$`-agents vs zen agents are only prose in the delta |
| Gemini, Cursor, others | `.gemini/settings.json` hooks; `flat-list` layout (`hookwriters/flatlist.py`) | only the file's own `version: 1` |

Version awareness, in full:

- **Descriptors:** free-text only. `claude.toml` says `probe = "claude --version -> 2.1.285; ..."`; `kilo.toml` says 7.2.20; 22 of 23 descriptors are dated 2026-10-09. Nothing parses these. Nothing compares them with the installed binary. On this machine `claude --version` prints 2.1.296 and `kilo --version` prints 7.8.18, both newer than the recorded probe; the changelog is at 2.1.311.
- **MCP client table:** `ddflow/mcpshim/clients.py` has the one real version mechanism: ranged rows (`min_version`, `verified_version`, `checked`, evidence, 180-day expiry for unverified `honours`) keyed by `clientInfo.name`, used for `list_changed`. It is Python data, not a managed record, and covers one capability. It notes opencode sends a constant `"unknown"` as its version, so the MCP handshake cannot identify opencode versions.
- **ddflow's own versions:** hook blocks carry `ddflow=` and `fmt=`; config has `CONFIG_FORMAT` and `configcompat.py` warns on unknown keys from a newer ddflow; the upgrade manifest (`templates/upgrade/changes.toml`) tracks ddflow releases only.
- **Doctor/harness doctor:** `ddflow harness doctor` reports drift of ddflow's own files and dangling commands (today it only prints that vscode has no claude hook writer). No check runs `<agent> --version`.
- **Settings keys outside ddflow's ownership:** this repo's own `.claude/settings.json` has an `attribution` block (commit 992f35b07) that a human wrote; ddflow neither writes nor audits it. The "no attribution trailer" rule is enforced only after the fact by the `commit-msg` forbidden-trailers hook (`services/forbidden.py`, `api/setup.py`), which is agent-version-independent and is the right backstop.

## 2. The real incident: Claude Code attribution settings

Verified from code.claude.com/docs/en/settings-reference.md and the CHANGELOG at github.com/anthropics/claude-code (fetched 2026-10-27, latest entry 2.1.311):

| Version | Change (changelog wording abridged) |
|---|---|
| 2.0.60 | Added model name to "Co-Authored-By" commit messages |
| 2.0.62 | "Added `attribution` setting to customize commit and PR bylines (deprecates `includeCoAuthoredBy`)" |
| 2.1.183 | `attribution.sessionUrl` added (omit the claude.ai link in web and Remote Control sessions) |
| 2.1.251 | Default trailer is `Co-Authored-By: Claude Code` when the model is not a recognised Claude model (third-party models behind `ANTHROPIC_BASE_URL`) |
| 2.1.269 | Fixed the attribution reminder overriding a CLAUDE.md or memory rule against attribution; "lines set by managed settings still apply" |
| 2.1.281 | `"attribution": false` hides all attribution; "older CLI versions skip a settings file that holds it, so keep the object form in files shared across versions" |

Current documented semantics:

- `attribution` object: `commit` (string, may include trailers; `""` hides), `pr` (string; `""` hides), `sessionUrl` (boolean). Unset defaults to the standard text.
- `includeCoAuthoredBy`: "Deprecated since v2.0.62 ... Claude Code still honors `includeCoAuthoredBy: false` from settings files that predate `attribution`, but ignores it once you set `attribution.commit` or `attribution.pr`." If only one of `commit`/`pr` is set, the other gets default text.
- Attribution is a model instruction, not a hook: "Claude Code tells Claude that your own instructions about attribution, such as a CLAUDE.md ... rule, take precedence ... unless the line is set in managed settings." That is why it is not "enforced": it is a prompt-level reminder the model can still miss, and the fix to hide it all is a settings key, not CLAUDE.md prose.

What I could NOT verify: the exact behaviour the operator saw. The docs show the old key is still read, so "not enforced anymore" is most likely one of: (a) a file with `includeCoAuthoredBy: false` plus a partially set `attribution` (old key ignored, other half back to default); (b) setting only `commit` and getting default `pr` text; (c) `attribution: false` written to a file shared with an older CLI (the whole file is skipped, hooks and permissions included); (d) a non-Claude model behind a custom base URL getting the 2.1.251 default trailer; (e) the 2.1.269 change moving precedence between CLAUDE.md and the reminder. Reproducing needs running the agent, which this item did not do.

Lesson: several independent changes in one key family over 14 minor releases, one (2.1.281) silently dropping a whole settings file on older CLIs. Only a per-version key map plus a conformance fixture catches that.

Also verified: Claude Code has `minimumVersion`, managed `requiredMinimumVersion`/`requiredMaximumVersion`, and `claude doctor` lists rejected settings entries. A key's introduction version is prose in the docs, not machine-readable.

opencode: the latest release at fetch time is v1.19.4 (github.com/anomalyco/opencode). I found no published 2.x; the 1.x/2.x split in the question is hypothetical. opencode's MCP handshake reports version `"unknown"` (per `mcpshim/clients.py`), so detection must use `opencode --version` or `package.json` of the install. Unverified: opencode.json schema changes between 1.x minors.

## 3. Design: a compatibility registry as data

### 3.1 Record and file

New record kind `agent-compat` (per r-everything-managed: CLI + one MCP action group from one RecordKind declaration, add/edit/remove(tombstone)/history/search/recall/RAG, logged events). Shipped defaults are TOML in `ddflow/harnesses/compat/<agent>.toml` beside the descriptors (the existing `templates/` tree is for rendered content; descriptors live in `harnesses/`). Projects and companies override per r-everything-configurable through the normal layers (`.ddflow/agent-compat.toml`, company, machine-local); a project may narrow a range or pin, never widen a company floor. Every knob is `[agents]`-style config with file, CLI, MCP and prompt surfaces.

```toml
agent = "claude"
version_scheme = "semver"          # semver | calver | opaque
detect = ["cli:claude --version", "mcp:clientInfo.version"]   # ordered; first answer wins
version_regex = '^(\d+\.\d+\.\d+)'
[range]
min_tested = "2.1.250"             # below: ddflow features that need newer keys are degraded
max_tested = "2.1.311"             # above: "untested", not "broken"
min_supported = "2.0.62"           # below: refuse writes of keys the agent cannot read
[verified]                          # same shape as harness [verified]
date = "2026-10-27"; sources = ["https://code.claude.com/docs/en/settings-reference.md"]; fixture = "tests/fixtures/agent-compat/claude/2.1.311"

[[feature]]
id = "attribution.object"          # a named capability ddflow code asks for
since = "2.0.62"
settings_file = ".claude/settings.json"
write = { attribution = { commit = "", pr = "", sessionUrl = false } }
[[feature]]
id = "attribution.false"
since = "2.1.281"
hazard = "older CLIs skip the whole settings file"
requires_all_readers = true        # ddflow must not write it into a committed file unless min_tested >= since
[[key_map]]
id = "no-coauthor"
ranges = [ { when = "<2.0.62", key = "includeCoAuthoredBy", value = false },
           { when = ">=2.0.62", key = "attribution", value = { commit = "", pr = "" } } ]
```

Ranges use a PEP 440-style specifier set (`<2.0.62`, `>=2.0.62,<3`), parsed by the one `version_tuple` helper already in `mcpshim/clients.py` (extend it for pre-release and calver rather than adding a second parser). A major such as opencode 2.x is a separate `[[era]]` with its own `[[feature]]` and `[[key_map]]` rows; ddflow code asks for a feature id, never a version, so a 1.x to 2.x break is a data change plus a fixture, not a code change.

### 3.2 Detection

1. `<agent> --version` via the existing probe runner (the `PROBE_TIMEOUT` pattern of `companions.py`; the only agent command ddflow runs), parsed by `version_regex`; result cached in machine-local state with the binary path and mtime, refreshed on mismatch.
2. Session-time: the SessionStart hook payload or env where the agent provides a version (Claude Code does not put one in `CLAUDE_CODE_SESSION_ID`; unverified for hooks stdin); MCP `clientInfo.version` where real (Claude Code, Kilo) and ignored where constant (opencode, Qwen, Continue, per the existing table).
3. Not installed or no answer: state `unknown` (never `pass`); recorded as an `unavailable` outcome in the same vocabulary gates use.
4. CLI, IDE extension and desktop installs can differ; record the surface.

### 3.3 Behaviour outside the range (default in bold; each tier a knob `warn|degrade|refuse|off`)

`above_max_tested`: **warn once per session**, keep features whose `since` holds. `below_min_tested`: **degrade** (older key from `key_map`, skip newer features, dependent gates `unavailable`). `below_min_supported`: **refuse** to write agent settings and say what to upgrade. `unknown`: **warn**, write only keys valid in every era. A company can enforce `refuse` (graded-policies research).

### 3.4 Doctor and surfaces

`ddflow doctor` and `ddflow harness doctor` gain checks: detected version vs range per adopted agent; settings file keys that this version flags as deprecated or unknown (from the registry's `deprecated` rows); keys written by ddflow that the detected version cannot read; a hook block whose `fmt=` predates the registry. `ddflow harness show` gets a version column and per-feature support for the detected version. The `attribution`/forbidden-trailer pairing becomes the first "policy to key" mapping: `[enforce].forbidden_trailers` can ask the registry which key silences the agent's own trailer for the detected version and offer it (never write unasked).

### 3.5 Drift detection

Two probes, both reading public data, neither running an agent:

- **Settings schema probe:** fetch `https://json.schemastore.org/claude-code-settings.json` (Claude Code's docs name it) and diff the key set and `deprecated` markers against the registry's known keys; report added/removed/deprecated keys. The docs say the schema can lag the CLI, so it is a lower bound.
- **Changelog watch:** fetch the agent's CHANGELOG or GitHub releases, keep lines matching the registry's watch patterns (`attribution`, `includeCoAuthoredBy`, `hooks`, `settings`, `mcp`, `permissions`, `deprecat`, `removed`) newer than `max_tested`, and file one `ddflow-gap` task per version batch. A scheduled job (`ddflow job` / cadence) runs weekly; it is off by default and honours `[egress]` (ddflow/services/egress.py exists for allowlisting network reads).
- Both write a `compat-drift` record (source URL, date) and never edit the registry; `max_tested` is bumped after the conformance test passes.

### 3.6 Conformance test per agent

`tests/fixtures/agent-compat/<agent>/<version>/` holds recorded settings files and validator output (`claude doctor` where offered). An offline test renders every `[[feature]]` write per fixture version and asserts each written key is valid in that version's snapshot and present in the registry. Recording a fixture is the one step that runs the agent (`--version`, `doctor`); it is committed. Fixtures double as the bug rule's regression tests.

### 3.7 Upgrade-manifest integration

Add an `agent-compat` section per release to `changes.toml` (ranges moved, features added, effect text). `ddflow version lint` fails a release whose descriptor or registry ranges changed with no entry (same waiver path); `ddflow upgrade` prints the changed rows for adopted agents.

## 4. Phases and tasks

Sizes: S under 1 day, M 1 to 2 days, L more.

**Phase DX-compat-mvp (do first)**

| Id | Needs | Globs | Size | What |
|---|---|---|---|---|
| DX-compat-schema | - | `ddflow/services/agentcompat.py`, `ddflow/harnesses/compat/**`, `tests/test_agentcompat.py` | M | Dataclasses and validator for `agent-compat` TOML, specifier parsing (reuse `version_tuple`), seed rows for claude (attribution family, 2.0.62/2.1.281, minimumVersion keys) and opencode (1.x only) |
| DX-compat-detect | schema | `ddflow/services/agentversion.py`, `tests/test_agentversion.py` | S | Run `<agent> --version` with timeout, parse, cache by binary path+mtime; states `ok/unknown/unavailable`; fixtures for output formats of claude and kilo |
| DX-compat-doctor | schema, detect | `ddflow/services/harness.py`, `ddflow/api/harness.py`, `tests/test_harness_doctor_compat.py` | S | `harness doctor` and `doctor` report detected version vs range per adopted agent (warn only); `harness show` version column |

Exit: `ddflow doctor` here reports claude 2.1.296 within range and kilo 7.8.18 vs descriptor 7.2.20.

**Phase DX-compat-policy**

| Id | Needs | Globs | Size | What |
|---|---|---|---|---|
| DX-compat-oor-policy | doctor | `ddflow/config_sections/**`, `ddflow/services/agentcompat.py` | M | Graded out-of-range knobs (warn/degrade/refuse/off) in config, with CLI/MCP/prompt surfaces; company floor enforcement |
| DX-compat-keymap | oor-policy | `ddflow/services/claudehooks.py`, `ddflow/api/setup.py`, `tests/test_claude_keymap.py` | M | Feature-gated writers: ddflow asks for a feature id, registry picks the key for the detected version; never writes `attribution:false` into committed files unless `min_tested >= 2.1.281` |
| DX-compat-trailer | keymap | `ddflow/services/forbidden.py`, `ddflow/api/adopt*.py` | S | `adopt`/`doctor` offer the version-correct key that silences the agent trailer when `[enforce].forbidden_trailers` is set; offer only, never auto-write |
| DX-compat-gates | oor-policy | `ddflow/services/gates/**` | S | Gates depending on an agent feature (hook-blocked commits, reviewers) record `unavailable` when the detected version lacks it |

**Phase DX-compat-drift**

| Id | Needs | Globs | Size | What |
|---|---|---|---|---|
| DX-compat-record-kind | mvp | `ddflow/services/records/**`, `ddflow/surfaces/**` | L | `agent-compat` as a RecordKind: CLI + MCP group, edit/tombstone/history/search/RAG, project/company override layers |
| DX-compat-conformance | schema | `tests/fixtures/agent-compat/**`, `tests/test_agent_conformance.py` | M | Recorded fixtures per agent/version and the offline conformance test; a fixture recorder command (`ddflow harness record`) |
| DX-compat-schema-probe | conformance | `ddflow/services/compatdrift.py`, `tests/test_compatdrift.py` | M | schemastore diff for Claude Code, injected fetcher, egress allowlist, `compat-drift` record |
| DX-compat-changelog-watch | schema-probe | `ddflow/services/compatdrift.py`, `ddflow/templates/jobs/**` | M | Changelog/releases watcher with watch patterns and a weekly opt-in job; files `ddflow-gap` tasks with `similar` dedupe |
| DX-compat-manifest | record-kind | `ddflow/templates/upgrade/**`, `scripts/upgrade_manifest.py` | S | `agent-compat` manifest section and `version lint` rule |

**Phase DX-compat-agents (breadth, after the above)**

| Id | Needs | Globs | Size | What |
|---|---|---|---|---|
| DX-compat-opencode | keymap | `ddflow/harnesses/compat/opencode.toml`, `tests/fixtures/agent-compat/opencode/**` | M | opencode eras (1.x now; 2.x row template ready), MCP shape, plugin event names (`experimental.session.compacting` is flagged experimental), detect via CLI since handshake version is `"unknown"` |
| DX-compat-rest | opencode | `ddflow/harnesses/compat/*.toml` | L | Codex, Gemini, Kilo, Cursor, Qwen rows from their docs, one task per agent if it grows; start with the five adopted most |
| DX-compat-descriptor-link | mvp | `ddflow/harnesses/*.toml`, `ddflow/services/harnessreg.py` | S | Replace free-text `probe` with structured `probed_version` that the registry cross-checks; reconcile `mcpshim/clients.py` rows to read the registry instead of duplicating versions |

## 5. Decisions for the operator

1. **Where range data lives.** Default: new `ddflow/harnesses/compat/<agent>.toml` beside descriptors, one record kind.  Alternative: extend each descriptor.
2. **Out-of-range default for above `max_tested`.** Default: warn once per session, keep working.
3. **Below `min_supported`.** Default: refuse to write agent settings, still run read-only commands.
4. **May ddflow write agent settings keys at all** (e.g. the attribution key)? Default: offer through `doctor`/`adopt` with a diff, write only on explicit approval.
5. **Drift probes and network.** Default: schema probe and changelog watch off until the operator enables them; allowlist limited to `code.claude.com`, `raw.githubusercontent.com`, `json.schemastore.org`, `api.github.com`.
6. **Who bumps `max_tested`.** Default: an agent proposes it only after the conformance fixture passes; never auto.
7. **Cross-version safety for committed files.** Default: committed settings must stay readable by `min_supported` (so never write `attribution:false`); per-user files may use the newest forms.
8. **Priority.** Default: MVP now; policy and keymap next; breadth after the first real drift catch.

## 6. Defects and gaps found in ddflow

- **G1 No installed-version detection** anywhere for agent CLIs (tasks above; filed as DX-compat-detect).
- **G2 Descriptor `probe` is free text** and stale: claude probe 2.1.285 vs installed 2.1.296, kilo probe 7.2.20 vs installed 7.8.18; 22 of 23 dated the same day with no expiry. Nothing warns when a `[verified]` date ages (the MCP table has a 180-day expiry; descriptors have none). Filed DX-compat-descriptor-link and DX-compat-stale-verified.
- **G3** `mcpshim/clients.py` ranged rows and descriptor `[verified]` carry the same facts separately.
- **G4 ddflow never audits agent settings it does not own**; `attribution`/`includeCoAuthoredBy` can silently diverge from the ddflow forbidden-trailer policy, caught only at commit-msg time (the correct backstop, but the author sees the failure late). Filed as DX-compat-trailer.
- **G5 Cross-version file hazard** (2.1.281 `attribution:false`; any key an older CLI rejects makes it skip the whole settings file including ddflow's hooks) is not modelled; ddflow hook install would not notice a file that an older agent has stopped reading. Doctor should flag "settings file rejected by agent" via `claude doctor` where available.
- **G6 `harness doctor` has no version axis** and reports only vscode. Covered by DX-compat-doctor.
- **G7 Agent writes are not registry-gated**: `claudehooks.py` writes hook event names unconditionally; a dropped or renamed event is skipped by Claude Code with a warning. Covered by DX-compat-keymap.
