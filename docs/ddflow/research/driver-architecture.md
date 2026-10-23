# Driver architecture: a common core, per-agent and per-model drivers that are mostly data

Task `DRV-research` (phase `P-drivers`), 2026-10-23, `res-drivers`. Proposal state; the decision proposal is
[decisions/driver-architecture.md](../decisions/driver-architecture.md).

Operator request (verbatim, 2026-10-23; "confirmation" read as "configuration"):

> Do you create drivers for every coding agent and model in order to keep the core code common and only adjust depending on which agent and model we communicate with and probably we could even adjust to the differences mostly with confirmation than code so it will be easier to adjust to future changes and coding agents? Respectively the same for agents that ddflow instantiate as subprocesses (if we plan that in the future)? Does this make sense? Can you research it?

Evidence labels: **V** verified (read in this repository's code or tests, or from a primary source fetched today), **R** reported (secondary,
or from a project record I did not re-check), **U** unknown. Repository facts were read in the `DRV-research` worktree on main of 2026-10-23.

## 1. Answer first

1. **It makes sense, and ddflow is already about 70 % of the way there on the harness axis.** 23 descriptors (`ddflow/harnesses/*.toml`, V) feed a strict loader
   (`services/harnessreg.py`), `adopt`, hooks, identity, `harness show|doctor`, the README table and a conformance suite. `docs/ddflow/adding-a-harness.md`
   promises "No Python changes for an agent whose hooks fit an existing style". That promise holds for hooks, MCP file and instruction files; it does **not**
   yet hold for anything that talks to an agent (launch, stream, resume, limits, transcripts).
2. **Four things are missing**, in order of cost:
   (a) **No model axis.** Model facts are in five unrelated places (section 3.4) and there is no rule for "this model through that harness".
   (b) **No subprocess driver at all in code.** The lane ledger is read-only; `ddflow launch` does not exist (P-agent-parity.launcher is open). Seven open tasks
   each plan their own launch template (kimi-launcher, DSH-lane, PI-lane-rpc, EXT-oh-lane, CP-backend-*, AC-backend, ATF-launch-env). Without one contract they
   will write seven launchers.
   (c) **Facts have four provenance dialects** (section 3.3) and only one of them has per-fact version ranges and expiry.
   (d) **Descriptors are not extensible from outside the package.** `HARNESS_DIR` is the only directory read (V: no other caller of `load(directory)`), while the docs say
   a project may override every value. A user or company cannot add an agent without a ddflow release.
3. **The cut is: three registries (harness, model, provider route) as data, a closed set of named engines that interpret the data, and transport drivers
   behind one `Lane` interface for the cases that are protocols, not facts.** Option B below; C (a third-party plugin SDK) is designed for but not built, which is
   what `D-extension-seams` already decided in 2026-10.
4. **Where plain code wins** (section 8): hook-config writers (idempotent region merge), tree- or compressed transcripts, protocol clients (ACP, Pi RPC, OpenCode HTTP),
   stateful stream semantics (`agent_end` versus `agent_settled`), tiered price arithmetic. A descriptor must stay a record with no conditionals, loops or expressions.

## 2. Inventory: what exists (V unless marked)

### 2.1 Harness axis

| Piece | Where | State |
|---|---|---|
| Descriptor schema and strict loader | `ddflow/services/harnessreg.py` (15.7 KB): sections `[mcp] [instructions] [hooks] [hooks.events] [plugin] [commands] [skills] [subagents] [env] [worktrees] [verified]`; unknown key, wrong type, id/file mismatch, duplicate rank all raise | done (B-hx-descriptor) |
| 23 descriptors | aider antigravity claude cline codex cody copilot cursor devin dsh gemini glm goose kilo kimi opencode openhands qodo qwen replit tabnine vscode windsurf; 1058 lines | done |
| Three-valued rule | absent = not verified, `none = true` = documented absent, values = fact (`harnessreg.py` docstring) | done |
| Coverage of the 8 optional sections over 23 descriptors (184 cells) | 104 stated, 7 `none`, **73 not verified (40 %)**; four descriptors (glm, qodo, cody, replit) state almost nothing; every `[verified].date` is 2026-10-09 except dsh (2026-10-22) | measured here |
| Hook dialects | `services/hookio.py` (10.9 KB): `NORMALIZERS` (10 `FieldMap` entries: dotted JSON paths per fact) and `EMITTERS` (7 small functions); the descriptor only **names** one | done, tables are Python literals |
| Hook config writers | `services/hookwriters/` (`claude`, `gemini` JSON; `flatlist` for cursor and copilot; plugin/cascade/cline/antigravity styles loaded but have no writer) | partial |
| MCP file shapes | `services/mcpconfig.py` (25.8 KB): 8 shapes (`mcpServers`, `servers`, `copilot`, `mcp.servers`, `opencode`, `toml.mcp_servers`, `cordis-patch-yaml`, `none`) | done, closed set in code |
| Session identity | `[env] session_id`, `harnessreg.detect(environ)`, `api/firstcall.harness_of` (id or lineage substring of `clientInfo.name`, unique match only) | done |
| MCP client capabilities | `mcpshim/clients.py` (12.7 KB, stdlib-only): per-row `list_changed`, source, verified version, `min_version`, checked date, evidence, `names`, joined to descriptors by `harness` id | done (SU-client-table, SU-client-table-descriptors) |
| Fixtures and conformance | `tests/harness_conformance/cases.py`, `tests/test_harness_conformance.py`; recorded payloads for **5 of 23** agents (claude, codex, copilot, cursor, gemini; `session_start` and `prompt` only); `tests/fixtures/dsh_probe/` has 14 recordings for dsh | partial |
| Delta notes and drivers | `docs/ddflow/drivers/implement-phase.md` plus `deltas/{claude-code,kilo-cline,vscode}.md` | 3 deltas for 23 agents |
| CLI | `ddflow harness list|show|doctor` | no `add`, `lint`, `test`, `probe` |
| Not built | `[detect] [headless] [continuation] [meters] [caps] [autonomy] [telemetry] [spawn] [doors] [acp] [test]` reserved by P-agent-parity.descriptor-v2 (open); fact record HX-facts-core (open); `[tokens]` proposed in `decisions/agent-token-features.md` (D-ATF-1, PROPOSED); vocabulary gaps DX-descriptor-vocab-dsh (open) | open |

### 2.2 Subprocess side (agents ddflow launches)

| Piece | State |
|---|---|
| Lane **ledger** (`services/lanes/`, `ddflow lanes`): read-only; a lane is an agent identity; `harness` and `model` come from `session.*` events (`ledger.py:96-97`). Never spawns | done (B-gap-lanes) |
| **Reviewers** (`services/review.py`, 75 KB): `kind = openai | anthropic | gemini | command`; `PRESETS` is a 20-entry dict of provider settings (base URL, key env, default model, family); the `command` kind is a templated subprocess (`claude -p --model {model}`, `codex exec -m {model} -`) and is the one subprocess agent that runs today | done; presets are data in code |
| Lane launcher: `ddflow launch start|stop|resume|steer` | **open**: P-agent-parity.launcher (needs descriptor-v2, continuation, B-ac-smoke, permissions) |
| Backend interface `start, steer, report, stop, status` with backends as registry entries (process, tmux, paseo-cli, paseo-sdk, opencode-http, orca-cli, acp) | **open**: CP-backend-iface; AC-backend is the `acp` entry; AC-prefer holds the per-agent preference (native, acp, tmux) |
| ACP: AC-transport, AC-client, AC-registry, AC-events, AC-permissions, AC-steer, AC-resume, AC-turn-loop, AC-fake-agent (all open); decision `D-acp-r2` (client, wrap proxy, thin agent; backend beside native; auto-allow inside the worktree and lease globs) | open |
| Per-agent lane facts already filed against the descriptor: P-agent-parity.kimi-launcher, DSH-lane, DSH-lane-acp, PI-lane-rpc, PI-lane-events, EXT-oh-lane, CP-backend-opencode, CP-backend-tmux, ATF-launch-env, ATF-resume, ATF-limit-signals, ATF-spend-caps, ATF-terms-gate | open |
| Process cleanup: Ba3b7509530 (orphaned ci suite ran 8.3 h after its parent died, fixed), DX-runner-signal-handling, DX-ci-suite-cgroup-scope, DX-ci-suite-owner-watchdog | open |
| Security decisions that bind a driver: D-dsh-telemetry (lane workers launch with `DSH_TELEMETRY_MODE=DISABLED`), D-control-planes (trusted bots start lanes only from operator-approved templates), D-config-layer-order (launch templates are operator-only keys: read from layers 3, 5, 6, 9 only) | decided |

### 2.3 Model axis

| Piece | Where |
|---|---|
| Name-substring to model family (`FAMILY_HINTS`, 22 needles), `family_for()`; unknown returns `""` on purpose | `config_sections/agent.py` |
| Provider presets: base URL, key env var, default model, wire kind | `services/review.py` `PRESETS` (20 entries) |
| Quota subjects `agent:<harness>/<account>`, `llm:<base_url>`, profiles unlimited/unknown/limited | `services/quota.py` (D-quotas) |
| Context windows, compaction, price: planned as one table keyed by model family | CC-inventory (`templates/context_families.toml`, open); prices and limits layered per TE-limit-aware-routing (open) |
| Role routing and floors | D-model-routing (decided); GV-* tasks |
| Review independence uses the **model** family; the descriptor's `family` is a **lineage** label (claude-code, gemini-cli, devin...). Same word, two meanings; `firstcall.py` reads it as a `clientInfo` key | DX-descriptor-family-doc (docs only, open) |

### 2.4 Hard-coded agent names in code: the ratchet

`tests/test_no_agent_literals.py` counts string constants (not docstrings) equal to a descriptor id or under a descriptor-named directory. Run today (V):
**110 sites in 21 files**, equal to its `BASELINE`. It is a pure ratchet: it only goes down.

| Class | Files (sites) | Total | Verdict |
|---|---|---|---|
| **Data kept as Python literals** (should be descriptor data) | `mcpshim/clients.py` 30 (clientInfo names), `services/hookio.py` 20 (dialect keys of `NORMALIZERS`/`EMITTERS`), `harnessreg.py` 6 (legacy session env, vocabulary), `services/compat_refs.py` 5 (known agent paths), `config_sections/approvals.py` 2 (transcript root `~/.claude/projects`), `services/adopt.py` 2 (CLAUDE.md pointer) | 65 | leak; DRV-inventory moves them |
| **Compat shims with a removal date** (D-compat-r2, until 1.0) | `commands/setup.py` 11, `claudehooks.py` 9, `api/setup.py` 2, `declared/hooks.py` 2, `parsers/workflow.py` 1, `legacy.py` 1 | 26 | legitimate until 1.0 |
| **Model-axis names filed under the harness ratchet** | `config_sections/agent.py` 6 (vendors anthropic, google), `services/review.py` 3 (provider kinds) | 9 | belongs in the model catalog |
| **Legitimate driver code** (names a style or a format, not a branch on the agent) | `hookwriters/__init__.py` 2, `flatlist.py` 2, `mcpconfig.py` 2 (shape names), `leanagents.py` 1 (claude-only template set), `importer_harness.py` 1 (Claude memory store reader), `harness.py` 1 (marked for removal), `core/bookkeeping.py` 1 | 10 | keep, but key by descriptor field |

Only about ten of the 110 are real "if this agent then" branches; the rest are table keys. The ratchet is an **exact-match** detector. An AST pass for any string constant
that merely contains an agent or model name finds **163 more** (V): `review.py` 48 (presets), `clients.py` 22, `parsers/workflow.py` 11, and strings such as
`"run prompt --harness claude"` in `claudehooks.py`. Name tests on identifiers and on non-string checks are invisible to both. So the true leak surface is a few hundred
sites, concentrated in the same ten files; the ratchet is a floor of the problem, not the problem.

## 3. What the code already gets right, and the four gaps

### 3.1 Right
- Strict loader and three-valued facts: a typo cannot silently turn into "not provided".
- Hook normalisation is already **data-shaped**: `FieldMap` is a set of dotted JSON paths with first-non-empty-wins. It is the right abstraction; it lives in the wrong place (Python dict keyed by a name the descriptor repeats).
- Conformance is parameterised over descriptors, so a new agent gets tests without writing any.
- `AC-prefer`, `CP-backend-iface`, `D-acp-r2`, `D-control-planes` already say "native, acp or tmux per agent, backends as registry entries".

### 3.2 Gap: the descriptor is a catalogue of files and paths, not of behaviour
Everything in the 8 shipped sections answers "where does this agent keep X". The questions a lane needs are "how do I start it, read it, stop it, tell what it cost, and tell it hit a limit". Those are the reserved-but-empty sections.

### 3.3 Gap: four provenance dialects
1. Descriptor `[verified]`: one `date`, `sources`, `probe`, `unverified[]` for the **whole file**.
2. `clients.py` `Entry`: per row `source` in {verified, source, reported, docs, observed}, `verified_version`, `min_version`, `checked`, `evidence`, and an expiry rule (an unverified `honours` expires after 180 days; `ignores` does not). This is the only place with version range and expiry.
3. Research documents: V / R / U labels, prose.
4. HX-facts-core (proposed): `{status: verified|documented|unverified|none, source, tool_version, date}`.
They agree on the idea and not on the words. `reported` (secondary source) exists in 2 and 3, not in 4.

### 3.4 Gap: model facts have no home
`FAMILY_HINTS` (agent.py), `PRESETS` (review.py), quota subjects (quota.py), CC-inventory (planned), TE-limit-aware-routing price table (planned), ATF pass 2 ("model prices are not descriptor keys"). The planned items are consistent with each other and nobody owns the **combination**.

### 3.5 Gap: no outside extension
`harnessreg.load(directory)` can read another directory but nothing passes one; `HARNESSES` loads the shipped set. The docs claim project overrides; I found no reader (grep over `ddflow/` for any caller of `load(` with an argument, or for a user directory: none). Filed as a defect.

## 4. The design

### 4.1 Three axes, one combination rule

```
            harness (the client program)     model (the weights + vendor terms)     route (how the call reaches the model)
owns        files, hooks, flags, settings,   window, price, cache semantics and TTL   endpoint, key env, wire format,
            compaction MECHANISM, resume,    options, tool-calling quality, reasoning  who bills, rate-limit headers,
            exit codes, limit MECHANISM,     knob RANGES, output limits, family        cache behaviour through a gateway
            transcripts, MCP behaviour
files       ddflow/harnesses/<id>.toml       ddflow/models/<family>.toml (+ models)    ddflow/models/routes/<id>.toml
```

Rules for `effective(harness, model, route)`; each fact says which side may set it, so no per-pair code is needed:

1. **Mechanism is the harness's, numbers are the model's.** `tokens.window.set` (how to set a window) is a harness fact; `window = 1_000_000` is a model fact. The effective window is `min(model window, harness cap if declared, operator cap)`; caps only tighten.
2. **Route overrides model where the route changes the semantics** (Claude Code pointed at a non-Anthropic gateway loses explicit 5m/1h cache; the same model via OpenRouter and direct differ in price and cache). A route fact is stated only where it differs from the model's.
3. **A pair table exists but is sparse**: `[[pair]] harness=..., model=..., facts` for known exceptions (Copilot `--context long_context` price tier; Cursor `[context=N]`). It is the last data layer before observation.
4. **Precedence, strongest first**: runtime observation (probe or listen, as `SU-client-table` already does for `list_changed`) > operator or company override (layers of D-config-layer-order; company floors tighten-only) > pair > route > model family > harness default > the lowest-common-denominator policy of `research/agent-token-features.md` section 6.
5. **Unknown never reads as supported.** A missing fact yields the LCD behaviour and is *named* in `harness show` / `model show` ("not verified, using: byte-stable prefix, short gaps, rotation by own estimate"). Unknown model resolves to the family `unknown` row, never to a neighbour by name guess.
6. **Safety facts combine by `min` or `any-prohibits`** (limits, `terms`, caps): a prohibition on either side wins.

A model family is **not** a harness: Claude Code can run non-Claude models; dsh runs DeepSeek only; Copilot routes across vendors. This is why CC-inventory's note ("model family differs from harness") is right and why `FAMILY_HINTS` must not stay a harness-section helper.

### 4.2 Descriptor-first: data versus a named engine versus code

| Difference | Form | Engine (closed set) |
|---|---|---|
| paths, env names, flags, thresholds, exit codes, window/TTL setters, instruction budgets, event names | **data** | validated scalars and setters |
| hook stdin fields (`session_id`, `prompt`, `tool_name`, nested `toolCall.name`) | **data** | **E1 `fieldmap`**: dotted paths, first-non-empty (today's `FieldMap`, moved into `[hooks.fields]`) |
| what the hook prints (plain text, JSON with context, silent, camelCase) | one of N **named variants** | **E2 `emit`**: closed list (`plain`, `json_context`, `copilot`, `cursor`, `cline`, `antigravity`, `silent`) |
| MCP file container and entry shape | data for container path and entry template; format `json`/`toml`/`yaml` | **E3 `mcp_shape`**: today's 8 shapes become a container path plus an entry template, three serialisers |
| launch command | **argv template** with `{model} {session} {prompt_file} {dir}` and an `env` table; setters (`env`, `argv`, `file`) | **E4 `render_argv`**: placeholder substitution only, no shell, argv list, secrets by env name never by value |
| per-line stream events | rules `{when = {path, equals|regex}, emit = event, fields = {name = path}}` | **E5 `stream_map`**: NDJSON/JSON-lines to canonical lane events (`started, tool, usage, settled, limit, error`); `usage_fields` is a path table |
| limit and reset detection | `{exit_code | stream_event | message regex | hook}` plus `reset_source` | **E6 `limit_detect`**: regexes and exit codes, bounded ReDoS-safe (anchored, length-capped) |
| transcript and session ingest | path template, line-format `jsonl`, path table to `role/text/usage/model/ts` | **E7 `transcript_map`**: flat JSONL only |
| version and presence probe | `[probe]` list of safe argv (`--version`, `--dump-config` where documented read-only) | **E8 `probe`**: runs only declared argv, no network, no model call, timeouts, output stored as a fact with `tool_version` |

Code remains for: hook-config **writers** (merge into the operator's settings file idempotently, keep their edits; today's `hookwriters`), **transport protocols** (ACP, Pi RPC, OpenCode HTTP, Claude SDK, tmux), formats that are not flat (Pi session tree, dsh zstd), stateful stream meaning, tiered price arithmetic (long-context threshold: data thresholds, one small function). Each such piece is a **driver** behind a stable interface (4.4) with conformance tests, and descriptors only **name** it, as `hooks.normalizer` does today.

The target: **a new agent with a flat-JSONL transcript, a Claude-shaped or fieldmap hook dialect, a headless argv and an exit-code table needs zero Python.** Measurable: the number of new agents added without a Python diff (the success metric in the decision).

### 4.3 Capability facts with provenance
One record, used everywhere (descriptor, model, route, client table), extending HX-facts-core:

```
fact = { value, status = "verified" | "documented" | "reported" | "unverified" | "none",
         source = "<url|file|probe id>", retrieved = "2026-10-23",
         versions = ">=2.1.285,<3", expires = "180d", probe = "<probe id>", by = "operator|company|ddflow" }
```
- `verified` = read in a primary source **or** produced by a probe; `documented` = a primary doc states it, not exercised; `reported` = secondary; `unverified`/absent = unknown; `none` = documented absence. Mapping to the research labels: V = verified, R = documented or reported, U = unverified. (Open question Q3: keep `documented` and add `reported`, or collapse; default: keep both.)
- Compact form so descriptors do not triple in size: a table-level `[facts.<path>]` sibling, or inline `{ v = ".mcp.json", s = "verified", src = "R-hx-claude" }`; a section-level `[verified]` remains the default for every key without its own record (back-compatible).
- **Unknown degrades, visibly**: the consumer gets `None` plus a reason code; the LCD behaviour applies; `harness show` prints `not verified` (exists) and, new, `using fallback X`.
- **Expiry and drift**: `doctor` flags (a) a fact past `expires`, (b) an installed agent whose `--version` is outside `versions` (P-agent-parity.drift owns scheduling), (c) a fixture recorded for a version outside the range. A drifted fact is *demoted to unverified for decisions that depend on it*, not deleted.
- **Override and observe**: operator/company layers write a fact with `by = operator`; runtime observation (E8 probe, or an observed header or exit code) writes to `.ddflow/local` and outranks documents, never the reverse.

### 4.4 Subprocess agents: one `Lane` driver interface

The same descriptor answers four questions; the transport is the only thing that varies.

```
LaunchSpec    argv template, env (names only), lane-private home (CODEX_HOME, DSH_HOME, KIMI config dir), strict MCP config,
              model flag, permission profile ([autonomy] id), headless | sdk | acp | rpc | http-server, cwd = the lane worktree
Observe       stream_map rules, transcript path, usage_fields, exit-code table, limit_detect, reset_source, heartbeat source
Steer         send message, interrupt, resume (argv template or protocol call), capability list (what the transport supports)
Cleanup       process group, kill order (TERM, grace, KILL), optional cgroup/systemd scope, remove lane-private files listed in the lane record
```

```
class Lane(Protocol):                       # one per transport; registered like backends in CP-backend-iface
    capabilities: frozenset[str]            # {"steer","interrupt","resume","usage","tool_events","permission_broker"}
    def start(spec: LaunchSpec) -> LaneHandle
    def events(h) -> Iterator[LaneEvent]    # canonical, bounded, redacted
    def steer(h, text) -> Result            # unavailable if "steer" not in capabilities
    def stop(h, grace_s) -> Result
    def status(h) -> LaneStatus
```
Transports as drivers: `process-headless` (E4+E5, covers claude -p, codex exec, gemini -p, kimi, dsh headless, aider `--message`), `acp` (AC-*), `rpc-jsonl` (Pi), `http-server` (opencode serve, Kimi web), `tmux`, `sdk`. `AC-prefer` picks per agent. A missing capability returns `unavailable`, never an emulation that pretends.

Rules lifted from decisions already made: identity and session binding by foreign ids recorded in the ledger (CP-foreign-ids); launch templates operator-only (D-config-layer-order, D-control-planes); no secret in argv, keys by env name; lane-private home so the user's credentials and settings are not read (dsh `DSH_HOME`, Codex `CODEX_HOME`); telemetry off for lanes where the agent offers it (D-dsh-telemetry as a descriptor fact `[headless].env`, not dsh code); cleanup as one process-group kill with a scope (Ba3b7509530, DX-runner-signal-handling, DX-ci-suite-cgroup-scope); budget and limit awareness from `[caps]`, `[meters]` and TE-limit-aware-routing (a limit with unknown data is not headroom); terms refusal (ATF-terms-gate).

**Reviewers are lanes too.** A reviewer of kind `command` is today's only launched agent and already is an E4 template. Folding `PRESETS` into the route/model data and giving reviewers a `Lane` of capability `{}` (send prompt, read reply) removes the second table; the cross-family rule then reads the **model** family from one catalog.

### 4.5 Extension by users and companies
- Descriptor and model files load from layers (packaged, company pack, user `~/.config/ddflow/harnesses.d/`, project `.ddflow/harnesses.d/`, local), merged **per fact** with D-config-layer-order; a company floor (`terms`, spend cap, telemetry off) is tighten-only.
- **Security split**: a descriptor from a cloned project may add detection, MCP/instruction/hook **paths** and facts; it may not add `[headless]` launch argv, `[probe]` argv or any key that runs a process: those are operator-only keys (layers 3, 5, 6, 9), the same rule as reviewer commands and launch templates. A company pack is data only and pinned by digest like `CM-packs` (a bare URL without a digest is refused; update is the operator's approval).
- Schema versioned (`schema = 2` in each file, migration by the existing upgrade manifest, not by readers guessing); unknown keys stay an error (typo safety) but a newer `schema` from a pack is "unavailable: upgrade ddflow", never half-read.
- CLI under existing nouns (D-mcp-new-nouns: no new MCP tool): `ddflow harness add|lint|test|probe|show|doctor|facts`, `ddflow model show|lint|probe|resolve --harness H --model M`. `lint` is the loader plus "every field has a reader" and "every launch placeholder is known"; `test` runs the offline conformance suite against fixtures; `probe` runs E8 only; `resolve` prints the effective capabilities and the source of each fact.
- Conformance, offline: for every descriptor: schema, path safety, fixtures through E1/E5/E6/E7, idempotent install and uninstall, `show` totality, argv rendering snapshot, `Lane` contract tests against a **fake agent** (AC-fake-agent already planned; the fake is a script replaying recorded stdout/exit codes). A driver without recordings is reported as a gap, never as a pass. Recordings carry `recorded: true|false` and the agent version (already the convention).

## 5. External evidence (fetched 2026-10-23)

| Source | What it shows | Label |
|---|---|---|
| LiteLLM `model_prices_and_context_window.json` (github raw, primary) | One flat JSON object keyed by model id; a `sample_spec` template documents fields: `max_input_tokens`, `max_output_tokens`, `input_cost_per_token`, `output_cost_per_token`, `litellm_provider`, `mode`, `deprecation_date`, `supported_regions`, `supports_function_calling`, `supports_prompt_caching`, `supports_reasoning`, ...; cache costs (`cache_read_input_token_cost`) and a `source` URL appear on real entries, not in the template. The file is about 3 MB (2,967,897 characters) | V |
| Aider "advanced model settings" (primary) | Per-model settings are YAML (`.aider.model.settings.yml`: `edit_format`, `cache_control`, `caches_by_default`, `reasoning_tag`, `extra_params`...) and metadata is JSON (`.aider.model.metadata.json`: window, costs, `litellm_provider`); searched in home, repo root, cwd, later files win; `extra_params` pass through to litellm | V |
| Continue `config.yaml` reference (primary) | `models:` list entries `name`, `provider`, `model`, `roles`, `capabilities` (overrides autodetection: `tool_use`, `image_input`), `defaultCompletionOptions` (with `contextLength`), `apiBase`, `requestOptions` | V |
| ACP overview (agentclientprotocol.com, primary) | JSON-RPC 2.0, agents usually subprocesses; `initialize` negotiates version and capabilities; `session/new`, `session/load` only with `loadSession`; `session/prompt` ends with a stop reason; `session/cancel` is a notification; optional methods (`fs/*`, `terminal/*`, `elicitation/create`) are gated by capabilities; `session/update` kinds include message chunks, tool calls, plans, commands, mode changes, compactions | V |
| Terraform plugin protocol (HashiCorp docs, primary) | Providers are separate processes speaking a versioned gRPC protocol; major versions define CLI/plugin compatibility, minor versions are additive; version used as registry compatibility metadata; the process-launch page was not read | V for versions, U for the handshake |
| OpenRouter models API | page URL given in the brief returned 404; the models list is reported to carry `context_length`, per-token `pricing` and `supported_parameters` | R |
| Backstage plugin model, Cline provider config | not fetched | U |

Lessons that carry over: (1) **the community's model catalogs are data with a template and a small set of capability flags**, and ddflow should consume or mirror that shape instead of inventing one (LiteLLM and Aider agree on the field names); (2) Aider and Continue both let a user **override autodetected capabilities** in a user/project file, with later files winning, which is exactly layer order; (3) ACP's capability negotiation is the model for the `Lane.capabilities` set: optional methods exist only when advertised; (4) Terraform's split (stable versioned boundary, many providers as separate processes, compatibility by major version) is the shape for option C and its price: a protocol to keep stable for years.

## 6. Options

| | A. Status quo | B. Descriptor-first, named engines, model catalog, `Lane` interface | C. B plus a plugin SDK |
|---|---|---|---|
| New agent, no new code | hooks, MCP and instructions only | also launch, stream, limits, transcripts for the common shapes | same as B |
| New agent, irregular protocol | new Python in core, per agent, in 5 to 10 places (the 110 sites) | one driver module behind `Lane` or a transcript/writer plugin in-tree | an external executable speaking a versioned JSON-over-stdio protocol |
| Model facts | 5 homes, no combination | one catalog + route + pair rule | same |
| Company or user adds an agent | needs a ddflow release | drop a data file or pack; irregular ones need a ddflow PR | also without a release |
| Cost | 0 now; seven launchers later | medium: reshuffles planned work rather than adding it (section 7) | high: protocol design, versioning, sandbox, allowlist, docs; an API that must stay stable |
| Main risk | drift and duplicated logic (the exact class that "has cost three bugs", agent.py docstring) | schema grows into a config language; engines get one-off knobs | a stable plugin API frozen before the second plugin exists |
| Fit with decisions | contradicts D-everything-configurable, r-reuse-core | fits D-extension-seams step 1 (in-tree Registry) | fits step 2 (out-of-process JSON-stdio, operator allowlist); D-extension-seams says "later" |

**Recommendation: B now; design the Lane and transcript/limit interfaces so they could be driven out-of-process, and build C only when two real third-party drivers are asked for** (D-extension-seams already says plugins are later and out-of-process, no pluggy). A is the do-nothing cost: P-agent-parity and P-acp have about 70 open tasks that will each add agent-specific launch code.

## 7. Rollout (maps to queue phase P-drivers; links, not duplicates)

1. **Decide** (DRV-decide): Q1..Q8 in the decision file.
2. **Inventory to data** (DRV-inventory): move the 65 "data in code" sites to descriptor fields in the existing ratchet; lower `BASELINE`; extend the detector to substring matches and report them as a second, report-only counter.
3. **Engines** (DRV-engines): E1, E2, E3 first (pure refactor of what exists, golden-tested by current fixtures), E4..E8 as their descriptor sections land from **descriptor-v2**, **HX-facts-core**, **ATF-descriptor-fields** (these three own the schema; DRV adds only the engine and its tests).
4. **Facts and drift** (DRV-facts): after HX-facts-core.
5. **Model catalog and combination** (DRV-model-catalog, DRV-combine): extends **CC-inventory** (same file, same fact record); adds the route and pair tables and `model resolve`. TE-limit-aware-routing and ATF read it.
6. **Lane interface** (DRV-lane-interface): the contract and the fake-driven conformance suite over **CP-backend-iface**, **AC-backend** and **P-agent-parity.launcher**; per-agent launch code (kimi-launcher, DSH-lane, PI-lane-rpc, EXT-oh-lane) become data plus at most a transport entry.
7. **CLI, conformance, packs, migrate, docs** (DRV-cli, DRV-conformance, DRV-packs, DRV-migrate, DRV-docs).

Folds proposed (docs only, not done): DRV-lane-interface could fold into CP-backend-iface (same interface; keep separate until the lead agrees, because CP-backend-iface is blocked behind P-spawn.engine and P-extension-seams.registry); DRV-facts could fold into HX-facts-core if its owner prefers one task; CC-inventory and DRV-model-catalog share a file and should be sequenced, not merged.

## 8. Over-engineering: where code beats configuration

- **Writers that edit the user's own files** (hook config merge, region markers, key-level idempotence, keep the operator's edits): a declarative merge language would be a worse programming language. Code, with a closed set of styles; data names the style.
- **Stateful meaning in streams**: `agent_end` versus `agent_settled`, retries then compaction recovery then queue drain; Kimi exit 75 retryable versus 1 permanent depends on context. Data can say "this event means settled"; it cannot say when two events together mean it. Keep that in the transport driver.
- **Non-flat transcripts** (Pi tree with branches, dsh zstd JSONL): a reader function per format; E7 covers flat JSONL only.
- **Price and limit arithmetic** with tiers (long-context threshold, cache write multipliers, credits calendar): data for rates and thresholds, one tested function for the arithmetic.
- **Protocol clients** (ACP, JSON-RPC peers, HTTP/SSE): code; data supplies endpoint, auth style, capability defaults.
- **Config-language creep.** Rules to keep it a record: no conditionals, no loops, no expressions, no includes inside a descriptor; placeholders from a closed, linted list; every field must have a reader in code (a lint fails on a dead field); a field enters the schema only with a consumer **and** either two agents that use it or a safety reason (rule of two). ATF pass 1+2 adds roughly 40 keys, several used by one agent (Qwen exit codes 53/55, Kimi 75): they belong behind a generic key (`limit_exit_codes` map) and not as new sections. This is the practical answer to D-everything-configurable versus lean: knobs for **values people change**, engines for **mechanisms**, and a schema budget reviewed per release.
- **Over-reach**: reproducing every vendor price in ddflow. Consume a catalog (LiteLLM-shaped) as an optional import and keep only the fields ddflow reads (window, cache kind and TTL, price per token for input/cached/output, limits).

## 9. What would invalidate this

- After the engines land, **more than a third of the next 6 added agents still need a Python diff** (measure: "zero-Python agents" rate in DRV-conformance reporting): then the engines are the wrong cut; look for the missing engine or accept A for the irregular ones.
- ACP becomes universal (all of the top agents ship a maintained ACP server with usage and limit signals): then the Lane layer collapses to ACP plus a small registry and most `[headless]` data is dead weight.
- Facts rot faster than they are verified (drift doctor flags more than half of facts older than 90 days, as 100 % of descriptors are already dated 2026-10-09): then the data is not cheaper than code; invest in probes first.
- The operator never adds a third-party agent or company pack: C and most of DRV-packs are unnecessary.
- Descriptor schema churn stays high (a new section every month) after descriptor-v2 and `[tokens]` land: freeze the schema and push the volatile part into model data.

## 10. Sources and records

Repository (V): `ddflow/services/harnessreg.py`, `hookio.py`, `hookwriters/`, `mcpconfig.py`, `review.py` (`PRESETS`), `quota.py`, `config_sections/agent.py` (`FAMILY_HINTS`), `api/firstcall.py`, `mcpshim/clients.py`, `services/lanes/ledger.py`, `tests/test_no_agent_literals.py`, `tests/harness_conformance/cases.py`, `docs/ddflow/adding-a-harness.md`, `docs/ddflow/decisions/{agent-compat,acp,control-planes,agent-token-features}.md`, `docs/ddflow/research/{agent-token-features,agent-token-features-pass2,dsh,pi-dev,mcp-client-matrix,accrete}.md`. Decisions: D-acp-r2, D-control-planes, D-config-layer-order, D-extension-seams, D-mcp-new-nouns, D-everything-configurable, D-model-routing, D-dsh-telemetry, D-agent-compat. Tasks linked: HX-facts-core, P-agent-parity.descriptor-v2/.launcher/.drift/.permissions/.new-descriptors, DX-descriptor-vocab-dsh, DX-descriptor-family-doc, ATF-*, CC-inventory, TE-limit-aware-routing, CP-backend-iface, CP-foreign-ids, AC-backend/-prefer/-fake-agent, P-agent-lifecycle.verify/.manifest, CM-packs, DSH-lane, PI-lane-rpc, EXT-oh-lane, DX-runner-signal-handling, DX-ci-suite-cgroup-scope. Web (fetched 2026-10-23): LiteLLM raw JSON, aider.chat adv-model-settings, docs.continue.dev reference, agentclientprotocol.com protocol overview, developer.hashicorp.com terraform-plugin-protocol.

Method limits: counts of name sites come from an AST pass over string constants; identifiers and non-constant tests are not counted. The 73-of-184 figure counts presence of a section, not the completeness of its keys.
