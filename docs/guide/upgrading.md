# Reading the log, upgrading and recovery

The event log, its compatibility contract, upgrades, repairs, crash recovery and reconstruction. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

## Crash recovery

An agent is killed. Nothing is cleaned up, because in a real crash nothing runs.

```console
$ ddflow recover
1 recoverable situation(s); 1 may contain work:

!! P1.T1  [expired_lease]  was: delta
     worktree /repo/.ddflow/worktrees/P1.T1
     INSPECT FIRST — 1 uncommitted file(s), 1 unmerged commit(s).
     `git -C .../P1.T1 diff main` then salvage,
     then `ddflow release P1.T1 --note salvaged`.
```

Four behaviours, each chosen against a specific way this goes wrong:

- **While the lease is live, nothing happens.** A dead agent is indistinguishable from a
  slow one until the lease expires, and guessing is how two agents end up in one tree.
- **Recovery measures the tree** — uncommitted files, unmerged commits — rather than
  trusting the recorded state. "Is there work in here?" is the only question that decides
  the remedy.
- **An expired lease is never stolen silently**, and `recover --apply` expires only trees
  it measured as *empty*.
- **Adoption, not duplication:** an agent resuming a recovered item gets the *existing*
  worktree back, not a second one beside it.
- **The brief leads with it.** `ddflow brief` names, before anything new, the trees
  holding work, the trees it COULD NOT MEASURE (treated as holding work) and the RUNNING
  items nobody holds -- up to five of each, the rest counted; leftovers with nothing to
  salvage are counted in one line. `recover` (its `!!` flag and "may contain work"
  count), `status` (`recoverable` and "may contain unsaved work") and `doctor` (a
  PROBLEM, not a note, for a tree) count the same three, so an unmeasurable tree is never
  reported as nothing to lose.

---

## Reconstruction from logs alone

```console
$ ddflow replay --out ./recovery-kit
wrote:
  recovery-kit/RECONSTRUCTION.md
  recovery-kit/QUEUE.md
  recovery-kit/LESSONS.md
```

`RECONSTRUCTION.md` is written as instructions to a fresh agent, not as a report about
the past: every operator prompt in order, every research verdict, every lesson, the
queue's shape — **and every approach already tried and rejected, with the measurement
that killed it.**

It states its own limit, in the document: it reproduces the *decisions*, not the bytes.
Model outputs are not deterministic, so replaying prompts will not recreate the original
source. What it recreates is every input that produced it, which no other artefact holds.

The demo destroys an entire repository and rebuilds from 3.9 KB of JSONL, then checks
nine specific fragments are present — including the operator's stated *reason* for a
constraint, and the probe output behind a rejected design.

**Secrets are redacted on the way in**, not on the way out — the log is committed, so a
scrub at read time is a scrub that `git show` walks straight past.

One shared `Redactor` (`core/redact.py`) serves the log, the views, the exports, upstream reports, each a
named profile (`redact_argv` gives the shape of a command line). The log profile is the full one: besides secrets (which keep their
surrounding words, `api_key: [REDACTED]`) it removes private addresses and hosts, this machine's hostname, home-directory paths,
emails, and (once the `[upstream]` section exists) its `redact_extra` names from the free text of every NEW event, not only prompts and notes:
`EventLog` applies it where every write passes: every string of an event's data is redacted unless it is a known lookup
(`core.redact.LOG_VERBATIM_NAMES`: ids, digests, shas, paths, globs; `LOG_VERBATIM_FIELDS`: a few per-kind pairs, each with its reason), so
gate evidence and output tails, bug summaries, lessons, decisions, task bodies, reasons, notes and a field the vocabulary has never heard
of are all redacted. `LOG_TEXT_FIELDS` names the fields that are text even where their name is a lookup name. A new event kind must be classified or `tests/test_log_redaction_all_kinds.py` fails; a new
field of a known kind that is a lookup must be added to those tables when the event-kind snapshot is regenerated, or it is redacted. A definition's digest (`def.recorded`) is of the redacted fields it
stores, with every redaction marker (`[REDACTED]`, `[REDACTED:secret]`, ...) read as one token
(`core.digest.normalized`), so clones on different redaction profiles agree on whether a definition
changed; a digest recorded before that still matches (`core.defs.same`). The duplicate check's
"identical text" digest does the same and also ignores case and whitespace (`core.digest.normalize_text`;
the similarity index re-derives once). A trigger definition's fingerprint is `core.digest.of_obj` of
its fields; a fire that carries the older spelling still counts as the same definition. Events already written are
not rewritten.

---

## Reading the log, and why it is never compacted

The log only grows, so every state-reading call used to re-read and re-parse all of it.
Measured at 20,000 events, that read costs **115 ms** — and the breakdown is the whole
design argument:

| stage | cost | share |
|---|---|---|
| `Event.from_json` | 97 ms | **84%** |
| `fold` into state | 9 ms | 7% |
| sort by Lamport key | 3.8 ms | 3% |
| read the bytes off disk | 3.7 ms | 3% |
| de-duplicate by content address | 0.7 ms | <1% |

Parsing dominates, and **an append-only file guarantees the bytes already parsed have not
changed.** So `EventLog.read_all` re-parses only the appended tail, and re-hashes the
bytes it is re-using to prove they are still the same bytes:

| events | read, uncached | warm read | |
|---|---|---|---|
| 20,000 | 121.1 ms | **11.9 ms** | 10.2× |
| 100,000 | 623.8 ms | **62.8 ms** | 9.9× |

A command like `ddflow doctor` — which reads four times — pays the full cost once instead
of four times.

**The sort and the de-duplication are maintained too (B169).** `read_all` keeps the merged,
Lamport-sorted, de-duplicated order between calls and folds each append into it by
bisection, falling back to a full rebuild whenever the shortcut could not be proven
identical (a shard whose bytes changed, a repeated event id, a batch of more than 64).
Measured, one appended event, `tests/bench_log_read.py`: 20,000 events 14.8 ms to 3.8 ms;
100,000 events 75.9 ms to 19.5 ms. What remains is reading and hashing the log's bytes,
which is the price of verifying them and is linear in the log.

**A one-shot CLI call no longer parses everything (B166).** Once a log has 5,000+ events,
`read_all` writes a snapshot of the parsed events and their merged order to
`.ddflow/local/read-snapshot.bin` (machine-local, git-ignored, never merged). A later
process loads it instead of calling `Event.from_json` on every line: 100,000 events cold
685 ms to about 400 ms (1.7x), 20,000 events 125 ms to 63 ms (2x), median of three runs
on a loaded machine. It is a cache
and is trusted only as far as it verifies: per shard, the SHA-256 of the bytes it
describes must still match the shard on disk (so a rewritten, truncated or switched-branch
shard is re-parsed, and an appended or torn tail is parsed as usual); the file's own
checksum, format, ddflow version and Event fields must match; any doubt means the snapshot
is ignored and the log parsed. `ddflow doctor`'s integrity check (`EventLog.verify`) never
uses it, and a log opened only to read another repository (`external sync`, exports)
neither reads nor writes one. It has the trust of the rest of `.ddflow/local/`: your own
checkout's machine-local state, never fetched or merged. Deleting the file is always safe; `DDFLOW_SNAPSHOT=0` turns it off for a
process (or `[log] snapshot = false`). It covers up to `max_cached_events` (a larger log is parsed each time, as
before). The table above is reproduced by `python tests/bench_log_read.py`.

Six knobs for reading the log, `[log]`:

| knob | default | what it trades |
|---|---|---|
| `reuse_parsed` | `true` | Off = always re-parse from scratch. Slower, and worth it only if a shard is being rewritten in place under a running process. |
| `max_cached_events` | `100000` | Memory ceiling, in events. ~736 bytes per parsed event, so the default holds ~74 MB in a long-lived MCP server. Over the ceiling the cache is dropped and reads cost what they always did. |
| `snapshot` | `true` | Off = the on-disk snapshot is neither read nor written (`DDFLOW_SNAPSHOT=0` does the same for one process and wins). A cold command then re-parses the whole log. |
| `snapshot_min_events` | `5000` | No snapshot below this many events: a cold parse is cheap there and a file is not free. |
| `snapshot_stale_days` | `7` | Days a snapshot written by another ddflow version or parser is kept before it is collected. |
| `snapshot_touch_s` | `86400` | How often, in seconds, a snapshot in use is marked as used, so a sibling checkout on other code does not collect it as abandoned. |

**The validity check is a content check, and that is the whole design.** The consumed
prefix is re-hashed on every read — 1.1 ms to read plus 4.3 ms to digest, against the
97 ms of parsing it avoids. The first version used `st_ino` instead, on the reasoning
that "a `git merge` writes a temp file and renames, so the inode changes". That is false:

```console
$ git checkout -q other && stat -c %i .ddflow/events/a1.jsonl
218500670
$ git checkout -q main  && stat -c %i .ddflow/events/a1.jsonl
218500670
```

Git rewrites tracked files **in place**. So switching between two branches that had
diverged left a warm server serving events from the branch you left, silently losing the
ones actually on disk, with the tail read starting mid-line — and because `Store.rebuild`
takes its fingerprint from the real file while taking its events from the cache, that
wrong state was written into the SQLite index *stamped as current*, which a fresh process
would not rebuild away. A content digest makes a rewrite, a truncation, a `git checkout`,
a `git merge`, a delete-and-recreate and a torn tail all one case, so there is no list of
mechanisms to keep current.

A **torn final line** from an append that died mid-write is reported by `ddflow doctor`
and re-read until the writer completes it, never marked consumed. Every guarantee here is
mutation-verified in `tests/test_log_read_cache_*.py` — including that the digest covers the
whole prefix rather than a trailing window of it, which a smaller fixture cannot tell
apart.

### Events that vanished from a shard

Events live in git history only, so a `git push -f` of an older main, a revert of the events
commit, a `git reset --hard` or a deleted shard file takes events out of the tree, and a torn-line
check cannot see an event that is simply gone. ddflow therefore keeps a **shard high-water mark**:
`.ddflow/local/shard-marks`, a machine-local file that is never committed, with one row per
(branch, shard) holding how many events this clone last saw in that shard on that branch, the id
of the last one, and the file's size then. Every command raises the marks to what the shards hold
and none lowers them (only `--accept-lost-events`, below, does), so ddflow's own operations (which only append) never shrink a shard. A mark
is compared only on the branch it was taken on: checking out a branch that never had the events
is not a loss. A detached HEAD takes and compares no mark.

When a shard now holds fewer events than its mark, `ddflow doctor` reports a **PROBLEM** (exit 1)
and the brief opens with a "Lost events" section saying the same:

    <shard> lost 4 event(s) since this clone last saw it (was 120, now 116); the last event it saw
    was <event id>. They may still be in git: `git reflog`, `git log --all -- .ddflow/events/<shard>`,
    `git show <sha>:.ddflow/events/<shard>`. Nothing was rewritten; `ddflow doctor
    --accept-lost-events` records the new count once you have decided

A shard that grew back past its mark but no longer holds the last event this clone saw reads
`<shard> no longer holds the event this clone last saw in it (it held 120, now 125)` instead. The
hints are the way back: the lost events are usually still in the reflog or in another branch's
history, and restoring them is an ordinary git operation. Nothing is rewritten or repaired by
ddflow.

`ddflow doctor --accept-lost-events` is the operator's explicit acknowledgement that the loss is
known and intended (the force-push was deliberate, or the events were restored elsewhere). It
sets the marks of the shards that lost events to their present counts, so the PROBLEM clears, and
adds a note per shard (`<shard>: accepted the new count (116, was 120)`); the log is not touched.
It is a **CLI-only** action: there is no MCP parameter for it, because an agent that met this
PROBLEM must put it to the operator rather than silence it.

**Known limit.** The mark is a count and the last event's id, not the set of ids. A rewrite that
removes an interior event and then brings in enough new ones to reach the old count, with the old
last event still present, all before any ddflow command looks at the shard, cannot be told from
growth. Closing that needs a per-event record and belongs to the P-multiuser sync-check, not to
this mark.

### Managed definitions: the `def.*` event kinds

Doc types, schedules, triggers, skills, agents, research claims and rules are all
*definitions*: named documents that are written, revised, retired, replaced or merged. They
share one record (B-uni-def-records) instead of a set of events per family. Five event
kinds carry them, each with the envelope `{kind, id, digest, source, provenance}`:

- `def.recorded`: a whole definition (`fields`). Recording an id again replaces its fields
  and brings a retired, superseded or merged one back.
- `def.updated`: some fields; a field set to null is removed.
- `def.retired`: with a reason.
- `def.superseded`: replaced by another active definition of the same kind (`successor`).
- `def.merged`: folded into another active definition of the same kind, which lists it as
  merged from.

The fold keeps each definition under `kind:id`, so a schedule and a skill may share a
name, with its digest and its full history. All five kinds are provenance kinds, and
`ddflow replay` renders each one. The one
write path is `ddflow.api` `def_record` / `def_update` / `def_retire` / `def_supersede` /
`def_merge`, with `def_show` and `def_list` to read. It refuses an unknown kind, an id that
cannot be an id, or fields that are not plain JSON (exit 1). It also refuses to revise a
definition that is missing or no longer active (exit 3). An update that changes nothing is
exit 2. A new definition runs the add-time duplicate check when its kind is in
`[dedupe].kinds`: `rule` is by default, and other families add their kind when they start
filing. The CLI and MCP surfaces are B-uni-record-surface.

### A log from a newer ddflow, and the link event kinds

Checkouts of one repository can run different ddflow versions, and a shard merged from a
newer one may hold event kinds this code has no handler for. They are skipped, as an
unknown config key is, but **not silently**: `ddflow doctor` adds a note naming each
unknown kind and how many events were skipped (`this log has events from a newer ddflow
... skipped: record.extended x2`), and `ddflow status` carries them as `skipped_kinds`.
Every number the older ddflow shows is computed without those events, so the remedy is to
**upgrade ddflow-mcp** (and restart the MCP server) to at least the version the log was
last stamped by (next section); the note names it. An event whose `schema` is newer than
this code knows is counted the same way (`phase.added (schema 2) x1`).

**Payload versions and upcasters.** The log is never rewritten, so when an event's `data`
has to change shape the kind's payload version goes up (`"v": 2` in `data`; absent means 1)
and a pure upcaster in `ddflow/core/upcasters.py` turns the old shape into the new one as
the log is read: every handler sees the current shape, and the events every released
ddflow wrote keep folding. A payload version newer than this code knows is counted like an
unknown kind (`phase.added (payload v2) x1`), never guessed at.
`tests/fixtures/event_kinds.json` is the committed snapshot of every kind's fields and
types; a removed kind, a removed field or a changed type fails the suite unless it comes
with a version bump, its upcasters and an entry in the compatibility contract. An added
kind or field only has to be recorded (`python tests/test_event_upcasters.py --write`).

**One malformed event no longer stops the fold.** An event whose handler raises (a
hand-edited shard, a ddflow bug) used to abort every read of the log. It is now recorded
as a fold problem and the fold goes on; `ddflow doctor` adds a note naming each one
(`<event id> <kind> at lamport N by <agent>: <error>`) with the advice to file it; what that
event would have changed is missing from every number (what its handler changed before it
raised stays). A strict fold (the tests' default) still raises.

### The compatibility contract

What ddflow promises not to break is written down in
[docs/ddflow/compatibility.md](../../docs/ddflow/compatibility.md) (decision D-compat): which
surfaces are interfaces (commands, flags, MCP tools and their results, `--json` fields,
config keys, event kinds and payloads, template data, hook commands, on-disk formats); that
every change is additive, deprecating (a hidden alias with a notice, kept until 1.0) or
breaking (a declared migration and a minor bump while 0.x); that releases are numbered by
impact; and that `FORMAT_LEVEL` in `ddflow/__init__.py`, not the version, says when an older
ddflow can no longer read or write a project safely. An older ddflow writes what it
understands, preserves the rest, and is refused only on a direct conflict. The skew guard
below is the current, version-based form of that refusal.

**Aliases and deprecations.** A renamed command, command group, flag, MCP tool, tool argument
or config key keeps working under its old name until 1.0 (D-compat). The new name is declared
once in the command registry (`ddflow/surfaces/registry.py`: `aliases`, `tool_aliases`,
`deprecated_since`, `removed_in` on `Command` and `Param`; `renamed_from` on a knob in
`config_sections/`), and the declaration is refused if it names no release or lets the old
name go before 1.0. An alias is hidden from `--help`, usage and `tools/list` and is always
callable; the first use in a session says so in one line (`ddflow: command 'doc' is deprecated
since 0.1.17; use 'docs' ...` on stderr for the CLI, a `note:` in the MCP result, a warning
for a config key), and a second use says nothing. An old config key is read (the new key wins
when both are set), and the next config write moves it to the new key, keeping comments; an
old `config --set` key writes the new one. An unknown command, MCP tool or argument names the
closest known one (`Did you mean 'ddflow_probe'?`). A command's alias is the same command to
the parity test.

### Upgrading ddflow

One command, `ddflow upgrade`, and one MCP tool, `ddflow_upgrade`, cover everything an operator
asks about an upgrade (decision D-self-upgrade). Each mode below is a flag on the CLI and an
argument over MCP, so "is ddflow up to date?" and "upgrade ddflow" can be put to an agent.

| ask | CLI | MCP argument |
|---|---|---|
| is a newer ddflow out, is this project behind? | `ddflow upgrade --check` | `check` |
| what would change in this project? | `ddflow upgrade --plan` | `plan` |
| change it, after saving the originals | `ddflow upgrade --apply [CATEGORIES]` | `apply` |
| upgrade ddflow itself | `ddflow upgrade --self [--dry-run] [--rollback]` | `self` |
| where does it stand? | `ddflow upgrade --status` | `status` |
| what did past upgrades do? | `ddflow upgrade --history` | `history` |
| finish an interrupted apply | `ddflow upgrade --resume` | `resume` |
| undo an apply | `ddflow upgrade --restore [NAME]` | `restore` |

**Check and proposal.** The release check (at most once per `[upgrade].check_interval_h`, never
a daemon) PROPOSES a newer release in the brief, `status` and `doctor`; it installs nothing. See
"Is a newer ddflow out?" below. **Self-upgrade** installs the newer ddflow with the tool that
installed this one and needs the operator's consent (`--yes --reason`; `[upgrade].self`); the
version it replaced is remembered so `--self --rollback` reinstalls it. **Containers and MCP
servers** compare their version with the project's stamp on start and run the safe migrations
(`[upgrade].on_start`); they serve even while a migration waits. **Backups** are `local` (a copy
under `.ddflow/backups/`) or `snapshot` (a git tag); `--restore` puts either back.

**Monitor.** `--status` prints the running version, the newest release and when it was last
asked, this project's stamp, the plan with each step's state (`pending`, `running`, `done`,
`failed`, `unavailable`, `needs operator confirmation`, `waived`), the backups, and the agents and MCP servers
last seen on an older ddflow, which must restart (from the `ddflow.seen` stamps and
`skew.overridden` events). It exits 0 when nothing is pending and 1 otherwise. `--history` lists
the upgrades the log records. Both only read.

**Orchestrate and resume.** Every step of an `--apply` is an `upgrade.applied` event (a `run` id
and `status`: `started`, one `step` per item, then the closing `done` or `failed`; no new event
kind; an older ddflow reading the log shows these progress records as empty rows in its history), so replay explains the upgrade and an interruption (a crash, a closed terminal, a
timeout) leaves a run `--status` calls `interrupted`. `ddflow upgrade --resume` finishes it:
the steps the run settled are not run again, the rest are, what the operator confirmed before the
interruption still counts, and a second `--resume` finds nothing (exit 2). A run that recorded a
step in the last 3 minutes may still be applying, so `--resume` refuses it (exit 3) until it has
been quiet that long. Over MCP an apply reports each finished step as a progress notification when the
client sent a `progressToken`.

**Rehearsing it first.** `scripts/ci/upgrade-dry-run --installed-version X` (or `--from-commit SHA`,
`--previous-release`) upgrades a throwaway clone of the project from the version you run now to
this tree and prints a PASS/FAIL table; nothing touches the project, its `.ddflow`, the installed
ddflow or a running MCP server (the clone gets a private HOME and no network, and the project's
log, snapshot tags and backups are compared before and after). It reads the clone with the new code, shows the plan (the items
you would be asked to confirm fail the row), applies it with a snapshot, applies again (a no-op),
restores (the files return, every shard keeps its bytes), lets the OLD ddflow read the upgraded
clone (its write is refused with the remedy or lands without disturbing a byte), connects the new
MCP server to the old project (it proposes the upgrade, applies nothing until consent, then applies),
runs two agents through a task, merges shards two old-version agents wrote and folds the old log
through one registered event upcaster (the machinery has never met a real payload bump). The old ddflow is
`--old-cmd`, `--build-old` (installed from the clone with uv) or `--simulate-old VERSION`; without
one those rows say `unavailable`. Exit 0 all passed, 1 a check failed, 2 it could not run;
`--max-show N` sets how many items the read pass renders with `show` (25; 0 is every item);
`--report FILE` and `--preview FILE` write the Markdown report and the plan. `scripts/release.sh`
and the publish workflow run it from the previous release and refuse a release whose upgrade is red;
they pass `--confirmations-ok`, which names the items the operator would be asked to confirm in the
preview without failing the row, because those come from this project's own config, not from the
release (run without it, as an operator does, they fail the row).

**Turning each off.** `ddflow config upgrade.release_check off` (or `DDFLOW_NO_UPDATE_CHECK=1`)
stops the release check, `upgrade.on_start off` the check at server or container start,
`upgrade.auto off` the notice after an upgrade, `upgrade.self operator` keeps an agent from
installing, and `upgrade.backup none` skips the originals. `ddflow help upgrade` is the same
for an agent.

### Upgrading a project: the whole picture

A project is brought up to a newer ddflow in three steps, and ddflow does only the first on its own.

1. **Silent: it tells you.** After ddflow is upgraded, the brief and the MCP handshake
   print one line, `Upgraded ddflow A -> B: run ddflow upgrade --plan`: the handshake once per
   version on this machine for this project, every brief until the project is upgraded. With the shipped `[upgrade].auto = "check"` the only file that
   writes is the git-ignored `.ddflow/local/upgrade-notice.json`. `"safe"` also refreshes
   ddflow's own files (hooks, driver docs, rules blocks) after a backup; `"off"` says nothing.
2. **Read-only: you look.** `ddflow upgrade --plan` lists what would change, by category, and
   writes nothing.
3. **Consented: you apply.** `ddflow upgrade --apply` does what the plan lists after saving what
   it rewrites. Anything an operator set by hand (a config value, a hand-edited driver doc) is
   never changed without `--confirm KEY --reason WHY`, which is recorded in the log.

**On connect and on container start.** A starting `ddflow mcp` server (and the container that runs
it) does not wait for the brief: it compares its version with the project's stamp and, when it is
newer, acts per `[upgrade].on_start` (`safe` default, `check`, `off`; the environment variable
`DDFLOW_UPGRADE_ON_START` overrides it for one run, e.g. `docker run -e
DDFLOW_UPGRADE_ON_START=check`; an unknown value counts as `check`). `safe` rebuilds the derived
stores (the sqlite index) and, **in a container only** (running the newer image is the operator's
choice), applies the hooks and instructions categories after a backup. On an MCP connect outside a
container nothing else is applied without the operator: the handshake instructions and the first
brief carry the PLAN as a question (`ddflow ... newer than this project ... pending ... Ask the
operator`), and only on their yes does the agent call `ddflow_upgrade` with `apply`. Config values
anyone set and every item marked for the operator still wait for `confirm` and `reason`. The start
never refuses to serve: errors become a line in the report, work past `[upgrade].start_timeout_s`
(10 s) is left running and the handshake says so in a NOTE (`... an upgrade is PENDING but its plan
is not known yet ... Ask the operator ...`, never silence and never "current"); the plan it
reaches later is kept in the marker, so the next connect proposes at once, and every brief repeats
the note until the project is upgraded (`ddflow doctor` lists it as a NOTE too, with the same remedy, and
nothing when the project is not behind). A
read-only or foreign-owned project is reported and not written, and a
runtime OLDER than the project writes nothing and says `restart` (the skew guard). The outcome is
kept in the git-ignored `.ddflow/local/upgrade-start.json`; it replaces the one-line notice for
that version. A server that was already running when ddflow was upgraded says `restart the
server` in its replies.

**Is a newer ddflow out? The release check.** At most once per `[upgrade].check_interval_h`
(default 24), when a brief or `ddflow doctor` runs, ddflow asks the
package index it was installed from whether a newer release exists, and PROPOSES it: one line
(`ddflow 0.1.10 is available (you run 0.1.9): upgrade ddflow-mcp ... then restart the MCP
server`) in the brief and the MCP handshake once per version on this machine, and in `status`
and `doctor` whenever one is known. It installs nothing. `ddflow upgrade --check` (MCP
`ddflow_upgrade` with `check`) asks at once, whatever the interval, and prints the running version, the newest
release, this project's stamp and the command that upgrades THIS install; exit 0 when this is the
newest, 1 when a newer release exists, 2 when the check is off or the index is unreachable.

What leaves the machine: one `GET <index_url>/ddflow-mcp/json` (the public package index
`https://pypi.org/pypi/ddflow-mcp/json` by default; `[upgrade].index_url` points a private mirror
that serves the same document) with a 3 s timeout. It carries nothing about the project or the
machine, and the answer is cached in the git-ignored `.ddflow/local/release-check.json`. Offline,
a timeout or a bad reply is silent, and counts as a check: at most one request per interval. Pre-releases
are ignored unless `[upgrade].prereleases`. A ddflow running from a source checkout is never
proposed an index release by the periodic check. Turn it off with `ddflow config
upgrade.release_check off` or, for one shell or container, `DDFLOW_NO_UPDATE_CHECK=1`: then no
request is made at all. In a container the image registry tag is not consulted; the package index
answers for every install.

**Upgrading ddflow itself.** `ddflow upgrade --self [--to VERSION] [--dry-run]` (MCP
`ddflow_upgrade` with `self` = `run`, `dry-run` or `rollback`, and `to`) installs the newer ddflow
with the tool that installed this one: `uv tool upgrade ddflow-mcp` for a uv tool, `pipx upgrade
ddflow-mcp` for pipx, that interpreter's own `pip install -U` for a virtualenv (`--to 0.1.10` pins
a version with `uv tool install --force` / `pipx install --force` / `pip install ddflow-mcp==...`).
Where ddflow cannot do it safely it does NOT try: an ephemeral `uvx` environment, a container image,
a source checkout, an install this user cannot write, a system pip or a VCS install each print the
exact command for the operator (pull the image and restart; `git pull`; reinstall) and exit 2.
`--dry-run` names the command and runs nothing.

Installing new code is the operator's decision (`[upgrade].self`): `ask` (default) runs the
installer only with `--yes --reason WHY` (MCP: `confirm: ["self"]` and `reason`), which is recorded
in the `upgrade.applied` event (category `self`, from and to); without it the call is refused
(exit 3) and shows the command it would run. `operator` refuses an agent over MCP altogether;
`auto` asks for no consent but still needs `--self`. The installer runs as a subprocess with a
10-minute bound and its output captured; a failed or timed-out installer leaves the old version
installed and working and says so (exit 1). The running process never replaces its own code
mid-command: it finishes, says `RESTART needed` (an MCP server also says it in its replies), and
the NEW code, in a fresh process, shows `ddflow upgrade --plan` (and under `[upgrade].auto = safe`
applies hooks and instructions after a backup, which re-points a hook or MCP entry that pinned the
old path). The version it replaced is remembered in the user's state directory
(`$XDG_STATE_HOME/ddflow/self-upgrade.json`, default `~/.local/state/ddflow`) because it belongs to
the install, not to a project; `ddflow upgrade --self --rollback` reinstalls it from any directory,
with the same consent.

**Undo and downgrade.** The log is append-only, so an upgrade is never undone by deleting events.
`ddflow upgrade --restore [NAME]` puts back the files a backup holds (the default `local` backup,
or the opt-in git `snapshot` tag: see "Backups: local or snapshot" above), and `git revert` of
the commit that holds the upgrade's changes does the same for committed files. Going back to an
older ddflow *binary* is a different matter: once a newer ddflow has stamped the log, the older
one reads it but its writes are refused with the version to upgrade to (the skew guard,
below), unless the operator passes `--allow-older-version` for that session.

**Two versions at once.** Teammates on different ddflow versions share one log. Each agent
appends to its own shard under `.ddflow/events/`, the shards are merged by union
(`merge=union` in `.gitattributes`), and the version stamp is an event like any other, so the
highest stamp wins on every clone: after the first upgraded clone's shard arrives, an older
ddflow can still read and `ddflow upgrade --plan`, but its writes are refused until it is
upgraded. Two clones that each ran `--apply` before merging both appended an `upgrade.applied`
event; the union keeps both, every step was idempotent, and the plan on the merged log is empty.

### The version stamp and the skew guard

A project's log records which ddflow versions have worked on it, so an upgrade, or a
checkout running an older ddflow than its teammates, is a fact instead of a guess
(decisions D-upgrade-event-kinds and D-upgrade-skew-guard). Five event kinds, all skipped
with a note by a ddflow that predates them:

| kind | written | carries |
|---|---|---|
| `ddflow.seen` | once per (agent, version, format level), on that agent's first write after either changes | `version`, install kind (`installed` or `source-tree`), `format_level` (the `FORMAT_LEVEL` the writer wrote at) |
| `ddflow.capabilities` | on the first write that uses a capability (below) | `capability`, the `kinds` it governs, the release (`version`) that provides it |
| `skew.overridden` | when an agent insists on an older ddflow writing (below) | running version, the log's version (and, for a format skew, its format level), session, the reason |
| `upgrade.applied` | when `ddflow upgrade --apply` applied or acknowledged something | from, to, categories, backup, items, confirmed, config_changes, summary |
| `repair.applied` | when a versioned data repair is applied (below) | repair id, since, version, the settled findings' keys and details |

The fold keeps the **highest** version stamped (`State.ddflow_versions`; `ddflow status
--json` shows it in its ddflow_version field, and `ddflow doctor` names it when there is
a skew); an older ddflow stamping later does not lower it.
A machine-local, git-ignored marker `.ddflow/local/seen.json` holds the last version *this
machine* acted under. It works the same on a fresh `ddflow init` and on a project that
predates the stamp: an old log with no stamp reads clean and is stamped on its next write.

**The skew guard.** A write by a ddflow *older* than the log's highest stamp is refused
(exit 3) before anything is written:

```text
REFUSED: this project's log has been worked on by ddflow 0.2.0 (stamped by alice), and
this ddflow is 0.1.9, which is older: writing now could drop or misread what the newer
one recorded. Upgrade ddflow-mcp to >= 0.2.0 (`uv tool upgrade ddflow-mcp`, ...), then
restart the MCP server and retry ...
```

The remedy fits how this ddflow is installed (`config_sections._compat.upgrade_advice`): an
installed ddflow is told to upgrade its package, a source checkout to merge main into it
(`Merge main into this checkout ..., then restart the MCP server (ddflow >= 0.2.0 is needed)
and retry`). The rest of the message does not change.

Reads are always allowed. The agent upgrades or asks the user; only if the user insists
does it rerun with an explicit override: `--allow-older-version --reason "<why>"` on any
CLI command, or the `allow_older_version` argument (the reason) on any MCP tool. That
writes a `skew.overridden` event and **marks every event of that session** with the older
version (`older_ddflow` in its data): `ddflow history` shows `[older ddflow 0.1.9]`,
`ddflow replay` says so, and `ddflow doctor` lists the overrides and how many events they
cover, to be reviewed after upgrading. The override is per session, not per command: a new
session, another agent, or a newer stamp is refused again. Only versions that ship the
guard can refuse; releases before it cannot.

A skew of **patch releases only** (the same minor, the same data format level, and no
recorded capability this ddflow lacks) is not refused, even under `[upgrade].skew = refuse`:
the write goes through with a one-time warning and the event is marked as written by an older
version (`older_ddflow`, as for an override). A higher minor, a higher format level or a
capability gap is still refused. An older `ddflow adopt` is refused even for a patch, since it
would rewrite the newer managed files, and a project instruction file that holds two managed
blocks (an older adopt's leftover) is a PROBLEM in `ddflow doctor`;
`ddflow adopt --refresh-docs` collapses them to one.

**The format level.** A version number alone misses a branch or source tree that changes an
on-disk format without a bump, and every patch release looks newer than the last. So each
`ddflow.seen` stamp also carries the `FORMAT_LEVEL` its writer wrote at
(`docs/ddflow/compatibility.md`), and the guard refuses a write when the log's highest
stamped level is **above** the running one, even when the version is not older. The message
then names both levels (`... at data format level 3, and this ddflow (0.2.0) writes level 2`),
and `--allow-older-version` overrides it for the session the same way, against that level:
a later, higher level is refused again. A stamp from before the field says nothing about the
level and never refuses. The same `[upgrade].skew` policy governs both.

**Capabilities.** A version number and a format level are coarse: some data needs one
specific ability of the writer. The first write that uses such a **capability** records a
`ddflow.capabilities` event naming it, the event kinds it governs and the release that
provides it, like git's repository format extensions. A writer that lacks a recorded
capability (it does not know the name, or does not govern that kind) is refused **just the
writes it governs** (exit 3), with the version to upgrade to; every other write proceeds.
Two exist: `id-template` (ids minted under a non-default `[ids]` template; governs the kinds
that mint ids: `bug.found`, `lesson.recorded`, `research.recorded`, `decision.recorded`,
`memory.recorded`, `job.started`, `session.started`, `task.added`, `phase.added`) and
`log-redaction-full` (free text written through the full `log` redaction profile; governs
every kind with free-text fields). Because every free-text write uses the second, a log's
first such write is preceded by its record. `[upgrade].skew = "warn"` or `"off"` skips the
refusal; there is no per-session override, since the gap is in the writer's abilities, not
its age. The fold keeps them in `State.capabilities`.

`[upgrade].skew` is the policy: `refuse` (default), `warn` (write, say so on stderr) or
`off`. Set it with `ddflow config` or `ddflow_configure`.

### Versioned data repairs

Some bugs leave damage in the log that outlives the fix: events an older ddflow wrote
before the fix still carry it. A **data repair** finds that damage in a log and mends it by
appending corrective events. It never edits or deletes a line, so history is never
rewritten (decision D-upgrade-model, `ddflow/services/repairs/`). Each repair has an id,
`since` (the first release that no longer causes the damage), a detector, the corrective
step and a test fixture: a crafted old log carrying that damage. The test checks that the
detector finds it, the repair mends it, a second run finds nothing, and every original line
is byte-identical. Applying a repair appends `repair.applied` (the repair id, `since`, the
version applying it, the keys and descriptions of the findings it settled), so the same
findings are never offered again. `ddflow history --kind repair` shows it.

| repair | finds | applying it |
|---|---|---|
| `orphan-prompts` | prompts and notes recorded with no session id | appends a copy under the nearest session, as `ddflow session adopt-orphans` does |
| `forced-completions` | items completed with `--force` over unmet conditions, still done | reports and annotates them (the record names each item and what was overridden); the completion stands and `ddflow verify <id>` re-checks it |
| `unreadable-lines` | shard lines that are not events (a torn append, non-object JSON) | quarantine note: records each line's shard, number and digest |
| `mismatched-ids` | events whose content does not match their id | quarantine note: records each event id |
| `unknown-author-shards` | shards by an agent id with no committed history on the default branch | records that the operator reviewed the author. Runs only when named: it is the operator's decision |
| `old-export-renders` | selected whole-file generated documents rendered by an older ddflow that regenerating would change | regenerates them, as `ddflow export <doc> --update` does. A hand-edited file is never touched |

`ddflow doctor` reports what is pending. A torn line or an edited event is a PROBLEM until
a repair has quarantined it, and a NOTE after that. A reviewed unknown author is no longer
named. Doctor only reports: it never applies a repair. A bug fix task tagged
`data-damage` must ship a repair that names the bug, or a `FOLD_ONLY` entry saying why
reading the log is already enough. A test on this project's own log enforces this.

### The upgrade manifest: what changed since your version

Every release ships `ddflow/templates/upgrade/changes.toml` in the wheel: a machine-readable
list, per release, of what a project upgrading from an older version will meet
(B-upgrade.2-changes). Each entry is one change:

| kind | carries |
|---|---|
| `knob_added`, `knob_changed`, `knob_removed` | the `section.knob`, its old and new default, a one-line `why`, and for a changed default the `effect` a project that never set it will see |
| `event_kind_added`, `event_kind_removed` | the event kind |
| `refresh`, `repair`, `feature` | an instruction, hook or template refresh; a data repair by id; an opt-in feature with the command that `enable`s it |

The file starts from a base (every knob default and event kind of 0.1.3) and lists every
release since. Replaying it gives exactly the knobs and event kinds of the ddflow that ships
it, and a test holds that: a branch that adds a knob, flips a default or adds an event kind
without an entry fails until it has one. Run `uv run python scripts/upgrade_manifest.py
backfill` (it snapshots every release commit and the working tree), commit
`ddflow/templates/upgrade/`, and write the `why` (and `effect`) it could not know. A change
not yet released is a fragment file of its own under `templates/upgrade/unreleased/`, so two
branches adding knobs never edit the same file; cutting a version folds the fragments into
its release. The history it backfilled from 0.1.3 to 0.2.0: 63 new knobs, 7 changed
defaults (among them `dedupe.on_match`, `warn` in 0.1.9 and `ask` again in 0.1.10).

**The release lint** (decision D-upgrade-manifest-lint). `ddflow version lint` compares the
code's knob defaults and event kinds with that replay; `ddflow version cut`,
`scripts/release.sh` and the publish workflow run it. With `[release].manifest_lint = "block"`
(the default) a change with no entry stops the release, exit 3, and the message is never a
bare failure: it lists each unmanifested change (`knob_added:section.knob`, ...) and the
operator's options. (1) Have an agent write the entries and whatever upgrade repair or note
a safe upgrade needs: the message pre-fills one fragment per change from the diff, then
re-run the lint. (2) Waive a named change for this release with `ddflow version lint
--waive <change> --reason "..."`, recorded in `templates/upgrade/waivers.toml` and shown in
the next upgrade plan. (3) Change the policy: `ddflow config --set release.manifest_lint warn|off`.
`warn` prints the same and carries on; `off` is silent. An agent may prepare entries and
propose; waiving or lowering the policy is the operator's decision. Over MCP,
`ddflow_version_cut` (also with `dry_run`) runs the lint and `ddflow_configure` sets the
policy. In a project that does not ship ddflow's own manifest the
lint does nothing.

In code, `ddflow.services.upgrade_manifest.changes_since("0.1.9")` returns every change in
a newer release, oldest first; `replay()` gives the knobs and event kinds a release has.
The upgrade plan reads it to tell a project what its upgrade will change.

### Migrations: breaking changes P-unify makes to what a project holds

A release that changes a file format, a marker grammar or a reference registers a
**migration** (`ddflow/services/migrations/`, decision D-compat) instead of rewriting
things ad hoc. A migration declares `since_version`, `format_level` (the `FORMAT_LEVEL` its
result belongs to), the artifact kinds it touches (`log`, `config`, `rules`, `docs`,
`hooks`, `references`, `local`) and four functions: `detect` (pure; nothing once migrated),
`plan` (the dry run: each file it would rewrite), `apply` (files only through `infra.fsio`;
any log change is a corrective event, never an edit of a line) and `verify`. The runner backs
up the planned files first, applies, appends the events, then re-detects and verifies: a
migration whose result does not check out is `failed`, never `applied`, and one that could
not run is `unavailable`. `ddflow upgrade --plan` lists the pending ones under *migrations*,
each with `would: <file>: <change>` lines; `ddflow upgrade --apply migrations` runs them. An
operator-consent migration waits for `--confirm <migration id> --reason WHY`. A second run
finds nothing and writes nothing.

Registered now: `stale-references` (kind `references`) rewrites the deprecated command and
tool names (an old name kept as an alias after a rename) that sit inside a region ddflow wrote and nobody
edited, in settings hooks, git hooks, driver docs and instruction blocks; the project's own
text and a hand-edited region are left: `ddflow doctor` proposes the change, and `ddflow upgrade --plan`
lists each such file as a note under *instructions* (one line per name, with its replacement; the old
names keep working until 1.0) that `--apply` leaves alone. The surface
that runs the upgrade hands it the command and tool tables (`api.refs.provide_upgrade_vocabulary`): the
CLI registers its parser and the tool table when it is imported, the MCP server the tool table when it first
reaches the api, so `ddflow upgrade` and `ddflow_upgrade` both find the names.

`rules-to-events` (kinds `rules`, `log`) imports the project's rule files once: every
`.ddflow/rules/<id>.toml` with no `rule` definition in the log becomes a `def.recorded` whose
provenance says `via = import` and whose `source` is the file, and a file whose content
differs by digest from its active record (a hand edit) becomes a `def.updated`. Timestamps
are not content, a retired rule is left alone, a file that does not load is not imported (see
`ddflow doctor`), and the files are not touched: it only appends events, so a second run finds
nothing. Rules are recorded definitions from here on (decision D-unify 7).

`rule add`, `rule update` and `rule remove` (CLI and MCP) write the file as before and then record what
it says: a `def.recorded` (`via = write`, `source` the file) for a new or re-added rule, a `def.updated`
when the content changed (a re-save that only moves `updated` is no change), a `def.retired` for a
removed one; an `extends` answer records the longer text. The add-time duplicate check stays the
rule's own (against rules and every other kind), so the record is written without a second one. If the
log cannot take the record (a lock timeout), the call FAILS (exit 1, on every surface) and says the
rule file is written and `ddflow upgrade` records it; a removal whose retirement failed says the file is
removed and the log still holds the rule.

The rule files are a checked generated view of those definitions: a file agrees with the log when the digest of
its fields is the one on its active record (timestamps are not content). `ddflow rule sync` (MCP
`ddflow_rule_sync`) settles every disagreement: a file with no record is recorded (`def.recorded`,
`via = sync`), a hand-edited file is recorded as a `def.updated`, and a live rule whose file is gone has its
file written from the log; a retired rule and a file that does not load are left alone, and a second run finds
nothing. `ddflow doctor` notes the rule files that disagree with the log; the pre-commit check
(`[enforce].generated_views`, with the other generated views) refuses a staged rule file the log does not say,
naming `ddflow rule sync`; and `ddflow replay --out DIR` writes `rules/<id>.toml` rebuilt from the log alone.

### Applying an upgrade

`ddflow upgrade` (or `--plan`) only reads. `ddflow upgrade --apply` does what the plan lists
(MCP: `ddflow_upgrade` with `apply`; `plan: false` applies everything), by category:
`--apply` or `--apply all`, or a comma list of `repairs`, `migrations`, `config`, `instructions`,
`hooks`, `mcp`, `features`. It prints the plan that is left, and exits 0 when everything chosen was
applied or acknowledged, 1 when a step failed, 2 when one could not run (never read as
done), 3 while an item waits for the operator. Before it rewrites any file it copies the
originals to `.ddflow/backups/<stamp>-<from>-to-<to>/` (local, git-ignored, never shared;
`--backup none` or `[upgrade].backup = "none"` skips it, `--backup snapshot` (or `--snapshot`) keeps it in git, `[upgrade].backup_keep` (default 10) is how many are
kept) with a `manifest.json` of what was
there, and prints the backup and `git diff` to review the change.

**The upgrade notice** (decision D-upgrade-auto-check; `[upgrade].auto`, shipped `check`).
When ddflow has been upgraded past the release the project was last brought up to and
`ddflow upgrade --plan` has items, the brief (so the SessionStart hook) and the MCP handshake say so in ONE line --
`Upgraded ddflow 0.1.9 -> 0.1.10: run ddflow upgrade --plan` -- the handshake once per version on this machine
for this project, every brief until the project is upgraded (`.ddflow/local/upgrade-notice.json`, git-ignored: the only thing it writes),
whichever surface speaks first. `check` changes nothing else. `safe` first applies the plan's non-destructive categories, `hooks` and `instructions`
(ddflow's own files), after the backup `[upgrade].backup` names, and says what it did; config
defaults, migrations, repairs and features stay the operator's. `off` says nothing. Set it with
`ddflow config upgrade.auto safe` (or the `ddflow_configure` tool).

**Backups: local or snapshot** (decision D-upgrade-backups; `[upgrade].backup`, or `--backup`
for one run). Both are explained here so you can choose:

| | `local` (default) | `snapshot` (`--backup snapshot`) |
|---|---|---|
| what it is | the files copied under `.ddflow/backups/<stamp>-<from>-to-<to>/` with a `manifest.json` | a git tag `ddflow-upgrade-snapshot/<stamp>-<from>-to-<to>` on HEAD, after committing affected files git did not track yet ("ddflow: snapshot before upgrade") |
| where it lives | this machine only, git-ignored; the newest `[upgrade].backup_keep` (10) are kept | in the repository: shared when you push it, durable |
| cost | disk; nothing in your history | a tag and, for untracked files, one commit; needs git, and a clean working tree (the snapshot is what HEAD holds) |
| stops when | the backup cannot be written (a failure, exit 1) | there is no git, no commit, or uncommitted changes to tracked files: exit 3 and nothing is changed, with the reason |
| undo | copy the files back from `<backup>/files/in/` (`manifest.json` lists them) | `git checkout <tag> -- <file>` (the upgrade's changes stay uncommitted until you commit them; once committed, `git revert` that commit) |

`--snapshot` is `--backup snapshot` for one run (with `--apply`; MCP: `snapshot: true`); naming
another `--backup` mode beside it is refused (exit 3). **Undo:** `ddflow upgrade --restore [NAME]`
(MCP: `restore`) puts back the named local backup or snapshot, or the newest when NAME is
omitted, and saves what it replaces first; it stands alone, so combining it with any other
option except the global `--json` (`--plan`, `--apply`, `--confirm`, `--reason`, `--backup`, `--snapshot`)
is refused (exit 3). `--json` carries a `restored` object.

A snapshot cannot hold a file git ignores or one outside the project (the git hooks): those
still get a local copy, named in the output. `--backup none` (or `[upgrade].backup = "none"`)
skips saving. What each category does:

- **repairs**: the pending data repairs (new corrective events, never an edit of the log);
  one only the operator may decide waits for `--confirm <repair id>`.
- **migrations**: the pending registered migrations (see above), the files they rewrite saved
  first with the rest; each is recorded in `upgrade.applied` as `migration:<id>`.
- **config** (`[upgrade].config_changes`: `agent` (default), `ask` or `operator`; the last two
  make every change wait for `--confirm`): a changed default for a knob the project never set takes effect by itself, so
  applying it only *acknowledges* it, lists it with `ddflow config --set KNOB OLD` to pin
  the old behaviour, and records it. A value anyone set (in a config layer, via `ddflow
  config`) is never changed without the operator: `--apply` refuses it (exit 3, naming the
  flag) unless `--confirm KNOB --reason "..."` is given, then sets the new default, or
  removes a knob the schema dropped; a value set by the environment is left to its owner.
- **instructions**: driver docs, rules blocks and native rules are refreshed as `ddflow adopt
  --refresh-docs` does; a hand edit inside a stamped region is kept in `<file>.local-edits`,
  a copy from before the stamp that may have been edited needs `--confirm <path>`, and a
  file a newer ddflow wrote is left alone.
- **hooks** and **mcp**: the missing or dangling hook, and the MCP entry that launches a
  ddflow that is gone, are reinstalled.
- **features** are acknowledged: they are opt-in and the plan prints the command that
  enables each.

Applying is idempotent: a second `--apply` finds nothing, writes no backup and no event. One
`upgrade.applied` event records what was applied, who confirmed what and why, the changed
defaults and the backup, so `ddflow replay` and `ddflow history` explain the upgrade. The
project's version moves up to the running ddflow only when every `config` and `features`
item was applied, acknowledged or confirmed; a partial apply, or one with a refused item,
leaves them in the plan. An apply that is cut short leaves the backup and a plan that can
be run again, because every step is idempotent.

Two such kinds describe how records relate (decision D-no-duplicates). Add events
(`task.added`, `phase.added`, `bug.found`, `lesson.recorded`, `research.recorded`,
`decision.recorded`, `memory.recorded`) may carry `extends`, `duplicate_of`, `related`
and `dedupe` (the recorded answer, its score and the candidates shown). A later
`record.extended` is a **verbatim addition** to an existing record (text, who, when,
score), and a later `link.recorded` is a link or a `distinct` dismissal. Both accumulate,
keyed by event: two additions made at once by two clones both survive in any fold order,
and an addition never replaces the record's own text (a bug's summary stays as written).
`ddflow replay` renders both. The add paths write `record.extended` and the `related`
back-link (see "The check every add runs"); `ddflow link` writes a bare `link.recorded`
for `extends`, `duplicate_of`, `related` and `distinct` (and, for two lessons, the
supersession that merges them).

### The compaction that was declined

An event kind `log.compacted` was reserved for a retention pass that shrank the log. It
has been **removed**, because the recipe it was reserved for cannot be implemented
without breaking two shipped commands. Three probes:

1. **A compaction survives a `merge=union` merge.** One branch compacts, the other
   appends; the deletions stick. So union is not the obstacle.
2. **Two divergent compactions merge to neither side's result**, and out of Lamport
   order — the case union cannot resolve.
3. **The decisive one.** `ddflow progress` and `ddflow loops` read **raw events**, not
   folded state: `progress.work` pairs each `lease.acquired` with the next release across
   the whole history. Leases and gate outcomes are not `PROVENANCE_KINDS`, so keeping
   "the last state-bearing event per subject" leaves a `lease.released` with no acquire
   to pair with. A queue whose loop detector fires `repeat_claims` before compaction
   reports **nothing** after it, and six attempts become zero — and under
   `[loops] on_detect = "block"` that is a behaviour change, not just a lost report.

Growth is addressed by making the read cheap rather than the log short, which keeps it
append-only and auditable. Beyond ~100k events the right answer is an on-disk state
snapshot, not a shorter history.

---
