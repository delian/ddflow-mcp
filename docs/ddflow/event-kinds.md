# Event kinds

Every event kind ddflow folds is a `[[kind]]` table in `ddflow/templates/events/<owner>.toml` —
one file per OWNER (the subsystem that writes and folds the kinds). A new kind is **one new file
(or one new table)** plus its handler in `core/model.py`: the registry discovers it by a sorted scan
of the owner files (`core/eventkinds.py` → `load_dir`, sorted owner files, a duplicate kind name
raises), and the surfaces that used to be hand-kept DATA lists are DERIVED from those declarations
(so none of the shared DATA files below is edited — the handler is the one code edit):

- `EVENT_KINDS` — the registry itself (`core.eventkinds.EVENT_KINDS`);
- `tests/fixtures/event_kinds.json` — the field-schema snapshot, from each kind's `fields`;
- `core/redact/log.py` `LOG_TEXT_FIELDS` / `LOG_VERBATIM_FIELDS` — from each kind's `text` / `lookup`;
- `tests/test_coherence_coverage.py` `CHECKED` / `coherence_exempt` `EXEMPT` — from each kind's
  `checked` / `exempt`.

## The `[[kind]]` schema

```toml
[[kind]]
name = "lease.waiting"
doc = "Who waits for which files and why."        # one trimmed line, one full sentence, no |
retention = "keep"                                # optional: keep (default) or compact
v = 1                                             # optional: equals `core.upcasters`'
fields = { holder = ["str"], on = ["list"], reason = ["str"] }  # each field's observed JSON types
text = ["reason"]                                 # free-text fields: redacted on the committed log
lookup = { holder = "an agent id compared" }     # a field looked up or compared, and why
checked = "items.task_add"                        # OR exempt = "why the kind files no checkable text"
```

`checked` and `exempt` are mutually exclusive; every kind the fold handles is one or the other,
except the `gate.<outcome>` RECORD kinds, which are exempt as a family by name. `text` and `lookup`
fields are members of `fields` (the example's `reason` is declared in `fields` above).

## New-kind recipe

1. `scripts/event_kind.py add <kind> --why "What it records." --field ... --text ... --lookup ...
   --checked ...` (or `--exempt ...`) writes the `[[kind]]` table to the owner file AND the upgrade
   fragment — nothing else declares the kind: the fixture, the redaction tables and the coherence
   classification derive from this table. (Hand-writing the table instead also means hand-writing
   the `unreleased/event_kind_added.<kind>.toml` fragment — `add` refuses a kind that already
   declares itself.)
2. Write the handler in `core/model.py` `HANDLERS` (code only you can write).

The contract is pinned by `tests/test_event_kind_discovery.py`: the fixture, the redaction tables
and the classification regenerate from the declarations byte-identically, and a throwaway kind in a
temp dir is discovered without editing the former shared files.
