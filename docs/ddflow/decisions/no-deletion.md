# No deletion: spent records stay, so the project can be replayed and regenerated

Decision document for task `DX-no-deletion-doc`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-no-deletion`).

## D-no-deletion: Nothing is deleted

status **accepted** · decided by operator · recorded by dec-recorder6

**Operator, 2026-10-23, verbatim.** "In principle decisions that are spent, and tasks that are implemented or bugs, should not be deleted - we would like to be able to replay the chat, requirements, and logs, fold them, and regenerate the project from scratch, just by the chat and combined requirements, decisions, etc." The operator then answered YES to recording this as a decision and to adding a replay round-trip test.

**Context.** Regeneration (`D-seed-extract`, phase `P-seed-extract`) and `ddflow replay` rebuild a project from its records. They are only as good as the records that survive. Folds have already lost information (`DX-fold-carries-needs`: a folded task's needs vanished), and a decision extension edited the old record (`B-decision-extends-mutates-old-record`).

**Decision.**

1. Closing, abandoning, folding, superseding and forgetting are **state changes only**. A spent decision stays with status `superseded`; a done task, a closed bug and an abandoned item stay with their text, links and needs.
2. **No agent and no command removes a record or an event.** A command that deletes is a bug.
3. A **fold carries** the folded task's text, links and needs into the keeper (`DX-fold-carries-needs`).
4. **Event kinds are tombstoned, never deleted** (`D-DEP-Q11`; the stubs it removes at a major are kind definitions, not records).
5. Extending a record files a **new** record linked to the old one; it never edits the old one (`B-decision-extends-mutates-old-record`).
6. **Redaction is the one sanctioned exception.** Where private text (an address, a secret) was recorded, it may be removed from the log, and the removal must itself be an **auditable event** naming what was redacted and why, never a silent rewrite (`D-event-log-redaction-forward`). A memory re-record therefore must not leave old private text where redaction is required (`B-memory-rerecord-keeps-old-text-in-log`).
7. The rule is enforced by a **replay round-trip test** (`DX-replay-roundtrip`): replaying the log from scratch gives the live folded state, and no record or event id disappears.

**Alternatives rejected.** Deleting spent decisions and finished tasks to keep the queue small (loses the requirements trail replay and seed need). Allowing delete on fold (fold already dropped needs silently). Rewriting the log on re-record (unauditable). Archiving spent records outside the log (a second source of truth).

**Consequences.** The log only grows; size is managed by folding the view (briefs, snapshots, cuts), not the records. Removal commands must tombstone. Privacy corrections need the redaction event. Every new event kind needs a tombstone path.

**Links.** Extends `D-seed-extract`; related `P-seed-extract`, `DX-fold-carries-needs`, `B-decision-extends-mutates-old-record`, `B-memory-rerecord-keeps-old-text-in-log`, `D-DEP-Q11`, `D-event-log-redaction-forward`.

Governs: `docs/ddflow/decisions/no-deletion.md`, `ddflow/core/**`, `ddflow/api/lifecycle/**`, `ddflow/services/fold*`
