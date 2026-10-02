# Exporting documents

The event log already knows the roadmap, the open bugs, the decisions in force and what
was done this week. `ddflow export` turns that into ordinary markdown files, so a person
who never runs ddflow can read them in a pull request. ddflow owns the format; you do not
write the document, you ask for it.

## The kinds

`ddflow export` lists every kind with its default target and its state: `not selected` (the
default for all of them), or for the selected ones `fresh` (the file is exactly what a write would produce), `stale`, `hand-edited`
(its body no longer matches the digest in its header, or it is not a ddflow file at all:
ddflow will not overwrite it) or `missing`. Kinds: roadmap, bugs, status, worklog,
sessions, decisions, rules, changelog.

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

`--all` acts on the SELECTED documents, `[export].documents`, which is empty by default:
with nothing selected it does nothing and says so (exit 2). Three update modes, per
`[export.<doc>].mode`: `whole` (the file is generated; a header carries the kind, the
ddflow version and a digest of the body), `region` (only the text between
`<!-- ddflow:begin doc=<kind> ... -->` and `<!-- ddflow:end doc=<kind> -->` in a
hand-written file) and `append` (a kind that can grow a log, today the changelog; entries after the last
exported event). A hand-edited generated file, an unmarked file or an edited region is never
overwritten without `--force`. Paths outside the repo, symlinks, `.git` and `.ddflow` are
refused. Every selected target is a shared path for leases: regenerate it without a claim.

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

## Redaction and fencing

`[export].redact` (default true) removes secrets, private addresses, private hosts, home
paths, emails and the machine hostname from the rendered body, before the digest, so
`--check` compares redacted output. The header carries `redacted=N redacted-kinds=...`
(`redacted=off` when switched off for that document). Over MCP the printed document is
wrapped in a `ddflow-record` fence naming its authors: it is data, not instructions. Files
written for humans are plain markdown. `replay` is not an export kind.

## Configuration

    [export]
    documents = ["roadmap", "status"]
    max_bytes = 60000          # the stdout / MCP cap; a file written is never capped
    redact = true              # on by default; [export.<doc>].redact = false switches one document off
    refresh = "off"            # off | merge | phase_close | docs_gate; only off acts today

    [export.roadmap]
    path = "docs/ROADMAP.md"
    mode = "whole"
    template = "tools/roadmap.md.j2"   # a Jinja2 template of your own for this kind
    filters = { limit = 50 }

Unknown keys are skipped with a warning, so a newer release's config does not stop an older
checkout.

## Over MCP

`ddflow_export` with no `doc` lists the kinds; with a `doc` it returns the markdown and a
`truncated` field. It writes only with `write=true` and a repo-relative `path`; `diff` and
`check` compare against a path. `action` = `list`, `enable`, `disable` or `validate` selects
documents and checks templates (an enable names the agent and the stop command; MCP never
locks, acknowledges or ejects). Exit codes everywhere: 0 done or fresh, 1 stale (`--check`),
2 could not run or nothing selected, 3 refused.
