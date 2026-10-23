# Decision: new record kinds and the tool diet (D-mcp-new-nouns)

Source: QD-decide-new-nouns-vs-tool-diet, queue dedupe review 2026-10-23. Operator answer 2026-10-23 (relayed by the lead, not a verbatim quote): new record kinds (policies, deprecations, approvals, commit templates, ...) are actions of ONE record family, not new MCP tools.

## Decided
- A new record kind adds NO MCP tool. It is reached as an action of the one record family (`ddflow_record`, the `kind` as an argument, verbs as in `ddflow record <kind> <verb>`), within D-mcp-tool-families (24 advertised tools).
- Exceptions only for `ddflow_run` (the long-operation poll) and the search/call facade (`ddflow_search` / `ddflow_call`, PM-search-call, MTD-search-call).
- Gated actions keep a shared optional approval param (RA-mcp); that is a parameter, not a tool.
- The boilerplate "no new MCP tool (tools/list budget has ~45 bytes of headroom)" is void and is deleted from task bodies as they are touched; each listed task names its record-family action instead.

## Amended now
GP-mcp, DEP-mcp, CM-mcp, RA-mcp (the four phases' MCP tasks: their kinds become `ddflow_record` actions, their other family actions unchanged), and the 16 tasks that named their own tool: B-goal-model, B-prop-model, B-perf-bench, B-sched-author, B-skills-events, B-sub-events, KB-config, LB-surfaces, US-surfaces, RQM-surfaces, SPEC-surfaces, P-agent-caps.slots, P-context-sync.tell, P-safeguards.model (RM-run-tool-mcp and PM-search-call are the named exceptions).
