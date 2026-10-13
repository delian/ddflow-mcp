# Test domains: verdicts, pins and waivers

Decision document for the task `TD-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-test-domains`).

## D-test-domains: Test domains: inconc, error, skip, licence failure and missing pins are unavailable and block completion until a waiver

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked for BDD, telecom conformance, packet-network and HDL test support; note R-test-domains.

**Decision.** Operator 2026-10-13 (AskUserQuestion, TD-decide): a native verdict is kept next to the mapped outcome; inconc, error, skip and licence failure map to unavailable; unavailable blocks completion until a waiver with a reason is recorded; a run missing its pins satisfies no gate for conformance kinds (warn for others); a bounded formal pass is bounded-pass with its depth, never proved. Remaining defaults accepted: artifacts by reference in a configurable store; vendor and lab tools only from an operator allowlist with fixed argument templates; hosted certification recorded as external attestation; first importers JUnit, Cucumber, Robot, cocotb, sby, Batfish; equipment leases time out and release on a crash.

**Consequences.** TD-verdict, TD-pins, TD-attest, TD-lease and TD-vendor-tools implement this; completion tooling needs a waiver record.

**Alternatives rejected.** unavailable shown but never blocking; inconc and skip treated as failed.

Governs: `docs/ddflow/decisions/test-domains.md`
