# ddflow configuration: Dynaconf, alternatives, drop-ins, templating safety

Research date 2026-10-17. Legend: **F** verified (source read or run here), **I** inference, **U** unverified.
Measurements: this machine, CPython 3.13.12, warm disk cache, best of 3, packages installed into `/tmp/claude-1001/scratch-config` only (dynaconf 3.3.5, pydantic-settings 2.16.0, omegaconf 2.4.0, tomlkit 0.15.2, jinja2 3.1.6, confuse 2.3.0, python-decouple 3.8). Scripts live in `/tmp/claude-1001/scratch-config/t1/`. Numbers are noisy at the +-20% level; treat them as orders of magnitude.

## 0. Starting point in the repo (F)

ddflow already has: `tomllib` readers (`ddflow/infra/tomlcfg.py`), two layers (`.ddflow/config.toml` committed, `.ddflow/local/config.toml` git-ignored; `ddflow/config_sections/_layers.py`), a drop-in-like loader for `<id>.toml` directories (`services/tomldir.py`, file-name order), merge-conflict detection (`ConfigConflict`), and a validated, comment-preserving writer built on **tomlkit** (`services/configwrite.py`, with guarded keys: `gate.<id>.human`, reviewers). Both `jinja2` and `tomlkit` are already runtime dependencies (`pyproject.toml`). So most of the request is already half-built; the missing pieces are a packaged `default.toml`, a `conf.d/` layer, provenance output, a managed file, and safe templating.

## 1. Dynaconf

### Version and health
- F: 3.3.5, released 2026-08-05; requires Python `>=3.10,<3.15` (covers 3.11-3.13); no mandatory dependencies (TOML parser is vendored; `jinja2` optional) (PyPI JSON, fetched 2026-10-17). Release cadence: 3.2.12 (2025-10), 3.2.13 (2026-03), 3.3.0 (2026-06-24), then 3.3.1-3.3.5 within six weeks. Repo pushed 2026-10-01, ~169 open issues, not archived (GitHub API). https://pypi.org/project/dynaconf/ , https://github.com/dynaconf/dynaconf
- F: **CVE-2026-33154 / GHSA-pxrr-hq57-q35p (high, published 2026-03-18)**: `@jinja` in <= 3.2.12 used an unsandboxed environment; RCE via `cycler.__init__.__globals__.os.popen`; `@format` allowed object-graph traversal exposing environment. Patched in 3.2.13. https://github.com/dynaconf/dynaconf/security/advisories/GHSA-pxrr-hq57-q35p . Implication: the template feature has a short security history; a pin `>=3.2.13` is the floor, and the post-fix sandbox is what I audited below.

### Layering, merging, includes (F, tested on 3.3.5 with a 3-file conf.d)
- `settings_files=["default.toml","config.toml","conf.d/*.toml"]`: later wins. Globs are **sorted alphanumerically** (`base.py` ~line 1572) but any path containing `.local.` is moved **after** all non-local paths of that glob (`conf.d/10-x.local.toml` would load after `conf.d/99-z.toml`). Each listed file also auto-tries a sibling `name.local.toml`.
- **Default merge is shallow at top-level tables**: without `merge_enabled=True`, a later file's `[ddflow]` table replaces the earlier `[ddflow]` table entirely (my test lost `ddflow.name` and the `gate` sub-tables and then failed a `{this.ddflow.name}` lookup). With `merge_enabled=True` tables merge recursively, scalars replace, and **lists are appended** (a,b,c + x + g1,g2 ...).
- List pitfalls (F): `merge_enabled=True` plus an explicit `"dynaconf_merge"` token duplicated earlier list items (`['a','b','c','x','a','b','c','x','m1']`). With `preload=[...]` and `merge_enabled`, list contents multiplied (repeated `m1,g1,g2` blocks). Includes behaved sanely (`a,b,c,x,m1,g1,g2,g3`). There is no "replace this list" in merge mode except per-key `@del`/`@reset` style tokens; replacing means turning merge off for that key.
- Tokens present in source (`utils/parse_conf.py`): `@merge`, `@merge_unique`, `@insert [index]`, `@del`, `@none`, `@int/@float/@bool/@str/@json`, `@format`, `@jinja`, `@get`, `@read_file`, `@note`. `@merge_unique g2,g3` worked as documented. The public docs fetched cover only `@format/@jinja/@get/@read_file` and hooks, not `@merge/@insert` (docs gap, U for exact doc wording). https://www.dynaconf.com/dynamic/
- `includes`: loaded after the main files; `preload`: before. Environments (`environments=True`, `[default]/[production]` tables, `from_env("production")`) work; the cost is that every TOML file needs those wrapper tables, which conflicts with plain `[section]` files.
- File discovery walks **up from the entry point to the filesystem root** and also tries a `config/` subfolder (docs). For a tool that runs inside arbitrary clones this must be disabled with absolute paths or `root_path`. https://www.dynaconf.com/settings_files/

### Dynamic values and jinja (F, source `utils/parse_conf.py`)
- One **module-global** `jinja2.sandbox.SandboxedEnvironment()` created at import, with the filters `abspath/realpath/relpath/dirname/basename` added. **No extension hook, no custom environment, no allow-list**; `jinja_extensions` is not a setting. To get custom filters/extensions you would have to monkey-patch `dynaconf.utils.parse_conf.jinja_env`. `{% include/extends/import %}` fail (no loader). Lazy values evaluate on access, with context `{"env": os.environ, "this": settings}`.
- Tested against a hostile file (3.3.5):
  - dunder escapes (`''.__class__.__mro__`, `cycler.__init__.__globals__`, `range.__globals__`) return empty with a "Unsafe access attempt" warning: the sandbox works.
  - **`{{ env.HOME }}` and `{{ env.get('PATH') }}` render**: any template can read every environment variable (API keys) into a config value, which ddflow would then print, log or send.
  - **`{{ env.pop('HOME') }}` mutated the process environment** (`HOME` really was removed afterwards): the sandbox lets public methods of passed objects run.
  - `{{ this.set('pwn','1') }}` mutated settings; `{{ this.load_file('/etc/hostname') }}` executed (a template can make the loader read and merge another file).
  - `@read_file /etc/hostname` reads any file; `@format {env.__class__}` leaks object info.
  - `{{ 7*7 }}` and loops are unbounded-capable: Jinja's own docs state the sandbox does not stop CPU/memory exhaustion. https://jinja.palletsprojects.com/en/stable/sandbox/
- Verdict (I): in 3.3.5 `@jinja` is "no arbitrary code execution" but **not safe for untrusted config**, because the context is the live environment and a mutable settings object.

### Validators, hooks, writes
- F: `Validator(name, default=, must_exist=, is_type_of=, gte=, eq=, when=Validator(...))` works, and defaults are applied by `settings.validators.validate()`. Type handling of TOML is plain `tomllib`-like; `@int` is only needed for env/strings.
- Hooks: `HookableSettings` lets you intercept access (docs). Custom loaders exist (`LOADERS_FOR_DYNACONF`); not tested.
- **Write-back (`dynaconf.loaders.toml_loader.write`) does not preserve comments** (F). Tested: it re-serialised `c.toml`, dropped `# human comment` and the trailing comment, expanded `list = [1,2]` into a multi-line array and rewrote other tables. Writing to a fresh file lost nothing but also has no provenance header. Dynaconf has no comment-preserving writer; you must bring tomlkit anyway.

### Key handling (F)
- Top-level keys are upper-cased in `as_dict()` (`GATE`, `DEFAULT`); nested keys keep their original case but lookup is case-insensitive (`s.gate['my-gate'].TIMEOUT` found `Timeout`). Hyphenated keys survive (`s.get('hyphen-sec')`), but attribute access (`settings.hyphen-sec`) is impossible and dotted access (`get('gate.my-gate.timeout')`) works. Two keys differing only by case would be ambiguous (I). ddflow ids such as `B-onboard` or gate names are hyphenated; this needs `[]` access everywhere.
- `Settings` is mutable global-ish state; thread-safety guarantees are not documented (U; GitHub issue #21 "Write a test_multiple_threads" is closed, I did not read the outcome).

### Cost (F, 300-key TOML file, 30 tables)
| | import | load 300 keys | max RSS (python baseline 9 MB) |
|---|---|---|---|
| tomllib | ~25 ms (incl. interpreter work) | ~3 ms | 14 MB |
| tomlkit | ~35 ms | ~13 ms | 16 MB |
| jinja2 sandbox | ~59 ms | 2 ms/render | 20 MB |
| **dynaconf** | **~120 ms** (`-X importtime`: 151 ms cumulative; `parse_conf` 73 ms, mostly jinja2) | ~16 ms (28 ms for 3 merged files) | **26 MB** |
| pydantic-settings | ~206 ms | n/a | 32 MB |
| omegaconf | ~74 ms | 3 ms | 20 MB |
| confuse | ~65 ms | n/a | 15 MB |
| python-decouple | ~14 ms | n/a | 10 MB |

Dynaconf imports jinja2 eagerly (about half its import time). Since ddflow already depends on jinja2, the marginal cost over today's path is roughly 60-90 ms per cold process, plus ~10 MB. For a hook that fires every agent turn, that is a 3-5x increase over a bare `tomllib` start (I).

## 2. Alternatives

| Option | Layering / drop-ins | Interpolation / templating | Validation | Comment-preserving write | Dynamic-value security | Weight | Fit for ddflow |
|---|---|---|---|---|---|---|---|
| **In-house `tomllib` loader** (today) + tomlkit writer | whatever we code; already 2 layers; add conf.d | none yet; add subset | typed sections exist | **yes (tomlkit)** | no dynamic values, so none | 0 new deps; ~25 ms | best base |
| Dynaconf 3.3.5 | files, globs, `.local.`, includes, envs | `@format`, `@jinja` (sandboxed since 3.2.13) | Validators incl. defaults | **no** | env + settings exposed; mutation possible | 0 hard deps, ~120 ms, 26 MB | loader only; write and templating unfit |
| pydantic-settings 2.16 | list of TOML files; in my test for a `dict` field a later file **replaced** the earlier top-level table (observed, deep-merge only for nested models: U) | none (env/secrets sources only) | **strong, typed** | no | n/a | pulls pydantic + pydantic-core (compiled), dotenv, typing-inspection; ~206 ms, 32 MB | too heavy for hot path |
| OmegaConf 2.4 / Hydra 1.3.7 | structured configs, `OmegaConf.merge` (lists replaced, dicts merged), Hydra composition | `${a.b}`, resolvers, `oc.env`; custom resolvers run arbitrary Python | structured configs | no | resolvers are code; docs have no untrusted-config warning | PyYAML; Hydra adds antlr4; ~74 ms | interpolation is nice, but YAML-centric, no TOML reader (we'd feed it tomllib dicts, which worked) |
| python-decouple 3.8 | env/.ini only | none | casts | no | n/a | tiny, last release 2023 | not a fit |
| Confuse 2.3 | layered sources, templates for validation | none | templates | no | n/a | PyYAML; YAML-first | not a fit |
| Jinja2 alone | n/a | full | none | n/a | sandbox needs careful context | already a dep, ~59 ms | use as the template engine only |
| tomlkit 0.15.2 | n/a | n/a | n/a | **yes** (verified: comment and `list = [1,2]` formatting preserved after edit) | n/a | already a dep; parse ~4x slower than tomllib | write path only |

(I) No mainstream library gives layering plus comment-preserving write-back plus safe templating; the write path is always tomlkit.

## 3. Drop-in / conf.d practice

- **systemd** (F, man7 copy of systemd.unit(5)): `unit.d/*.conf` are merged in lexicographic file-name order after the main file, regardless of which directory holds them; a same-named file in a higher-priority directory (`/etc` > `/run` > `/usr/lib`) replaces the lower one wholesale; list-valued settings are **appended unless reset by assigning empty**; `systemctl edit` creates `override.conf` (the tool-managed drop-in), `systemctl cat` shows merged files with each file's path as a header. Conventional names are `NN-topic.conf`. https://man7.org/linux/man-pages/man5/systemd.unit.5.html (freedesktop.org returned 403). `systemd-analyze cat-config` as the generic viewer: from memory (U, not re-fetched).
- **git** (F, git-config docs): `include.path` text is inserted in place, last value wins; relative include paths are resolved against the including file; `git config --show-origin --show-scope --list` prints file and scope of every value; protected settings (e.g. `safe.directory`) are honored only from system, global and command scopes, never from the repo-local file. https://git-scm.com/docs/git-config
- **cargo** (F): configs found by walking parents; scalars deeper-wins, **arrays are concatenated** with higher precedence later; `include = [...]` (supports `{ path, optional = true }`) loads includes left to right and then the including file on top; paths in config files are relative to the config's directory; `cargo config get --show-origin` exists (U: flag name from memory). https://doc.rust-lang.org/cargo/reference/config.html
- nginx/apache/sudoers `include conf.d/*.conf` ordering follows glob (alphabetical) order and `sudoers` ignores files containing `.` or ending in `~` (U, from memory). Kustomize overlays are patch-based (strategic merge/JSON patch), not covered here (U).
- Practices to adopt (I): (1) numeric prefix + topic, `10-gates.toml`, `50-workflow.toml`, `90-managed.toml`; (2) specify merge rules once: tables merge recursively, scalars replace, **lists replace by default** (safer for `pipeline`, which is order-significant; cargo/systemd append which is wrong for an ordered pipeline) with an explicit opt-in such as `pipeline_append = [...]` or a table key `_merge = "append"`; (3) a `ddflow config show --origin` that prints `key = value  # conf.d/20-x.toml:7`, plus `ddflow config cat` that prints files in load order with headers; (4) only `*.toml` files, ignore `*.local.toml`/backups/editor swap files, sorted by a locale-independent byte order; (5) one topic per file so two branches editing different topics do not conflict; (6) machine-local overrides stay in `.ddflow/local/`, never committed.

## 4. Safety of dynamic config from a cloned repo

- Risks (F where tested above): SSTI/RCE if the environment is not sandboxed (CVE-2026-33154 above); even with a sandbox, objects passed in the context are the attack surface (`env.pop`, `this.set`, `this.load_file`); `{% include/import %}`/`extends` give file reads if a loader is configured; extensions and custom filters are arbitrary code; template DoS (Jinja docs); `@read_file`/`@get` read arbitrary files or settings; templates can exfiltrate secrets into printed config.
- Established patterns: `SandboxedEnvironment` or `ImmutableSandboxedEnvironment` with `StrictUndefined`, no loader, a context of plain dicts/strings only (copies, not live objects), allow-listed filters/globals, extensions only from the user's own config or installed packages, and a trust gate on the file.
- Comparable tools: **direnv** `allow` records approval in `$XDG_DATA_HOME/direnv/allow`, `deny` revokes (F; hash details not in the doc fetched, U). **mise** requires `mise trust` for configs with templates, env changes, code or tasks with secrets; trust is path-based (also applies to worktrees of the same repo), and `paranoid` mode ties it to file contents (F). https://mise.jdx.dev/cli/trust.html , https://direnv.net/man/direnv.1.html . **git** restricts sensitive keys to non-repo scopes (F). Hydra/OmegaConf resolvers are code and assume trusted configs (docs silent on untrusted, F). Pre-commit hooks run repo-defined code after install; Ansible vault/templating assumes trusted playbooks (U, from memory).
- Recommended trust model for ddflow (I):
  1. Untrusted by default: `.ddflow/config.toml` and `conf.d/*.toml` from a clone are **data only**. Plain values never need trust.
  2. A value is a template only if it contains `{{`/`{%`. Templates and any "executable" key (reviewer `command`, gate `cmd`, hooks) in the project layer are honored only after `ddflow trust` records, in the user's own state (`~/.local/state/ddflow/trust.toml`, outside the repo), the **sha256 of the normalized effective files** plus the repo path (digest, like mise paranoid / direnv allow). A changed digest means "untrusted, show diff, re-approve". This mirrors the existing reviewer approval (`services/reviewer_trust.py`).
  3. Untrusted templates are not rendered; the raw string is kept and `doctor` says so.
  4. Rendering: `SandboxedEnvironment(undefined=StrictUndefined)`, no loader, no extensions from the project; the context is `{"project": {...plain dict...}, "env": allow-listed names only, "cfg": deep-copied read-only mapping}`; a size/time cap on output (U for a portable time limit).
  5. Extensions/filters only from user-level config or installed entry points, never from `.ddflow/`.

## 5. Performance

- Measured: `tomllib` parses a 300-key file in ~3 ms; tomlkit ~13 ms (4x); dynaconf ~16 ms plus ~120 ms import (F). The hot cost is imports, not parsing.
- Practices (I): (a) read with `tomllib` on the common path, import tomlkit only on write; (b) import jinja2 lazily, only when a loaded string contains `{{` or `{%` (jinja2 sandbox import was ~59 ms here); (c) cache the merged, validated result as JSON in `.ddflow/local/config.cache` keyed by `(path, mtime_ns, size)` of every contributing file plus the ddflow version and trust digest; a cache hit means `json.load` (about a millisecond for 300 keys, U: not measured) and no validation; (d) never re-validate unchanged inputs; (e) resolve provenance lazily only for `config show --origin`; (f) glob once, sorted, no upward directory search.
- Cache must be per machine and git-ignored; it must never feed the trust decision (the digest is computed from the files, not the cache).

## 6. Persistent changes written by the tool

Options and practice:
- **Managed drop-in** (`conf.d/90-managed.toml`, or `.ddflow/managed.toml`), systemd `override.conf` / `systemctl edit` style: the tool owns the file and rewrites it wholesale (atomic temp + `os.replace`, as `configwrite` does now); humans own everything else; diffs and merges are tiny and conflicts are confined to the file; clear provenance via a header `# managed by ddflow - edit with 'ddflow config set', hand edits are kept only if...`. Downside: the value a person set in config.toml is overridden silently unless `config show --origin` tells them.
- **Rewrite config.toml with tomlkit**: keeps comments (F: verified; trailing comments, inline arrays and key order preserved), but every agent's tool write edits the file humans review, enlarging merge conflict surface; this is what ddflow does today.
- Hybrid (I, recommended): `config set` edits **in place in the file that currently defines the key** (found via provenance, tomlkit), and creates the key in `conf.d/90-managed.toml` only when no file defines it or when the defining file is the packaged default. Ordering data such as the pipeline is a whole-list replace in the managed file. Keep a `# managed by ddflow` header and a per-key comment (`# set by ddflow config set 2026-10-17`) only in the managed file. Keep the existing guards (`gate.<id>.human`, reviewers) on every write path, including the managed file.
- Atomicity and merge-friendliness: temp file in the same directory, fsync, `os.replace`; a lock file under `.ddflow/local/` to serialize concurrent agents; one table per line group, stable key order (sorted in the managed file) so two branches adding different keys merge textually; tomlkit round-trip for hand-edited files, deterministic dump for the managed file.
- Cloning: committed files (`default` packaged inside ddflow, `config.toml`, `conf.d/*`, managed file) carry the whole shared configuration; only `.ddflow/local/` and the trust store stay per machine, which satisfies "someone else clones and continues".

## 7. Architecture options

(a) **Adopt dynaconf wholesale.** Gains: globs, includes, validators, templating off the shelf. Costs (F): +~100 ms import and +10 MB on every hook; list-merge duplicates with `merge_enabled`/`dynaconf_merge`/preload; `.local.` ordering quirk; upward search; upper-cased/case-insensitive keys vs hyphenated ids; no comment-preserving write; no way to configure jinja extensions; live `env`/`this` exposed to templates; template RCE history; mutable global settings. It would replace the typed sections, not feed them.

(b) **Dynaconf only as loader behind the typed sections.** Reduces the above to: merge semantics, import cost, and safety flags you must pin (`jinja2` off, `@read_file`/`@get` off is not configurable: U, they are always registered in `converters`). You still need tomlkit for writes and your own provenance. Little is gained over (c).

(c) **Keep the in-house loader; add packaged `default.toml`, `.ddflow/conf.d/*.toml`, a managed drop-in, `config show --origin`, a cache, and a restricted template subset** (recommended).

(d) Variant: in-house loader plus **OmegaConf-style `${a.b}` interpolation only** (no code): implement a ~50-line resolver for `${section.key}` and `${env:NAME}` (allow-listed). It covers "dynamic properties" without Jinja at all, and Jinja stays reserved for prompts and for the trust-gated `{{ }}` case.

### Recommendation and staged migration (c)+(d)
1. Stage 1: load order `packaged default.toml` < `.ddflow/config.toml` < `.ddflow/conf.d/*.toml` (sorted by byte order, `*.toml` only) < `.ddflow/local/config.toml` < `.ddflow/local/conf.d/*.toml` < env/CLI. Tables merge, scalars replace, lists replace with documented opt-in append. Move today's defaults out of code into `default.toml`. Add `ddflow config show --origin` and `config cat`.
2. Stage 2: `conf.d/90-managed.toml` and the "edit where defined, else managed file" write rule; atomic writes + lock; keep guards; MCP and CLI use the same `configwrite` path.
3. Stage 3: `${...}` interpolation (no code), evaluated lazily and only for strings containing `${`.
4. Stage 4: trust store + sandboxed Jinja for `{{ }}` values and project-layer executable keys, loaded lazily; compiled cache keyed by mtimes and trust digest.
5. Split the existing 5 KB `config.toml` into topic files with a migration command (`ddflow upgrade`), leaving a small `config.toml` of project-specific values.

State plainly: **dynaconf is a poor fit for** (i) comment-preserving persistent write-back, (ii) configurable jinja extensions (not possible without monkey-patching), (iii) safe handling of an untrusted cloned config (live `env`, mutable `this`, `@read_file`), (iv) low import cost on a per-turn hook, (v) order-significant lists. It is a decent fit for layering, globs and validators, which ddflow can do with ~100 lines (it already does most).

## Could not verify
- Dynaconf thread/process safety guarantees; whether `@read_file`/`@get` can be disabled by setting; custom-loader behaviour; Dynaconf `@merge`/`@insert` documentation wording (docs page silent).
- pydantic-settings multi-file deep-merge rules beyond my single dict-field test; Hydra composition details (read only OmegaConf resolver docs).
- `systemd-analyze cat-config`, cargo `--show-origin`, sudoers ignore rules, kustomize, Ansible, pre-commit trust: quoted from memory.
- direnv/mise hash algorithms and storage (docs fetched do not state them).
- Timings: one machine, best of 3, no cold-cache runs; JSON cache hit speed unmeasured.

## Open questions for the operator
1. Is `default.toml` shipped inside the package (immutable, versioned) or copied into `.ddflow/` on setup (visible to the project, can drift)? I recommend packaged plus `config show --defaults`.
2. Should lists replace or append by default across layers? Pipelines argue for replace; reviewers/lists of globs might want append.
3. Where does the trust store live and does a changed digest block all project-layer executable keys, or only the changed file's?
4. Is `${...}` interpolation without code enough for "dynamic properties", or are Jinja conditionals/loops in config values a hard requirement?
5. Is the managed drop-in committed (shared across clones, as requested) or are some managed settings machine-local?
6. Hot-path budget: what is the acceptable per-hook latency (it determines whether dynaconf's ~100 ms is tolerable at all)?
