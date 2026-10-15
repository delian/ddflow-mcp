# Storage and deployment

Decision document for the task `ST-decide`. It restates the record below; the record in the event log is the source of truth (`ddflow decision show D-storage`). Evidence: research note R-storage-seam, docs/RESEARCH.md R14.

## D-storage

```
D-storage — Storage and deployment: staged remote queue with serve as the single writer, local-required commands with an optional paired executor, session config overlay, Windows probe first, in-memory backend
  status accepted · decided by operator

Context:
  Operator 2026-09-25 asked about remote operation without filesystem (R14, B109-B112); 2026-10-15 asked to keep and merge those items (note R-storage-seam).

Decision:
  Operator 2026-10-15 (AskUserQuestion, ST-decide). (1) REMOTE QUEUE: yes, staged after the log package split: writer ratchet (ST-writers), P-change-surface.split-log-records, the storage seam (B109), the locality column (ST-locality, B112), the merge-strategy interface (ST-merge-strategy, B111), then the acceptance (ST-acceptance). Remote stays opt-in and stateless per D-remote; ddflow serve is the single writer. (2) LOCAL-REQUIRED: worktrees, merge, tree-fingerprint evidence, command gates and hooks stay local; a remote client is refused with the local alternative, OR may hand the command to a paired local executor that the operator enables. (3) CONFIG OVERLAY: yes, a session layer: the client sends an overlay with the session; it ranks above project config and below operator-only keys, is logged with its digest, expires with the session, never widens operator-only settings and never carries the event log (ST-config-overlay). (4) WINDOWS: probe first (a Windows CI leg or probe before any support claim; B13 closes as won't-do if it fails). (5) MEMORY BACKEND: yes, built with the seam for test isolation. All as recommended.

Consequences:
  B109, B110, B111, B112 and B13 are unblocked behind ST-decide; ST-locality, ST-merge-strategy, ST-config-overlay, ST-adopters and ST-acceptance are scheduled in that order; a paired local executor becomes a task of the fleet/control-plane work.

Alternatives rejected:
  Seam only; park it all. Local only without an executor; remote runs everything. Server-side config only; persisted per-client overlay. Windows out of scope or supported now; memory backend later.

Governs: docs/ddflow/decisions/storage.md, ddflow/infra/storage*.py, ddflow/infra/fsio.py, ddflow/services/config_overlay.py
```
