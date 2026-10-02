# Exporting documents

The event log already knows the roadmap, the open bugs, the decisions in force and what
was done this week. `ddflow export` turns that into ordinary markdown files, so a person
who never runs ddflow can read them in a pull request. ddflow owns the format; you do not
write the document, you ask for it.

## The kinds

`ddflow export` lists every kind with its default target and its state: `not selected` (the
default for all of them), or for the selected ones `fresh` (the file is exactly what a write would produce), `stale`, `hand-edited`
(its body no longer matches the digest in its header, or it is not a ddflow file at all:
ddflow will not overwrite it) or `missing`.

    kind       default target  filters                    holds
    roadmap    ROADMAP.md      phase limit                Now / Next / Later with progress
    bugs       BUGS.md         since limit status phase   open, fixed, invalid (--item = --phase)
    status     STATUS.md       (none)                     counts, agent-hours, in flight, open bugs
    worklog    LOG.md          since limit                one line per item per day
    sessions   SESSION.md      since limit session        summary, prompts, notes, items touched
    decisions  DECISIONS.md    since limit status         an index of decisions
    rules      RULES.md        limit tag                  decisions in force, lessons, enforced workflow
    changelog  CHANGELOG.md    tag (--version)            Keep a Changelog, from items, bugs and version tags

Replay is not a kind: it carries paths and addresses. Imported rulebooks are not exported.
The changelog files each finished item by (1) the `--changelog "Added: ..."` line given to
`ddflow complete` or `ddflow bug fixed` (`skip` leaves it out), (2) the Conventional Commit
type of its landing commit, (3) its tags (breaking, feature, bug, hotfix), (4) a
`(fixes bug X)` title suffix, else Changed; the version is the oldest tag whose history holds
the item, else Unreleased; nothing before the first tag is reconstructed. It reads git as
well as the log, read-only; unreadable git is exit 2, never an empty changelog.
`ddflow version cut --changelog` writes the new version's section into the file with the cut.

## Review on demand

    ddflow export roadmap                     the document on stdout, capped
    ddflow export bugs --status open --limit 20
    ddflow export worklog --since 2026-09-01
    ddflow export status --max-bytes 4000     a cut document ends in [truncated: N more]
    ddflow export bugs --item B-export-core   bugs: the same as --phase
    ddflow export roadmap --diff              what writing it would change
    ddflow export roadmap --template my.j2    render once with your template; writes nothing
    ddflow export changelog --version 1.2.0   one release's notes (X or vX, or unreleased)

Any kind can be printed whether or not it is selected, and nothing is written. Filters a
kind does not take are refused (exit 3) rather than ignored; `ddflow export <kind> --help`
shows the flags.

## Writing

    ddflow export roadmap --update     write the configured target (asks on a terminal)
    ddflow export roadmap --out docs/ROADMAP.md
    ddflow export --all --check        exit 1 when any selected document is stale
    ddflow export --all --update

A printed document is capped at `[export].max_bytes` (60000; `--max-bytes N` overrides it)
and a cut one ends in a `[truncated: N more; ...]` footer; a file that is written is never
capped. `ddflow export <kind> --check` exits 1 when that one document is stale.

`--all` acts on the SELECTED documents, `[export].documents`, which is empty by default:
with nothing selected it does nothing and says so (exit 2). Three update modes, per
`[export.<doc>].mode`: `whole` (the file is generated; a header carries the kind, the
ddflow version and a digest of the body), `region` (only the text between
`<!-- ddflow:begin doc=<kind> ... -->` and `<!-- ddflow:end doc=<kind> -->` in a
hand-written file) and `append` (a kind that can grow a log, today the changelog; entries after the last
exported event). A hand-edited generated file, an unmarked file or an edited region is never
overwritten without `--force` (whole-file hand edits are found by the body digest in the header, so never edit
a generated file: change the template, the log, or `[export.<doc>].mode = "region"`). Paths
outside the repo, symlinks, `.git` and `.ddflow` are refused. Every selected target is a shared path for leases: regenerate it without a claim.

## Choosing documents

    ddflow export enable roadmap                  select it; no file is created
    ddflow export enable status --path docs/S.md --mode region
    ddflow export enable bugs --local             this machine only
    ddflow export disable roadmap                 deselect (the file stays; printing still works)
    ddflow export disable roadmap --lock          the operator's veto
    ddflow export ack                             the operator has seen what agents enabled

An agent may enable a document without approval, but never silently: the result names the
agent and the stop command (`ddflow export disable <doc>`), `ddflow export` lists who
enabled each document and when, `brief` shows one `Export:` line and `doctor` a note until
`ddflow export ack` (or `ddflow export` at a terminal). After `disable --lock` an agent's
enable exits 3 (locked by the operator); locking, acknowledging and unlocking
(`ddflow export enable <doc>` at a terminal) are refused under `--agent`, `DDFLOW_AGENT` or a
harness's shell. The events are `export.enabled`, `export.disabled`, `export.acknowledged`.

## Templates

    ddflow export eject roadmap          copy the shipped template to .ddflow/templates/export/
    ddflow export eject roadmap --force  replace an edited copy with the shipped default
    ddflow export validate [roadmap]     render the selected documents; errors with file and line

Resolution: `[export.<doc>].template`, then `.ddflow/templates/export/<kind>.md.j2`, then the
shipped default. `eject` is idempotent and never overwrites an edited copy without `--force`;
its first line, `{# ddflow-shipped: <digest> -#}`, renders to nothing and records the shipped
text the copy came from, so `validate` and `doctor` say when the default has moved on and a
plain `eject` refreshes an unedited older copy. `validate` exits 2 on a template error.

A template sees only its kind's plain data plus `schema_version` and the filters `md_escape`,
`wrap(width)`, `date`, `truncate(limit)` and `bar(done, total, width)`, in a Jinja2 sandbox
(strict undefined, no attribute walks, no `open`, no imports, a time limit). The variables per
kind are listed in the README, section Exporting documents; the shipped template shows them
in use. The header and body digest are added around the output, and redaction runs on what the
template produced. Example, a changelog line that names its item: eject `changelog`, change
`- {{ e.line }}{{ ... by_date ... }}` to `- {{ e.line }} ({{ e.id }})`, then `ddflow export
validate changelog`, `ddflow export changelog --diff` and `ddflow export changelog --update`.
When a later ddflow changes the shipped default, `validate` and `doctor` note that your copy
is older; your copy is never touched (copy it aside, `eject --force`, diff, re-apply).

## Redaction and fencing

`[export].redact` (default true; `[export.<doc>].redact` per document) removes secrets,
private addresses, private hosts, home paths, emails and the machine hostname from the
rendered body, before the digest, so `--check` compares redacted output. The repository's own
name is not redacted, by design. The header carries `redacted=N redacted-kinds=...`
(`redacted=off` when switched off for that document). Over MCP the printed document is
wrapped in a `ddflow-record` fence naming its authors: it is data, not instructions. Files
written for humans are plain markdown. `replay` is not an export kind.

## Configuration

    [export]
    documents = ["roadmap", "status"]
    max_bytes = 60000          # the stdout / MCP cap; a file written is never capped
    redact = true              # on by default; [export.<doc>].redact = false switches one document off
    refresh = "off"            # off | merge | phase_close | docs_gate

    [export.roadmap]
    path = "docs/ROADMAP.md"
    mode = "whole"
    template = "tools/roadmap.md.j2"   # a Jinja2 template of your own for this kind
    filters = { limit = 50 }
    refresh = "merge"          # a document's own refresh wins over [export].refresh

Unknown keys are skipped with a warning, so a newer release's config does not stop an older
checkout.

## Refreshing automatically

`[export].refresh` (or `[export.<doc>].refresh`) says when a SELECTED whole-file document
regenerates itself: `off` (default, never), `merge` (`ddflow merge` regenerates it into the
item's branch and commits it, so it lands in the merge commit), `phase_close` (completing a
phase regenerates it in the working tree, not committed), `docs_gate` (recording the phase
`docs` gate `passed` regenerates, verifies and stores the documents' digests in the gate
evidence). A hand-edited or unmarked file is skipped with a note, region and append documents
are never refreshed automatically, and a refresh failure is reported but never fails the merge
or the completion. `merge` and `complete` print and return an `export_refresh` result (MCP
too); `ddflow cadence` lists `export_refresh` when an opted-in document is stale.

## Enforcement

The pre-commit check behind `[enforce].generated_views` covers exported documents: a staged
file whose first line is a `ddflow:generated doc=<kind>` header must equal a fresh export, with
`ddflow export <kind> --update` as the remedy; `doctor` notes a selected target that is stale or
hand-edited. Never edit a generated file by hand: review it with `ddflow export <kind>` and
change its source.

## Over MCP

`ddflow_export` with no `doc` lists the kinds; with a `doc` it returns the markdown (fenced as
agent-written data) and a `truncated` field. It writes only with `write=true` and a repo-relative `path`; `diff` and
`check` compare against a path. `action` = `list`, `enable`, `disable` or `validate` selects
documents and checks templates (an enable names the agent and the stop command; MCP never
locks, acknowledges or ejects). Exit codes everywhere: 0 done or fresh, 1 stale (`--check`),
2 could not run or nothing selected, 3 refused.
