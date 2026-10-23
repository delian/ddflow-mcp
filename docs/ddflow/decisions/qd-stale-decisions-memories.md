# Decision: spent decisions, reviewer holder, stale memories

Source: QD-decide-stale-decisions-memories, queue dedupe review 2026-10-23. Operator answer 2026-10-23 (relayed by the lead, not a verbatim quote): retire the six more spent decisions; refresh the reviewer-holder decision to what is actually true, flagging any operator-only change; ddflow chore approve is the operator's act.

## 1. Spent decisions (D-spent-decisions-2026-10-23-r2)
Retired by supersession, history kept, evidence checked in the queue today:
- D-cleanup-before-pause and D-cleanup-accept-bar: B-uc-acceptance is done (the cleanup it governed ended; R-unify-acceptance REFUTED and answered).
- D-dry-run-from-running-version: B-upgrade.12-dry-run is done, phase B-upgrade done 21/21, scripts/ci/upgrade-dry-run implements it.
- D-bug-override-accepted-2026-10-11: B-proc-stdin-resume and B-gitmaint-lock are fixed; the one-off acceptance is spent.

NOT retired, evidence says still live (qa-dupes listed them as spent; the lead should confirm):
- D-audit-calls-2026-10-10: B-gap-command-trust and B-gap-checks-publish are still blocked, B-rules-scope, B-ds-watch, B-mcp-http-transport, B-ds-semantic are still open; its calls (a), (d), (e) bind them.
- D-old-work-behind-core-docs-accepted: the docs wave is not done (P-docs-generic has 14 open tasks; P-doc-templates, P-roadmap, P-docs-search open) and D-old-work-behind-core-docs-r2 says the rule "stays in force".

## 2. Reviewer holder (D-reviewer-holder-r3)
What is true (`ddflow reviewers list`, 2026-10-23): lan-deepseek-v4-pro (family deepseek) holds critic and rubber_duck; kimi-k3 (moonshot), qwen3.8-flash-next-fp8, lan-deepseek-v4.1-flash and the two ext-* entries carry no gates (disabled or spare). One role-based record supersedes D-lan-deepseek-reviewer-r2, D-reviewer-holder-now and D-rubber-duck-on-pro-host. No address is written in it.
OPERATOR-ONLY, not changed here: the operator's auto-memory files 'Kimi reviewer key' (says kimi-k3 holds critic and rubber_duck) and 'Run tests in parallel' (-n 48) are stale; they live outside the repository.

## 3. Memories
Me5db019bea (fixed -n 24 / -n 16) and M1da6c78dd8 (names a private reviewer address) re-recorded as current, address-free statements. The old text stays in the append-only event log (an operator-only history rewrite, if wanted: D-event-log-redaction-forward).

## 4. OP-chore-approve
`ddflow chore approve` is a person's act: the operator runs it from their own terminal. It has waited since 2026-10-17. Not run here.
