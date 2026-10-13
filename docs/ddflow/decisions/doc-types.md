# Document types: format, location, section ownership, allowed sources

Decision document for task `B-dt-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-doc-types: Document types: shipped/project/config with extends, agents may add (surfaced, ackable, lockable); user content stored in the file and the log; cmd: sources only from operator config (agent-added stay inert until approved); ownership generated/human/seeded/draft; validation warns by default; document tasks get their own pipeline

status **accepted** · decided by operator · decided by opus-lead

**Decision.** Operator 2026-10-06 (B-dt-decide answers): (1) resolution shipped -> .ddflow/doctypes/<name>.toml -> [docs.types.<name>], later wins, extends; agents may add a type, shown in brief/doctor until acked, operator may lock; (2) answers to ask-sections are written into the document AND recorded as doc.answered events so replay rebuilds the document; (3) cmd: sources only in operator-written config; a command in an agent-added type stays inert until digest-approved (B-uni-approval); (4) ownership modes generated|human|seeded|draft, text outside markers is the user's; validation severity warn by default, block configurable; a document task gets its own editable pipeline outline -> user_content -> draft -> doc_check -> doc_review (cross-family) -> merge. Group name is 'ddflow docs' (D-unify).

Governs: `ddflow/services/doctypes/**`, `ddflow/templates/doctypes/**`
