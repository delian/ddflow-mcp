# Subagents: canonical store, copy-sync, when a subagent counts as a reviewer

Decision document for task `B-sub-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-subagents: Subagents: canonical .ddflow/agents/*.md copy-synced to each harness; another declared model family is enough to count as a reviewer

status **accepted** · decided by operator · recorded by hy-commit-events

**Decision.** Operator 2026-10-13 (AskUserQuestion, B-sub-decide). (1) STORE: canonical definitions live in .ddflow/agents/*.md in Claude's frontmatter format (the superset) with a ddflow block (role, write scope, gate routing, envelope per D-safeguards); ddflow COPIES them into each enabled harness's agent directory and detects drift (copy-sync, as D-skills-canonical-store). (2) REVIEWER RULE: a subagent whose DECLARED model is of a different family than the author counts as an independent reviewer for a gate; no per-subagent approval step is required. The declared model must be an explicit model id: 'inherit' cannot satisfy independence (B-sub-route). NOTE (open for the operator): this relaxes D-reviewer-trust for subagents; B-sub-trust (operator approval by digest for agent-AUTHORED or tool-widening definitions) is not removed by this answer and stays in force until the operator says otherwise.

Governs: `.ddflow/agents/**`, `ddflow/services/agents/**`, `ddflow/services/gates/reviewers.py`

## D-reviewer-trust: Agents cannot mint an independent reviewer: command reviewers are operator-only; tool-written HTTP reviewers count only after operator approval

status **accepted** · decided by operator · recorded by Monster3-ddflow-7267fe

**Decision.** Operator 2026-10-01 for bug B3f9b8a4ac0, option C: every agent surface (ddflow_configure, config --set/--append-toml from an agent, reviewers detect --write) refuses to write a [[reviewer]] of kind 'command'; only a person adds one. A tool-written HTTP reviewer entry is recorded with who wrote it (reviewer.configured: agent, entry digest); ddflow review stamps the digest into its evidence, and reviewer independence does not count reviews from an unapproved tool-written entry until a person runs 'ddflow reviewers approve <name>'. Like human gates, this makes tampering visible; it does not stop a shell edit of .ddflow/local/*.toml.

**Alternatives rejected.** (A) refuse every [[reviewer]] edit from agent surfaces: reverses 4dd7b3e's guidance for HTTP reviewers that detect can probe. (B) approval for every tool-written entry: still lets a command reviewer, which can print any verdict, be approved by habit.

Governs: `ddflow/services/configwrite.py`, `ddflow/api/setup.py`, `ddflow/services/gates.py`, `ddflow/api/review.py`

## D-skills-canonical-store: Project skills have one canonical directory that ddflow syncs into each agent's own

status **accepted** · decided by Monster3-bridge-cse_015NbhRCT5XtidHYRvkjBCLK-7267fe

**Context.** Each agent reads its own directory; Claude Code does not read .agents/skills.

**Decision.** Default canonical store is .agents/skills/ (the cross-client convention); [skills].canonical can name another. 'ddflow skill sync' makes symlinks (default), copies, or generated files in each enabled agent's directory, marks what it manages, never overwrites a skill it did not create, and reports drift. Skills already in an agent directory are adopted, not moved, until the operator asks.

**Consequences.** One edit reaches every agent; a foreign skill is never clobbered; Windows without symlinks falls back to copy with drift detection.

**Alternatives rejected.** Make .claude/skills canonical (rejected: other agents ignore it); copy-only (rejected: silent drift).

Governs: `ddflow/services/skillsync.py`, `ddflow/config.py`, `ddflow/surfaces/commands/skills.py`
