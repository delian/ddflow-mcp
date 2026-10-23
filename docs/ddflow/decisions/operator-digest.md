# Operator queue and digest: ddflow computes what waits on the operator

**State: DECIDED (2026-10-23).** The operator accepted the default of every question in section 6; the answers are decisions D-OD-Q1..D-OD-Q9 (task `OD-decide`, phase `P-operator-digest`). Evidence: [research/operator-digest.md](../research/operator-digest.md).

Operator request, 2026-10-23, verbatim: "is this question for the status - what waits on me driven by claude or by the ddflow? if it is not ddflow, should we think of improving the periodical info that ddflow reports to also report progress, status, potential eta and what is waiting on the user, so if the user has missed it to be able to ask, as I periodically do"

## 1. Summary

What waits on the operator is compiled by the model today; ddflow shows only one approvals line. Make it a state-derived fact:

1. An **operator queue**: pure function `State x Config x now -> entries` (what, why, since, recommended default, exact answer command, who and how many items are blocked, severity that rises with age).
2. A **digest** that puts the queue on top and adds progress, movement since the operator last looked, in-flight, stalled and risks, and an ETA that is a labelled range or `unavailable`, never a guess.
3. **Delivery** through `ddflow digest` (CLI), an existing MCP family action (no new tool), a budgeted line in brief/SessionStart and the `Progress:` block, a generated `docs/ddflow/DIGEST.md`, and optional push through AW-sinks.
4. A **rule** that agents and the lead relay the queue entries verbatim and ask the operator with the exact entry (`ddflow digest --ask`), not a paraphrase.

## 2. Decided here when accepted

- Deterministic: same state, same `now`, same text; `--json` carries the data.
- ETA: range, "estimate", method named, confidence from sample size, `unavailable` when data is missing, conditional on pending operator answers.
- Owner split: the default operator view lists operator-owned entries; lead-owned ones are shown with `--all`.
- Missed items resurface with age and escalate by a table; an entry never disappears by being shown; an agent printing the digest does not count as the operator having seen it.
- No new MCP tool (D-mcp-new-nouns); one `[digest]` section, layered by D-config-layer-order, company floors tighten-only.

## 3. Options

| | A: extend `status` | B: `digest` view + brief hook | C: B + sinks + `DIGEST.md` |
|---|---|---|---|
| Fixes missing items | no (pull only) | partly (every session start) | yes (push) |
| Cost | small | medium | B + AW-sinks |
| Main risk | status bloat | duplicates inbox/DX-digest unless folded | push spam / leak |

**Recommended: C, delivered as B first** (cumulative; stopping at B is complete for the question asked).

## 4. Queue (phase P-operator-digest, prefix OD-)

| Id | What | Needs | Writes (summary) |
|---|---|---|---|
| OD-research | this note and proposal | none | the two docs |
| OD-decide | ask the operator Q1-Q9, record answers | OD-research | decisions doc |
| OD-inventory | the source table as data: kinds, severity, collector, answer command, chat route; test fails on an unregistered source (reuses `approvals/sites.py`) | OD-research | `services/digest/sources.py` |
| OD-queue | the collector (pure), ranking, age escalation, impact, `[digest]` knobs | OD-inventory | `services/digest/queue.py`, `config_sections/digest.py` |
| OD-cli | `ddflow digest [--queue] [--all] [--json] [--ask]`; `status` line becomes the combined line | OD-queue | `api/digest.py`, `surfaces/**/digest.py` |
| OD-digest | progress, per-phase, merged-since, in-flight, stalled, risks, ETA (three methods) | OD-cli | `services/digest/{progress,eta,render}.py` |
| OD-seen | per-operator cursor, NEW / still-waiting markers, `--mark-seen`, `--since` default | OD-cli | `services/digest/seen.py` |
| OD-brief | budgeted brief/SessionStart line and the `Progress:` addition | OD-cli, RA-brief | `services/digest/brief_line.py`, `services/progress_line.py` |
| OD-export | `ddflow export digest` -> `docs/ddflow/DIGEST.md`, `--check` | OD-digest | `services/export/kind_digest.py`, `templates/doctypes/digest.toml` |
| OD-sinks | push classes `operator_queue.new`, `red_main`, `spend_limit`, `digest.periodic`; cadence `digest_push` | OD-seen, AW-sinks | `services/digest/push.py` |
| OD-mcp | the family action, parity test | OD-cli, MTD-family-route | `surfaces/declared/digest.py`, tests |
| OD-prompt | the relay-verbatim rule, `--ask` text, driver line | OD-cli | `templates/prompts/digest.md` |
| OD-e2e | replay over the real log, determinism, token-budget, resurfacing, adversarial (agent cannot mark seen) | OD-digest, OD-seen, OD-brief, OD-export, OD-sinks, OD-mcp, OD-prompt | `tests/test_digest_e2e.py` |
| OD-docs | operator doc, help topic, examples | OD-e2e | `docs/ddflow/operator-digest.md` |

First usable slice: OD-research -> OD-inventory -> OD-queue -> OD-cli (no other need).

## 5. Folds (proposed; lead acts only on items nobody holds)

AW-inbox into OD-queue; DX-status-digest into OD-digest (OD-cli owns the `digest` verb); AW-since-cursor into OD-seen. AW-sinks, AW-event-classes, RA-brief, `progress_line.py`, `metrics forecast` are reused, not folded.

## 6. Questions for the operator (recommended default first)

| Q | Question | Default | Alternative |
|---|---|---|---|
| Q1 | Which option? | C, built as B first | A only; B only |
| Q2 | Who is "the operator" queue for: only entries a person must answer, with lead-owned entries behind `--all`? | Yes | one list for both |
| Q3 | Escalation of an unanswered entry | `escalate_after = "1h, 24h"`: nudge at creation, needs after 1h, urgent after 24h (`escalate_after_red_main = 6h`) | none; fixed |
| Q4 | Fold AW-inbox, DX-status-digest and AW-since-cursor into OD-queue / OD-digest / OD-seen? | Yes | keep separate and make OD depend on them |
| Q5 | ETA shown where? | digest and `Progress:` per phase, range plus "estimate"; never in SessionStart | nowhere; everywhere |
| Q6 | What does SessionStart/brief carry? | one line: count, oldest age, top 3 titles | nothing (rule only) |
| Q7 | Push? | Yes, through AW-sinks, on new entry, red main, spend limit, and one daily digest; quiet hours default 22:00-07:00 except red main | on change only; never |
| Q8 | The relay rule | agents and the lead must show the queue entries verbatim and ask with `digest --ask` | advisory |
| Q9 | Generated `docs/ddflow/DIGEST.md` committed? | Yes: committed, regenerated by `ddflow export` on cadence, not on every push | local only |

### Answers (operator, 2026-10-23)

| Q | Answer | Decision |
|---|---|---|
| Q1 | C, built as B first | D-OD-Q1 |
| Q2 | Yes: person-owned entries only; lead-owned behind `--all` | D-OD-Q2 |
| Q3 | `escalate_after = "1h, 24h"`, red main 6h | D-OD-Q3 |
| Q4 | Yes, fold (only items nobody holds; else link and note) | D-OD-Q4 |
| Q5 | Digest and `Progress:` per phase, labelled range; never SessionStart | D-OD-Q5 |
| Q6 | One line: count, oldest age, top 3 titles | D-OD-Q6 |
| Q7 | Yes via AW-sinks; quiet hours 22:00-07:00 except red main | D-OD-Q7 |
| Q8 | Enforced relay rule, `digest --ask` | D-OD-Q8 |
| Q9 | Yes: committed, regenerated by `ddflow export` on cadence, not on every push | D-OD-Q9 |

## 7. What would invalidate this

See research section 10: lead-owned entries dominate; budget forbids the extra line; ETA samples never suffice; AW-sinks re-scoped; a detector proves unreliable.
