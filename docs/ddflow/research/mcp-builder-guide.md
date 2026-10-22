# Anthropic's MCP-server guide vs ddflow's own MCP server

Research item MCP-guide-research, 2026-10-22. The operator asked which practices of Anthropic's
"mcp-builder" skill apply to ddflow, which is itself an MCP server. The guide text is the
operator's paste (recorded as prompt `h-29b9661e-fc8b-5e4b-85cc-bfeb533757a2#p222`); the skill
itself was not re-read.

Evidence labels: **VERIFIED** = read in the primary source this session (spec page, or ddflow
source or a measurement on this tree, with file and line); **UNVERIFIED** = from the guide's
wording, a memory of the spec, or a page not opened; **THEORETICAL** = reasoning only.

## 1. What the spec actually says (VERIFIED against modelcontextprotocol.io, 2026-10-22)

Pages read: sitemap, `specification/2026-07-28/server/tools`, `.../basic/transports`,
`.../server/utilities/pagination`, `.../schema` (ToolAnnotations), and `2025-06-18/server/tools`.
The newest dated revision in the sitemap is 2026-07-28; ddflow already serves it
(`ddflow/surfaces/mcp_protocol.py`).

- Tool fields: `name`, `title`, `description`, `icons`, `inputSchema`, `outputSchema`,
  `annotations`. Annotations "MUST" be treated by clients as untrusted unless the server is
  trusted; they are hints, not guarantees.
- `ToolAnnotations`: `title`, `readOnlyHint` (default false), `destructiveHint` (default **true**,
  meaningful only when not read-only), `idempotentHint` (default false, same proviso),
  `openWorldHint` (default **true**). So an un-annotated server is described to clients as
  "may be destructive, open-world, not read-only": the defaults are the worst case.
- Output: "Servers MUST provide structured results that conform to [the] schema" when an
  `outputSchema` is given; "a tool that returns structured content SHOULD also return the
  serialized JSON in a TextContent block". In 2026-07-28 `structuredContent` may be any JSON
  value, and an `outputSchema` may be an array schema (2025-06-18 required an object).
- Errors: protocol errors (JSON-RPC) for unknown tool and malformed requests; tool-execution
  errors (`isError: true`) for "actionable feedback that language models can use to self-correct",
  explicitly including "input validation errors (e.g., date in wrong format, value out of range)".
  Clients "SHOULD provide tool execution errors to language models".
- Servers "MUST": validate all tool inputs, rate limit, sanitize outputs.
- `tools/list`: "SHOULD return tools in a deterministic order" (prompt-cache friendly); the set
  "MUST NOT vary per-connection"; pagination via opaque `nextCursor` is supported; 2026-07-28
  results carry `resultType`, `ttlMs`, `cacheScope`. Tool names: 1-128 chars, `[A-Za-z0-9_.-]`.
- Pagination: cursors are opaque, page size is the server's choice, invalid cursor -> `-32602`.
  This covers list operations of the protocol only; a tool's own result paging is a server design.
- Transports: stdio (newline-delimited, client-launched subprocess) and Streamable HTTP; custom
  transports allowed. The spec page does not say "stdio for local, HTTP for remote"; that is the
  guide's advice (UNVERIFIED as a spec statement, sensible as practice).
- NOT opened: the best-practices page and the Inspector pages (they exist in the sitemap under
  `docs/2026-07-28/tools/inspector/*`). Inspector CLI flags below are therefore UNVERIFIED.

## 2. Rating, practice by practice

Rating: MET / PARTLY / GAP / N-A. Evidence is ddflow source or a measurement on this tree.

| # | Guide practice | Rating | Evidence |
|---|---|---|---|
| 1 | Consistent prefix, action-oriented names | MET | All 123 tools are `ddflow_*`, snake_case, within the spec name rule. The tool-diet phase turns them into `ddflow_<noun> action=<verb>` families (D-mcp-tool-diet). |
| 2 | Concise descriptions | MET | Mean 275 chars, max 654 over the 123 tools (measured). Budget tests: `tests/test_mcp_tool_budget.py`. |
| 3 | Focused results, filtering, pagination | PARTLY | Bounded reads with an honest cut notice: `ddflow/surfaces/mcp_bound.py` (5 tools, `ROWS_SHOWN` 25, `limit=0` = most). `ddflow_list` has `limit`/`offset`/`next` (`surfaces/tools/operations.py:28-47`). No tools/list cursor (not needed at 24-123 tools). Gap: no systematic check that every list-returning tool is bounded (only 5 are). |
| 4 | Actionable errors | PARTLY | Strong: `isError` results, did-you-mean for tool and argument (`mcp.py:837-860`, `registry.unknown_tool_hint`), the reason leads the body, `_meta.exit` carries the 0/1/2/3 vocabulary. Weak: "missing required argument(s): x" names no description or example; no type, enum or range check (section 3, B1, B2). |
| 5 | API coverage vs workflow tools | MET | Workflow tools (`brief`, `next`, `claim`, `complete`) plus composable primitives; the diet decision keeps 9 hot singles and folds the rest into families. The open risk (action-enum accuracy) is already MTD-accuracy-eval. |
| 6 | Input schemas: constraints, descriptions, examples | GAP | Properties are `(type, description, required)` tuples (`registry.properties_schema`, `registry.py:501`). Measured: 0 of 488 properties carry `enum`/`minimum`/`pattern`. The machinery half exists: `Param.choices` validates on the CLI, and `schema_enum` (`registry.py:294-296`, `:411-412`) can emit `enum` but is off everywhere to keep the served bytes unchanged, so the MCP side never sees or checks the choices; enums live in prose ("task\|phase\|bug\|..."); 19 of 488 descriptions have an example. Descriptions are otherwise clear. |
| 7 | outputSchema + structuredContent | PARTLY (deliberate) | Implemented (`mcp.py:143-158`, `:326-328`, `[mcp].output_schemas`), default **off**: research Rd25385ec42 [CONFIRMED] measured more than 10 KB added to tools/list and double result size; D-mcp-surface-r2 and the lean-start work make bytes a first-class cost. The guide's "both text and structured" is met when on. Nothing to queue; revisit only if a client needs it. |
| 8 | Tool annotations | GAP | `grep` finds no `annotations`, `readOnlyHint` or `title` in `tools/list` (`mcp.py:749-762`). Clients therefore see every ddflow tool, including `ddflow_list`, as possibly destructive and open-world, and cannot auto-approve reads. The CLI/MCP matrix knows enough to derive them; `MTD-permissions` needs the same read/destructive classification. |
| 9 | Transport | MET / N-A | stdio, newline-delimited, with the shim/worker split in P-soft-upgrade, and the 2026-07-28 stateless mode (`mcp_protocol.py`). Streamable HTTP is N-A: ddflow is a per-repository local server; no remote use case is filed. |
| 10 | Shared infrastructure, DRY | MET | One `Outcome` -> result path (`_outcome_result`), one `_text`, one registry that generates CLI, MCP schemas and parity tests (`tests/test_mcp_parity.py`, `tests/test_cli_mcp_parity_reverse.py`). JSON vs Markdown: JSON by default, `text` tools for documents. |
| 11 | MCP Inspector / build self-check | PARTLY | Strong conformance tests (`tests/test_mcp_protocol_conformance.py`, `test_mcp_modern_protocol.py`, goldens `tests/golden/test_golden_mcp_tools.py`). No standalone self-check that spawns the real stdio server and lints the advertised surface (schema validity, name rule, annotations present, deterministic order); Inspector never used. |
| 12 | Evaluations | GAP | There are no LLM-usability evals. `tests/eval/` does not exist; the only planned one is MTD-accuracy-eval (tool-call accuracy of flat vs families, not "can an LLM answer real questions"). Section 5 designs it. |
| 13 | Deterministic tool order (spec SHOULD) | MET | `sorted(TOOLS.items())` in `_m_tools_list`. |
| 14 | Validate all inputs (spec MUST) | PARTLY | Unknown and missing arguments are refused; types, enums and ranges are not checked at the boundary (B1, B2). |
| 15 | Unknown tool as protocol error | DELIBERATE DEVIATION | ddflow returns `isError` results even for an unknown tool (`mcp.py:836`, docstring lines 23-26) so the model sees the did-you-mean. The 2026-07-28 text makes unknown tool a JSON-RPC error and says clients "MAY" show those to the model. Keep; record the reason if challenged. |

## 3. Defects found while auditing (filed as bugs B-mcp-required-falsy and B-mcp-no-type-enum-check; their fix tasks sit in the queue beside P-mcp-practices, since `update` cannot move a task into a phase)

- B1 `Server._check_call` (`mcp.py:842`) treats any falsy value as missing:
  `not args.get(n)`. A required boolean passed as `false` is refused as "missing required
  argument(s)". Measured: `ddflow_phase_close` has a required boolean `imported`; `imported=false`
  cannot be sent. (`ddflow_job_add.pid=0` is the same class, harmless in practice.)
- B2 No type, enum or range validation at the MCP boundary: `_check_call` returns "" for
  `ddflow_list {kind:"nonsense"}` and `{limit:"banana"}` (measured). The spec says servers MUST
  validate inputs and that validation errors are the self-correcting kind of tool error. The api
  layer may refuse later, with a message not shaped for the schema.

## 4. What to take, what to leave

Take (queued as P-mcp-practices): annotations derived from one effect classification; schema
constraints rendered and enforced; examples where an eval shows they help; a result-size audit
for every list-returning tool; a stdio self-check; and the usability eval set.

Leave, with reasons: `outputSchema` on by default (bytes, section 2 row 7); tools/list paging
(24 tools); Streamable HTTP (no remote case); the guide's "XML qa_pair file" format is adopted
as is, because it costs nothing and any evaluation harness can read it (UNVERIFIED that the
skill's own harness reads exactly this shape; the design below does not depend on it).

Coordination with running phases (do not duplicate):

- P-tool-diet owns families, profiles, hints and the tool-accuracy eval. Annotations on a family
  tool cannot be per action (a hint applies to the whole tool), so a family with mixed actions
  must be annotated for its worst case (not read-only, destructive); the read-only profile of
  MTD-permissions removes the destructive actions instead. Hence MCPP-annotations depends on
  MTD-profiles and the effect table is shared with MTD-permissions (MCPP-effect-class).
- P-lean-start measures session-start bytes. Annotations cost about 30-60 bytes per tool and
  examples cost more; both are budgeted by test, and examples are added only where the eval
  proves benefit.
- P-soft-upgrade changes the interface digest; annotation and schema changes are "additive" or
  "changed schema of an existing tool" in its 5-rung rule, so they ship through its manifest.

## 5. Proposed ddflow eval set (design)

Purpose: test whether an LLM can use the ddflow MCP server to answer real project questions,
which neither the parity tests nor MTD-accuracy-eval (tool choice accuracy) measure.

### Fixture

A deterministic fixture project `tests/eval/mcp_usability/fixture/` built by a script from a
fixed event log (fixed ids, timestamps and agent names, no network, no model calls): 3 phases,
about 40 tasks in mixed states (done, running, blocked by a dependency, blocked by a file
conflict), 6 bugs (two fixed with a regression test), 5 decisions (one superseded), 4 lessons,
2 research verdicts, gate outcomes on four items (one `unavailable`, one skipped with a reason),
and one open lease held by `agent-a`. Read-only for the eval: the harness copies it to a temp
directory per run.

### The ten questions (all read-only, single string answers)

Each has one answer comparable by exact string match, derived by a gold script from the
event log (never typed by hand), so the question set cannot rot silently.

1. Which task is the only one blocked solely by a file-glob conflict, and which item holds the
   conflicting files? (`<task-id> / <holder-id>`)
2. How many tasks in phase P-alpha are done? (integer)
3. Which decision superseded D-first, and who decided it? (`<id> / <by>`)
4. What reason was recorded when the `critic` gate on `T-17` was skipped? (exact reason)
5. Which bugs are fixed but have no regression test recorded? (sorted comma list)
6. Which agent holds the oldest unexpired lease, and for which item? (`<agent> / <item>`)
7. Which gate is recorded `unavailable` on the most items? (gate name)
8. What does lesson L-cache-keys say to do before editing the cache module? (the first
   imperative sentence, exact; chosen so only `lesson_search`/`show` can answer)
9. Which ready task has the lowest priority number among tasks tagged `dx`? (id)
10. List, in order, the tasks that must finish before `T-30` can start (transitive needs,
    topological order, comma list).

Selection rules from the guide, applied: independent (no question depends on another's answer),
read-only (no claim, no write), complex (each needs 2-4 tool calls and a join), realistic (the
operator's and agents' real daily questions), verifiable (one string), stable (fixture is frozen;
a fixture change regenerates the gold file in the same commit).

### Files

```
tests/eval/mcp_usability/
  build_fixture.py        # deterministic fixture builder
  gold.py                 # derives every answer from the fixture's event log
  questions.xml           # <qa_pair><question/><answer/></qa_pair> x 10, generated from gold.py
  oracle.py               # scripted tool-call sequence per question (no LLM)
  run_eval.py             # LLM-driven runner (opt-in)
```

### Harness

- Oracle mode (CI, no LLM, cheap): for each question, run the scripted calls over the real stdio
  server (`ddflow mcp --repo <fixture copy>`) and assert the derived answer equals the gold
  answer. This proves each question is answerable through the advertised surface, and fails when
  a tool diet, rename or bound hides the data (the failure the diet could cause). Runs in the
  normal suite; no key needed.
- LLM mode (opt-in, never in the default gate): `run_eval.py --model <id> --surface families`
  starts the server on the fixture, gives the model only the advertised tools, runs a tool loop
  (cap 12 calls) and compares the final answer string to the gold. It records per question:
  correct, calls used, wrong-tool or bad-argument errors, result bytes read, and wall time, and
  writes one JSON file. Run with two model families (the independent-reviewer rule applies to
  the evaluators too) and with each profile once the diet lands (`flat`, `families`, `lean`).
  No key or model -> the run is reported `unavailable`, never passed.
- Reuse: share the stdio client and tool-loop with MTD-accuracy-eval if it lands first
  (`tests/eval/tool_diet/**`); until then each is independent (different directories, no file
  overlap).
- Pass bar (proposal, to be set after a first run, THEORETICAL): at least 8 of 10 per profile,
  and no profile more than 1 question below `flat`.

## 6. Queued work (phase P-mcp-practices)

Ids are in the queue; every task carries tags `feature,ddflow-gap`, declared small globs, and
`needs` on the tool-diet tasks that touch the same files.

| id | what | needs |
|---|---|---|
| MCPP-eval-fixture | deterministic fixture project | |
| MCPP-eval-questions | 10 qa_pairs + gold derivation | MCPP-eval-fixture |
| MCPP-eval-oracle | scripted no-LLM check in the suite | MCPP-eval-questions |
| MCPP-eval-harness | opt-in LLM runner + report | MCPP-eval-oracle |
| MCPP-effect-class | one read/additive/destructive/idempotent table for every command | MTD-family-spec |
| MCPP-annotations | `annotations` (and `title`) in tools/list from the table | MCPP-effect-class, MTD-profiles |
| MCPP-input-schema | turn `schema_enum` on where it pays (hot tools first, byte-budgeted), add min/max/default | MTD-family-spec |
| fix-B-mcp-required-falsy | fix B1 (regression test required) | MTD-next-hints |
| fix-B-mcp-no-type-enum-check | fix B2: type/enum/range refusals quoting the parameter's description | MCPP-input-schema, fix-B-mcp-required-falsy |
| MCPP-result-bounds | every list-returning tool bounded; size ceilings per tool on the fixture | MCPP-eval-fixture |
| MCPP-selfcheck | stdio self-check script + test; documents an Inspector recipe | MCPP-annotations |
| MCPP-examples | examples in hot tools, byte-budgeted, kept only if the eval improves | MCPP-eval-harness, MCPP-input-schema, fix-B-mcp-no-type-enum-check |

Suggestion to the P-tool-diet owner (not applied here): MTD-permissions should consume the
`MCPP-effect-class` table rather than define its own read/destructive list.
