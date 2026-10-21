# ddflow configuration: internal analysis and integration map (2026-10-17)

Read-only research. Measured on this machine (Python 3.13.12, warm cache, queue idle-ish). dynaconf 3.3.5 was installed only into /tmp/claude-1001/dyn/site. Not verified: dynaconf behaviour beyond its source read and the timings below; no prototype of the proposed design was built.

## 1. How configuration works today

**Declaration.** `ddflow/config.py` (992 lines) assembles `Config`, a dataclass of 35 sections (`lease, worktree, flow, gates, ... docs`). Each section is a dataclass in `ddflow/config_sections/<name>.py` (3,432 lines in all). That is 314 knobs by my count. A knob is declared once with `knob(default | factory=..., doc=..., choices=..., strictest=(value, why), outward=..., check=..., renamed_from=..., members=..., default_changed_in=...)` under `@declare("section")` (`config_sections/_docs.py`). `declare` fills `KNOB_DOCS` and `DECLARED`, from which `KNOB_CHOICES`, `KNOB_STRICTEST`, `KNOB_OUTWARD` and the load/write checks are built. Ratchets: `test_config_knobs_declared.py`, `test_ratchet_no_dead_knobs.py`, `test_every_knob_is_documented`.

**So defaults are Python.** The default of every knob is the dataclass field default. The default gate definitions (`DEFAULT_GATES`, about 11 `GateDef`s with their agent prompts, `services/gates/defs.py` lines ~35-355) and the default pipelines (`GatesConfig.task_pipeline/doc_pipeline/phase_pipeline/promotion_pipeline`) are Python too. Already TOML: `ddflow/harnesses/*.toml` (22 agent descriptors), `templates/companions.toml`, the upgrade manifest.

**Layers** (`Config.load`, config.py:298; layer paths in `config_sections/_layers.py`, `LAYERS = ("file","local")`):
1. dataclass defaults;
2. committed `.ddflow/config.toml` (`layer_path(root,"file")`), source label `file`;
3. git-ignored `.ddflow/local/config.toml`, label `local` (D-no-own-services-local-dir);
4. env `DDFLOW_<SECTION>_<KNOB>` (plus old names from `RENAMED`), label `env`;
5. outside `Config`: values recorded in the event log. `services/choices.py::overlay` applies `flow.*` choices (`flow choose`) only where no config layer set the knob (`core.flow.config_wins`); defaults get recorded once (`adopt_defaults`) so a later ddflow default change does not move the project. Source label `log:<by>`.

There is NO user layer and NO system layer in `Config` today (grep: only `services/quota.py` uses `~/.config/ddflow`). Those are planned in P-org-config (`~/.config/ddflow/org.toml`, `/etc/ddflow/org.toml`). There is no per-knob CLI flag; the CLI writes (`ddflow config KEY VALUE [--local]`), it does not override per run.

**Foreign tables** (not `Config`): `[gate.*]` (`services/gates/defs.py::load_gates`), `[[reviewer]]` (`services/review.py`), `[[companion]]` (`services/companions.py`), `[[macro]]` (`services/macros.py`), loaded through `infra/tomlcfg.py` (`overlay_table`, `overlay_array`, `config_paths(root, own)`), reading `config.toml` and a dedicated file (`gates.toml`, `reviewers.toml`, `companions.toml`, `macros.toml`) at the committed layer, then both again at `local/`. `Config._FOREIGN_TABLES` lists them. One-file-per-item directories also exist: `.ddflow/schedules/*.toml`, `.ddflow/triggers/*.toml` (`services/tomldir.py::load_toml_dir`), `.ddflow/rules/*.toml`, `.ddflow/pins.toml`. So a "split per topic" idiom already exists, but as a hand-rolled list of dedicated filenames, not a general `conf.d`. `adopt.py` records a bug where a placeholder `gates.toml` silently overrode `config.toml`: two sources of truth is a known failure.

**Unknowns.** `_apply` (config.py:383) is lenient for a file a newer ddflow wrote (unknown key skipped, warned, reported by `doctor`), strict when code and file are one tree (`_is_code_tree`); a bad enum falls back to the strictest value; `format = N` (`CONFIG_FORMAT = 1`) newer than the code refuses writes; renamed keys migrate (`_migrate_renamed`).

**`ddflow config --explain`** prints `(key, value, source, doc)` for all knobs (`Config.explain`, 2,171 lines of output); `Config.sources[key]` holds only the layer name. No file/line provenance; dynamic keys (`gate.*`, reviewers, nested `export.<doc>.*`) have no source at all (DX-shadow-dynamic-keys).

**Writer.** `ddflow config KEY VALUE [--local]` / `--set` / `--append-toml` and MCP `ddflow_configure` go through `api/setup.py::configure(ConfigEdit)` to `services/configwrite.py::apply_edit` (823 lines): tomlkit edit (`tomlcfg.upsert`) keeping comments, file lock, validation of the MERGED text with `Config.check`, and guards (a pipeline may not name an undefined gate; `gate.<id>.human` never writable; command reviewers operator-only, D-reviewer-trust; plain keys, D-plain-keys). `services/config_shadow.py::shadow_report` warns when `local` or `env` shadows a committed write (only `section.knob` keys). Knob writes and hand edits are not logged; only flow choices, export selection, tool-written reviewers and approvals have events (P-configurable.event, B-org-events).

**Pipelines/workflows.** Stored in `[gates]` knobs (`task_pipeline, doc_pipeline, phase_pipeline, promotion_pipeline, kind_pipelines` table, `required`, `evidence_required`, ...) and `[gate.<id>]` tables. Edited by `ddflow workflow pipeline|gate|drop|state` (MCP `ddflow_workflow_*`) which call the same writer. Gaps: `doc_pipeline` has no workflow verb (DX-workflow-pipeline-doc); an agent can weaken a pipeline at once (B-org-agent-guard). Workflow choices (`flow.*`: trunk/gitflow, merge/pr, ...) live in the LOG, with config winning.

**Prompts, templates, rules.** `[prompts]` knobs hold optional paths; resolution is config path > `.ddflow/prompts/<name>.md` > packaged `ddflow/templates/prompts/*.md` (`services/overlay.py`, `services/prompts.py`, `ddflow prompts eject`, shipped-digest marker for drift). Export templates and commands follow the same overlay. Rules are `.ddflow/rules/<id>.toml` files rendered from events (D-unify). Jinja is already a runtime dependency; prompt rendering uses a plain `jinja2.Environment(StrictUndefined)` (`prompts.py:342`, `# nosec B701`), export rendering a `SandboxedEnvironment` (`export/registry.py`).

**Upgrade manifest.** `ddflow/templates/upgrade/changes.toml` plus one fragment per change in `templates/upgrade/unreleased/` (129 files today: 122 `knob_added.*`, 7 `event_kind_added.*`). `ddflow version lint` compares code knob defaults with the manifest replay and blocks a release on a gap; `ddflow upgrade` (`services/upgrade_apply.py`, `upgrade_manifest.py`) applies changes, never changing a value a layer set (D-upgrade-config-changes). Old-release project fixtures: `tests/fixtures/releases/0.1.3`, `0.1.18`.

**README knob pin.** `.ddflow/pins.toml` `[[pin]] readme-knobs` regenerates the `README/knobs` region from `Config()` defaults via `python -m ddflow.views.knob_table` (`views/knob_table.py`), checked by `tests/test_readme_knob_table.py`.

**Constants audit.** `docs/ddflow/constants-exemptions.toml`: 130 `[knob]` constants still to migrate to knobs (value = proposed key) and 59 `[exempt]`; `services/constants_audit.py`, `tests/test_constants_ratchet.py` (only shrinks). Migration waves: P-configurable.migrate-1/2.

**init / adopt / onboard.** `services/adopt.py::init_files` (shared by `init`, `adopt`, MCP `ddflow_setup`) writes `.ddflow/.gitignore` and, only if absent, `starter_config()` (a Python string, mostly comments plus `[gate.unit_tests]` guidance) as `.ddflow/config.toml`. "The starter config is the project's once written." `api/onboard.py` and `api/setup.py` add hooks, MCP launch lines, drivers, agent rules. No default snapshot of pipelines or knobs is written; a fresh project's behaviour is therefore "whatever this ddflow version's Python defaults are".

## 2. Performance (measured)

| Item | Measurement |
|---|---|
| bare `python -c pass` | 0.02-0.04 s, 9 MB RSS |
| `import ddflow.config` (35 sections, 314 knobs) | 0.15 s wall, 17 MB; -X importtime cumulative 79-108 ms (dataclasses 24 ms, `config_sections` package self ~33 ms) |
| `import ddflow.surfaces.cli` | 0.58 s cumulative (`ddflow.api` 0.52; `ddflow.config` 0.08 of it) |
| `ddflow --version` | 0.72 s wall, 49 MB |
| `ddflow hooks prompt` | 0.78 s, 48 MB |
| `hooks status`, `config --explain`, `brief` | 2.7 s, 2.7 s, 12.5 s; 283-287 MB (log fold, not config) |
| `Config.load(repo)` here | 1.9-2.4 ms (cProfile: 5 ms with profiling; 2 TOML reads) |
| TOML parses per command | `--version`: 22 `tomllib.loads` (4.9 ms), all `services/harnessreg.py:291` harness descriptors parsed at import. `hooks prompt`: `Config.load` x1; `config.toml` read 3x, `local/config.toml` 2x, `local/gates.toml` 1x; 28 parses, 6.7 ms. `brief`/`hooks status`: `Config.load` x2, `config.toml` 4x, `local/config.toml` 3x, 30 parses, 7.4 ms. `list task --all`: 13 ms config load total |
| caching | None. `Config.load` re-reads and re-validates every time and several loaders (gates, reviewers, companions, macros, provenance, shadow) read the same files again. Dedupe of warnings only (`_WARNED`) |
| import cost, warm | tomllib 2.7 ms (28 ms the first cold time), tomlkit 54 ms, jinja2 86-90 ms (20 ms when something else already pulled most of it in) |

Config parsing is 5-13 ms per command (1-2% of the 0.72 s floor); the config-related cost is the package import (80-100 ms), paid by `--version` and every hook.

**dynaconf 3.3.5** (zero third-party dependencies; vendors its own tomllib/box/toml; requires Python >=3.10; installs 1.2 MB):
- `import dynaconf`: 127-166 ms cumulative on this machine, 89 modules, 24-26 MB RSS (vs tomllib alone 13.8 MB, bare 9 MB). It imports jinja2 eagerly. With jinja2 already imported: 66 ms. Standalone it is slower than the whole current `import ddflow.config` (80-100 ms) and would ADD to it, since dynaconf does not replace the dataclasses, validators, docs, strictest-fallback or renames (it has its own `Validator`, which would be a second validation system).
- Constructing `Dynaconf` with 9 files and loading: 9.3 ms, versus 1.5 ms for plain tomllib reading the same 9 files. Fine per se.
- Estimated impact: +~70-150 ms to every process that loads config (every hook, `--version` unless config is lazy), about +10-20% on a 0.72 s floor, directly against D-perf-core-wave / B-perf-lazy-cli (target: `--version` under 4x bare Python startup). Mitigation: import it lazily, only for projects using dynamic values, or cache the resolved config (risk 7).
- Dependency: runtime dependencies are an allowlist (`scripts/ci/deps-allowlist.py`: `ALLOWED = {"jinja2","tomlkit"}`, pre-commit hook `deps-allowlist`, CI "Dependency audit"). D-unify (1) says "Runtime deps: Jinja2 + tomlkit (declared, not vendored); README says two". Adding dynaconf needs an amending decision, an allowlist edit, a README/docs change, and either a `[config]` optional extra with a fallback or making it required.
- Capability gap: dynaconf reads, merges and templates; it does not write comment-preserving TOML (its `loaders.write` rewrites a file). The writer stays tomlkit-based `configwrite`. So the operator's "managed by dynaconf" cannot cover the write side.

## 3. Current defaults: where they live, how they reach projects

- All-project defaults: dataclass defaults in `config_sections/*.py`, `DEFAULT_GATES` (Python); prompts/templates as package files; harnesses/companions as packaged TOML. A project overrides by writing only changed keys into `.ddflow/config.toml`.
- Reaching `init/adopt`: nothing is copied; the starter is a small stub. A clone gets defaults by running the same ddflow version; another version changes behaviour silently, except `flow.*` choices frozen by `adopt_defaults`.
- Different org: unsupported today; P-org-config is the plan: a "bundle" is "a directory mirroring `.ddflow/`" (config.toml, gates.toml, reviewers.toml, companions.toml, macros.toml, rules/, prompts/, help/, templates/export/, doctypes/, schedules/, triggers/, agents/ ...) fetched from git/HTTPS, pinned in a committed `.ddflow/org.lock`, layered `shipped < company defaults < project < local < env/CLI < company enforced` (D-org-config 4).

Observation: `default.toml` + `conf.d/` is structurally the "shipped" layer and the P-org-config bundle directory. Built in isolation it would be a second, incompatible layering mechanism.

## 4. Integration map

Constraining decisions: D-universal-principles and r-everything-configurable (every tunable a knob settable by file, CLI, MCP and prompt; definitions are records; changing a default never overwrites a set value; all logged); D-company-workflows / r-company-workflows (layers system, company, project, user; floors; shipped examples copied, never edited in place); D-org-config (layer order, project may name sources, fail open); D-compat (an older ddflow preserves what it does not understand); D-unify (1) (two runtime deps; strangler + goldens first); D-upgrade-config-changes; D-reviewer-trust; D-plain-keys; D-no-own-services-local-dir; D-perf-core-wave (now inside D-roadmap-order: hook latency first); D-roadmap-review; D-layer-model-strict.

Verdicts: BUILD-ON, MERGE, REPLACED, WAIT, ORDER.

### Core of the overlap
| Task | Interaction | Verdict |
|---|---|---|
| B-org-layers (p41) | Its text: "Today Config.load hard-codes default -> file -> local -> env ... Replace with one LayerStack whose entries carry (layer, source, rev, path)". This IS the loader change the operator asks for, plus org. Also specifies list semantics (append+dedupe, `extend-<key>`, `<key>.replace`) which clash with dynaconf `@merge`/`dynaconf_merge`. | MERGE: make the new layer stack the first deliverable and let B-org-layers inherit it; pull it ahead of the org fetch/trust tasks (it currently needs B-org-trust and sits behind P-unify, P-docs-generic, P-roadmap, P-docs-search). Decide the list-merge dialect once. |
| B-org-bundle (p33) | Bundle = directory mirroring `.ddflow/`. `default.toml` + `conf.d/*.toml` should be the SAME layout, so a shipped default, an org bundle and a project are the same shape. | MERGE / BUILD-ON: define the layout once; ship ddflow's own defaults as the lowest bundle. |
| B-org-events, P-configurable.event | `config.changed` events from "the one writer". Required by the operator's "kept in the config file ... so another clone continues" ONLY partly: the file is the source of truth, events add history/restore. | BUILD-ON: do the single writer + event in the same change as the conf.d-aware writer (the writer must know which file a key belongs to). |
| P-configurable.sync | Notice hand edits of config files. With conf.d there are N files; a content digest per file/layer is natural. | BUILD-ON; reorder after the layer stack. |
| B-org-provenance (p41) | per-value provenance (layer, rev, file, line); conf.d makes "which file" central; line numbers need our own parse, not dynaconf. | BUILD-ON; implement in the layer stack. |
| B-org-parity | A registry of configurable targets generating CLI/MCP/prompt/docs from one declaration. The natural place to declare "this knob belongs to conf.d/gates.toml, default layer = local/committed" (also DX-machine-local-keys). | BUILD-ON; may absorb DX-machine-local-keys. |
| P-org-config as a phase | Needs P-unify, P-docs-generic, P-roadmap, P-docs-search (priority 41). | ORDER: split off "layer stack + conf.d + default.toml + single writer" as an early phase before these (it only needs P-unify's tomlcfg). Operator decision needed. |

### Defaults, pipelines, templates
| Task | Interaction | Verdict |
|---|---|---|
| P-configurable (audit, migrate-1/2, new-work) | 130 constants still to become knobs. Declaring them in `config_sections` Python (current) vs in `default.toml` decides where the default lives. | ORDER: decide the default-source question BEFORE migrate-1/2, or each constant is migrated twice. |
| Upgrade manifest (122 `knob_added.*` fragments) + `version lint` | Lint compares code defaults to manifest replay. If defaults live in default.toml, the lint reads that file. Per-topic default files would give natural fragment targets. | Must be changed in the same release as the move; keep fragment format. |
| DX-registry-files-shared | Adding a section edits ~8 shared registry files; a `conf.d` loader that discovers per-topic files could remove the `config.py` import/field list edits. | SIMPLER if the design makes section registration declarative; sequence together. |
| DX-machine-local-keys | Classify keys with a default layer. Fits a `layer = local` annotation per knob/file. | MERGE into the new design (B-org-parity). |
| DX-shadow-dynamic-keys | Source tracking for gate.*, reviewers, nested keys. A unified layer stack with per-key provenance fixes it. | REPLACED by the stack. |
| DX-workflow-pipeline-doc, B-pipe-* (P-goals: B-pipe-model, B-pipe-resolve, B-pipe-agent, B-pipe-preview, B-pipe-acceptance), B-goal-model | Pipelines gain record-derived requirements resolved at claim. The "base" pipeline would come from conf.d/workflow. | BUILD-ON (base pipeline source); keep `kind_pipeline(cfg, kind)` as the single reader. |
| SE-workflow-template, B-rw-workflow, RF-template, TF-kinds, GV-workflows, IA-pack, CN-workflows, SE-packs | "Workflow template layered shipped < org < project < local, generates `kind_pipelines`". Template engine overlaps conf.d. | BUILD-ON: templates are conf.d fragments + a record kind; do not invent a fifth format. |
| B-tpl-*, B-dt-spec (P-docs-generic, P-doc-templates), B-docs-onboarding, B-docs-lint-and-links | `[docs]` section and document types: "TOML schema loader, shipped -> project -> config, extends". Same layering again. B-docs-onboarding writes `[docs]` defaults at adopt time. | BUILD-ON the same layer stack; B-docs-onboarding becomes "write conf.d/docs.toml". |
| GV-preset (optimize_for) | "compiled into" tier offsets, "through the layer stack"; a preset is a defaults bundle. | BUILD-ON conf.d: a preset = a conf.d fragment; needs B-org-layers already (declared). |
| P-agent-parity (descriptor-v2, tool-profile, permissions) | Descriptors are already shipped TOML, "every value user-overridable": same layering. | BUILD-ON; no conflict. |
| B-gap-command-trust (deferred, research first) | Verified in repo: `command_provenance` only distinguishes layer for a pass; no approval-by-digest of a changed gate/hook command. | WAIT, but promote: templated config makes it more important. |

Not found: any task mentioning `dynaconf`, `conf.d` or `default.toml` (1,116 open tasks searched); ids `B-docs-config` and `DX-update-globs...` do not exist (nearest: B-docs-onboarding).
Also touched: B-org-policy/B-org-agent-guard (floors and `tier=policy` knobs; the mechanism that lets agents participate while the default process stays protected; add `doc_pipeline`/`kind_pipelines`; wait for the stack), P-storage (ST-config-overlay, B109/B110: a seam behind `tomlcfg` means conf.d reads must go through it; B-arch-next-unify counts 16 direct tomllib sites, unify first), P-schedule (shipped jobs are `.ddflow/schedules/*.toml`: same idiom, one loader), P-observe (self-tuning needs the single reversible writer), B-arch-layer-contracts (`core -> config` baseline), P-daemon (a config digest would help change detection), P-fleet/P-control-planes/P-remote.config (each adds sections; use declarative registration), P-labels/P-sdd/P-plan-modes (conformance paragraph only), B-mu-generated-files (conf.d reduces config conflicts), B-org-sources/lock/sync (wait for the stack), B-org-kinds/authoring/surfaces/acceptance (wait; `org init` would emit the conf.d scaffold), P-change-surface.split-src-2 and the hot-file globs (config.py is hot: serialise or coordinate).

## 5. Risks specific to this codebase

1. **Trust boundary.** A cloned project's config is untrusted. Today gate commands in committed config run with the operator's rights with no approval by digest (B-gap-command-trust, deferred; `command_provenance` only tags the layer). dynaconf raises the stakes (verified in 3.3.5 source): `@jinja` is sandboxed, but `@format` gives `str.format` access to `env` (os.environ: API-key exfiltration) and `this`, and a `dynaconf_hooks.py` next to any loaded settings file is imported and executed (`execute_module_hooks`); `.py` settings are exec'd. Needed: `.toml` only, hooks and py loaders off, expressions limited to a ddflow-owned variable whitelist without raw `env`, and expression-bearing files inert until approved by digest (the B-org-trust executable-content rule, D-reviewer-trust). Existing exposure: project prompt overrides render in a NON-sandboxed Jinja environment (`prompts.py:342`).
2. **Reproducibility.** A fresh clone must run identically; values resolved from env or machine state break that unless made explicit in a committed file. The goal (another clone continues from the stored process) means `init/adopt` should materialise defaults into committed `conf.d/` files; otherwise a ddflow upgrade silently changes everyone's pipelines (only `flow.*` is frozen today).
3. **Committed vs local vs log.** Three stores today: committed files, `.ddflow/local/`, and the log (`flow.*`). Moving `flow.*` into files is a migration with replay consequences (config already wins over the log).
4. **Concurrent agents.** Per-topic files cut textual conflicts, but the writer must lock the right file, `Config.check` must validate the merged multi-file result, and `conf.d` order must be deterministic (lexicographic).
5. **Existing projects.** This repo's `config.toml` is 109 lines. The loader must read the old single file unchanged (D-compat) and never rewrite it silently; `ddflow upgrade` may offer a split with operator confirmation (D-upgrade-config-changes). An older ddflow ignores `conf.d/` and runs on defaults without error, a silent change: declare it with `format = 2` (older ddflow then refuses writes).
6. **Pinned outputs.** `test_readme_knob_table.py` (README region), the knob ratchets (`test_config_knobs_declared`, `test_ratchet_no_dead_knobs`, `test_constants_ratchet`), the writer/layout/forward-compat/shadow tests (`test_config_*.py`, `test_plain_config_keys.py`), 422 test files mentioning config, golden snapshots and `result_schemas.json`, the setup-ops transcript, and `tests/fixtures/releases/*`. B-org-layers already demands a byte-identical `--explain` golden when no org is used; adopt the same acceptance.
7. **Performance.** dynaconf adds +70-150 ms and a second validator; no cache exists. Safer: keep `Config` as the typed interface, add a cache keyed by (path, mtime, size) of all layer files, and import any dynamic-value engine only when a file contains an expression marker.
8. **Not verified:** runtime with many `conf.d` files beyond a synthetic 9-file test; dynaconf on Python 3.11 (it declares >=3.10); whether further `[docs]`/daemon config-watching tasks exist beyond those listed.

## 6. Open questions for the operator

1. Is dynaconf itself required, or its capabilities (layered files, `conf.d`, merging, dynamic values, Jinja)? The latter can be built on tomllib, the existing jinja2 and `Config` without a third dependency, +70-150 ms or a second validator. Which wins if they conflict with hook latency and D-unify (1)?
2. Which "dynamic properties": derived values, environment lookups, computed lists, conditionals? May a project file read environment variables at all, and must an expression-bearing file be approved by digest first?
3. Where should defaults live: Python dataclass defaults (docs, validators, README pin, upgrade lint rely on them) or a shipped `default.toml` as the single source (then Python reads it, and the README pin and manifest lint change)? Should `init/adopt` copy the defaults into the project (`conf.d/`) so another clone is immune to ddflow default changes, accepting that the project then does not receive improved defaults unless it runs `ddflow upgrade`?
4. Should this feature be pulled ahead of P-org-config (it needs only P-unify) and become the layer stack that B-org-layers/B-org-bundle then reuse, or built as part of P-org-config? Which list-merge dialect: append+dedupe with `extend-<key>`/`.replace` (B-org-layers), or dynaconf-style `@merge`?
5. Agent-made changes (pipeline order, gate edits): written to committed `conf.d/*.toml` at once, or proposed first for policy-tier knobs (B-org-agent-guard)? Which topics get which file, and which are machine-local by default (DX-machine-local-keys)?
6. Should `flow.*` choices recorded in the log move into the config file as the source of truth (log keeps history)?
7. Existing projects: split a 100+ line `config.toml` automatically, on offer, or never?
8. User (`~/.config/ddflow`) and system (`/etc/ddflow`) layers for ordinary config now, or only for org sources (D-org-config)?
