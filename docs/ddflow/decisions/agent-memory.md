# Agent memory: what context to capture, where, retention, embeddings, handoffs

Decision document for task `B-am-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-agent-memory-defaults: Agent memory: the recommended defaults are decided

status **accepted** · decided by operator · recorded by opus-lead

**Context.** R-agent-memory; phase P-agent-memory.

**Decision.** Operator 2026-10-10 accepted the eight recommended defaults of R-agent-memory: (1) capture lane briefs and reports committed and redacted, turn text in a local-only index; (2) committed pointers, briefs, reports and handoffs, local turn text; (3) 500 MB and 90-day local cap, handoffs and pointers kept forever, committed caps 8 KB per brief or report and 6 KB per handoff; (4) BM25 default, semantic rerank only when [rag] is configured, never a download; (5) handoff = deterministic skeleton + the agent's narrative + the harness summary verbatim, no extra model call by default; (6) cross-agent search on by default, fenced, with an opt-out knob; (7) loop reminders warn only, blocking stays opt-in via [loops].on_detect; (8) <private> spans stripped before storing in addition to the redaction profile.

**Alternatives rejected.** decide at B-am-decide; prompts only; full transcripts committed.

Governs: `docs/ddflow/decisions/agent-memory.md`, `ddflow/services/transcripts.py`, `ddflow/services/handoff.py`
