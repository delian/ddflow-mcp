# Managed regions in every file, the hand-edit policy, the roadmap

Decision document for task `B-tpl-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-doc-regions: One marker grammar for every ddflow-managed region, with name and hash; a hand edit inside a region is 3-way merged; formatter pragmas only when a formatter is detected

status **accepted** · decided by operator · decided by opus-lead

**Decision.** Operator 2026-10-06 (B-tpl-decide answers): (1) every managed region in every file (exports, ROADMAP, SECURITY, CLAUDE.md/AGENTS.md blocks, views) uses <!-- ddflow:begin <doc>/<region> sha=<12hex> --> ... <!-- ddflow:end <doc>/<region> -->, replacing DDFLOW:BEGIN, ddflow:begin doc= and ddflow:generated (migrated by the upgrade step); (2) a hand edit inside a region is merged automatically on refresh (3-way, git merge-file, base = the last render whose hash is in the marker); only a conflict stops and shows the diff, with --keep-mine (move the edit to a user section) and --force; every merge is reported; (3) formatter-ignore pragmas (prettier, markdownlint, mdformat) are added around regions only when the project uses that formatter (detected from its config).

Governs: `ddflow/services/export/**`, `ddflow/services/adopt.py`, `ddflow/views/**`

## D-roadmap: ROADMAP: Now/Next/Later by commitment and dependency order, internal BL-* batches hidden by default, a throughput ETA hint marked as an estimate

status **accepted** · decided by operator · decided by opus-lead

**Decision.** Operator 2026-10-06 (B-tpl-decide answers): Now = phases with work in flight; Next = the top N ready phases in dependency order; Later = waiting on dependencies; BL-* internal batches hidden by default (a switch shows them); an ETA hint from measured throughput is shown and labelled an estimate.

Governs: `ddflow/services/export/kind_roadmap.py`, `ddflow/templates/export/roadmap.md.j2`
