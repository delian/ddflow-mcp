# Decision management: categories, enforcement, who accepts agent-proposed decisions

Decision document for task `B-dec-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-decision-management: Decision management: warn by default (block per decision); agents may accept decisions but each agent acceptance is flagged for the operator and reversible; waivers need the operator and expire within 90 days; recommended categories, command-check approval, shipped packs and two-way ADR sync

status **accepted** · decided by operator · decided by opus-lead

**Decision.** Operator 2026-10-06 (B-dec-decide answers): (1) default enforcement warn, shown in gate status, brief and review; a decision may be set to block (gate fails until fixed or waived) or advisory; (2) an agent may accept a decision (proposed -> accepted), but every agent acceptance is flagged 'accepted by agent' in brief, doctor and decision list until the operator acknowledges it, and the operator can reverse it; inferred and preset decisions follow the same rule; (3) a waiver is approved only by a person, carries reason, file scope and an expiry of at most 90 days; an expired waiver makes the violation live again; (4) categories architecture, infrastructure, coding-style, libraries, dependencies, upgrade, testing, security, data, process (extensible); a check runs a command only from operator config or after digest approval; shipped packs python-library, python-service, web-frontend, go-service, container-infra, testing-baseline, security-baseline, upgrade-policy; ADR files in docs/adr in two-way sync with decisions.

Governs: `ddflow/services/decisions/**`, `ddflow/api/decisions.py`, `ddflow/templates/decision_packs/**`
