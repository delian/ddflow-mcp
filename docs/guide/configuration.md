# Configuration

How configuration works and the id schemes. The generated knob table stays in the README. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

## Configuration

675 knobs across 60 sections, every one documented in place and listed, with its default
and its values, in the [table below](../../README.md#all-knobs):

```console
$ ddflow config --explain --filter lease
lease.ttl_s = 1800   [default]
    Seconds a lease stays valid without a heartbeat. After this it is EXPIRED and
    reclaimable. Longer = fewer false expiries when an agent is deep in a slow gate;
    shorter = faster recovery after a crash.
```

Resolution: dataclass defaults → `.ddflow/config.toml` → `DDFLOW_<SECTION>_<KNOB>` env.
An unknown knob **in a file** is never a silent drop: ddflow skips it, so a config written
by a newer ddflow does not stop an older checkout, but it warns on every command and
`ddflow doctor` reports it as a problem (a typo, or a config newer than this code — merge
main). An enumerated knob (`[enforce].stale_docs = block | warn | off`, `[flow].model`,
`[lease].reclaim_policy` and every other one whose doc lists `a | b`) is held to its
declared values (`KNOB_CHOICES` in `ddflow/config.py`): a value outside them in a file is
warned about and reported the same way, and the knob takes its **strictest** allowed value
(`KNOB_STRICTEST`), not its default, so a typo makes ddflow more careful —
`stale_docs = "blok"` acts as `block`, `[upgrade].skew = "refusee"` as `refuse`. A numeric limit can fail closed the same way: `[triggers].max_fires_per_hour = -1`
(or `"ten"`) in a file acts as `0`, and no trigger fires until it is fixed. Each
knob's doc (`ddflow config --explain`) names its fallback and why; where a knob has no
safety dimension the fallback is the value that does the most checking or changes least
(`[ci].on_merge` → `full`, `[mcp].tools` → `all`, `[export].refresh` → `off`), and the
`doctor` line names the value in effect. A fallback never makes ddflow act outside this
clone (decision D-fallback-no-remote): `[flow].integration` falls back to `merge`, not
`pr`, and `[flow].claims` to `local`, not `remote`, so a typo never pushes, opens a pull
request or writes claim refs -- that happens only when someone sets it correctly. That
rule outranks strictness: a typo in a deliberately set `pr` or `remote` loosens approval
or claim exclusivity until it is fixed, and `doctor` names it (`KNOB_OUTWARD` lists each
knob's outward values). From the environment it is refused, and so
is a typo in ddflow's own source tree, where the config and the code are one commit. `ddflow config --set` refuses an unknown knob (exit 1) or an invalid
value of a known one (exit 3, naming the key) outright, before writing. A map or list knob (`list[str]`, `dict[str, str]`) given by environment as JSON
refuses a non-string element instead of casting it (`null` is not the string `"None"`).
A test asserts every knob carries documentation, so the reference cannot rot.

**Config across versions (D-compat).** An older ddflow changes the keys it knows and keeps the
rest of the file untouched. `ddflow config --set`, `--append-toml` and `ddflow_configure`
judge the keys they WRITE strictly (an unknown one is a typo, exit 1) and everything else the
file already holds the way a load does, so a section, knob or enumerated value a newer
release wrote passes through byte-identical instead of refusing every edit. A checked list or
table whose set of members grows (`[dedupe].kinds`, `[agent].routers`,
`[enforce].trailer_waivers`) ignores a member this version does not know, applies the rest
and says so (`ddflow doctor` lists each; if nothing usable is left the value below stays);
the environment and the write paths still refuse such a member. An optional top-level
`format = N` (a positive integer; none means 1) declares the config format: a file whose
format is newer than this ddflow understands (`CONFIG_FORMAT`, now 1) is read, as far as it is
understood, but every write to it is refused (exit 3) with the way to upgrade, because an
edit could lose what the newer one wrote; the format changes only when a section's shape
does, never for an added knob. A knob declares a changed default with
`knob(default_changed_in=, default_was=)` (shown in `config --explain`; a value anyone set
is never changed by it, D-upgrade-config-changes). Where a note tells you to catch up, the
advice fits how ddflow is installed (`ddflow doctor`, the skew refusal and the startup
warning): a source checkout merges main, an index install upgrades `ddflow-mcp`
(`uv tool upgrade`, `pipx upgrade`, `pip install -U` or `uvx --refresh`), a VCS or
directory install reinstalls from its source. `ddflow doctor` also lists the fields an older
ddflow skipped in `[gate.*]`, `[[reviewer]]`, `[[companion]]` and `[[macro]]`, not only on
stderr.

### The README and these guides are managed documents

`README.md` and `docs/guide/*.md` are registered as evergreen document types (the contract the docs check validates, not a lock
on the file) by
`.ddflow/doctypes/readme.toml` and `guide.toml` (the glob-to-contract map of D-readme-split-r2:
README.md and each guide are `evergreen`; the five tables between `ddflow:begin README/...`
markers are generated and stay in the README). `ddflow docs check` validates them like any typed
document: the README must keep its front-door sections in order, and a guide may not skip a
heading level. What keeps the generated tables true is not this registration but the checks that
already exist: the five staleness tests (`tests/test_readme_toc.py`, `test_readme_command_table.py`,
`test_readme_agents_table.py`, `test_readme_tool_table.py`, `test_readme_knob_table.py`) fail when a
region differs from what its generator renders, the generators refuse to rewrite a region edited
by hand unless told to discard the edit, and the commit hook refuses a staged region whose body was
edited (`[enforce].generated_views`). Rewrite a region with `ddflow pins regenerate --only <recipe>`.

## Id schemes

Every id ddflow mints comes from a template in `[ids]`, one per record kind (decision
D-id-schemes-final). The defaults reproduce the ids ddflow has always minted, so a project
that sets nothing sees no change and an existing log replays exactly; a recorded id is never
renamed, and a changed template applies to records created afterwards.

| Kind | Default | Example |
|---|---|---|
| `bug`, `lesson`, `research`, `decision`, `memory`, `job` | `{prefix}{hash}` | `B9c56de9d58` |
| `session` | `s{time}-{pid}` | `s20261005T225456-2195563` |
| `fix_task` / `fix_task_followup` | `fix-{parent}` / `fix-{parent}-{seq}` | `fix-B9c56de9d58`, `fix-B9c56de9d58-2` |
| `promotion` | `promote-{env}-{seq}` | `promote-staging-1` |
| `ci_bug` | `Bci-{slug}-{digest}` (stable) | `Bci-tests-test-x-py-9f1c2a7b3d` |
| `split_child` | `{parent}.{seq}` | `P.T.2` |
| `imported_phase` / `imported_task` | `{user-text}` / `{phase}.{slug}` | `SESSION-ONE`, `P1.do-it` |

Tokens: `{prefix}` (the kind's letter), `{seq}`, `{date}`, `{time}`, `{pid}`, `{slug}`,
`{hash}`, `{parent}`, `{phase}`, `{env}`, `{user-text}`, `{digest}`. A template must hold a
source of uniqueness -- `{seq}`, `{hash}`, or `{time}` with `{pid}` -- or `{digest}`, which
makes it stable (the same content maps to the same id) and then allows only content
tokens. Two kinds are exempt: `fix_task`, minted once per bug, is unique by its `{parent}`,
and the imported kinds carry names the importer already makes unique. Outside its tokens a
template may hold ASCII letters (either case), digits, `.`, `_` and `-` only, because an id
is a file name, a branch name and a glob token. `ddflow config --set ids.bug "BUG-{seq}"`
sets one; an invalid template (`BUG-{date}`: nothing makes it unique) is refused, exit 3;
one in a file is warned about, named by `ddflow doctor`, and the default stays in effect.
`[bugs].phase`, the one fixed id, is held to the same characters.

Every place that mints a record id goes through one id service (`ddflow/core/ids.py`); a
ratchet test fails on a module that builds one of those ids by hand (the shapes the service
replaced: salted hashes, `fix-`/`promote-`/`Bci-` ids, session stamps, split children) or
reads a kind back from such a prefix. A minted id is checked against every
id the log holds -- and a key against every key, again under the log's lock as the record
is written -- and, when taken, gets `-2`, `-3`; a `{seq}` is the highest number that
template has used plus one, never reused. A stable template (`{digest}`) returns the same
id for the same content -- the same failing CI check is one bug while it is open -- and is
refused when the id is held by a record of another kind. When a kind whose shipped id has
no `{seq}` is given a `{seq}` template (`bug = "BUG-{seq}"`), the record keeps the internal
id ddflow always minted (`B9c56de9d58`), which every event names, and `BUG-1` is its KEY,
recorded on its event (`key`); resolving keys wherever an id is accepted is the aliases
task's.
