# SW-live-smoke: the first lanes run against a live model

Task SW-live-smoke, decision D-swarm-pilot-2026-11-05, run 2026-11-05. This is the go/no-go evidence for the pilot.

**Result: go for SW-pilot-toy, with the conditions below.** A real kilo lane on a keyless LAN model claimed, edited, committed,
renewed its lease, recorded a gate and stopped at its lease. Tool calls are reliable on both models tried. Nothing here shows that a
lane can do real work: every run was a toy item in a throwaway repo.

## How it was run

- kilo 7.8.18, driving two keyless LAN DeepSeek endpoints: the flash model and the pro model. Addresses stay in the machine's local layer.
- Each run used a new throwaway repo under the scratchpad. This repo's main was not touched, and no paid or keyed endpoint was used.
- **The harness was a test harness, not a product route.** Nothing can start a lane yet (B5c8139e564), so it called `lanes.launch.launch()` directly.
- **The two person approvals were stubbed in the harness's memory.** No command can grant a lane-template approval (B8647b69e06). The harness added both rows, template and plugin, to its folded state in its own process. No approval event was written to any log, and no production code changed. The authority for the run is the operator's consent in D-swarm-pilot-2026-11-05.
- **`tool_call` was asserted as verified in the sandbox's local catalog before any evidence existed**, because `launch()` refuses anything else (Bc64f9048f5).
- Conformance runs did not go through `launch()`. `conformance.run` starts the lane itself, so they used `opencode.prepare`'s spec.
- The harness teed kilo's raw stdout to a file, which is what the stream reading below rests on.
- Sandbox config raised `conformance_idle_s` to 120 and `conformance_lease_wait_s` to 90, except run 11.

## The runs

| Run | Model | Driven by | Item | Result |
|---|---|---|---|---|
| 1 | flash | launch | toy, lane given its own branch | every step done, after a detour: see B7d091d0f35 |
| 2 | flash | launch | toy, the item's own tree | every step clean, 30 s, 5 calls |
| 3, 4, 5 | flash | conformance | toy plus a work loop | level 6 of 7 |
| 6, 7 | pro | conformance | toy plus a work loop | level 6 of 7 |
| 8 | flash | launch | guard test | both guards held |
| 9 | pro | launch | guard test | both guards held |
| 10 | flash | launch | a served model id that does not exist | failed honestly: exit 1, `error` event with the server's message |
| 11 | flash | conformance | shipped defaults | level 0: see B72bcd17b4e |

`smoke.judge` checks the six steps of the toy item. Runs 8 and 9 ran a different script with no heartbeat step, so its `failed` for that step there says nothing about the lane, and those two runs are not counted as judged.

## Each step

| Step | Evidence | Runs |
|---|---|---|
| Claim | `lease.acquired` under the lane's own identity in the sandbox log | 1 to 9 |
| Edit | `write` calls went through inside the lease | 1 to 9 |
| Commit | commit on the item's branch; the identity fix of B6a483bb0f5 held under the private HOME | 1 to 9 |
| Heartbeat | ddflow answered `renewed: true` | 1 to 7 |
| Gate record | `gate.passed` by the lane, with the commit sha as evidence | 1 to 9 |
| Stop | exited 0 by itself; no merge, no completion | 1 to 9 |
| Stop at the lease | 8.5 to 14.1 s from revocation to end, no later write | 3 to 7 |

A heartbeat soon after a claim writes no event: renewals are coalesced by design, and the one `lease.renewed` in such a log is the
claim's own. So `smoke.judge` reads the heartbeat from ddflow's answer in the stream, not from the log.

## Guards: `prevent` observed live

The pre-tool plugin ran inside kilo and blocked real calls. This is the first time it was seen outside a stub.

- A write to an unleased path in the worktree was refused: `other.txt is outside the item's lease globs` (runs 8, 9).
- A write after the lease was revoked was refused: `holds no live lease: stop; do not write any file` (runs 3 to 7). Each model then stopped without trying another route, but the item told it to.
- A shell write is not prevented, as designed. `git commit` of a file written with `echo` was refused by the lane's pre-commit hook (runs 8, 9). That layer is detection only, and `--no-verify` was not tried.

## Tool calls

86 calls across runs 1 to 9, 85 well formed. Flash: 61 calls, 1 bad. Pro: 25 calls, 0 bad.

The one bad call was `ddflow_memory action=similar` with `sources`, an argument the family tool's schema offers and that action
refuses (B59a07c283d). The model corrected it on the next turn. No call was lost or garbled, no stream line was unreadable, and no
tool call appeared as plain text.

`tool_call_reliable` exists only in the task text. The field that holds this is the route's `tool_call` catalog fact, and no route is
committed. On this evidence `smoke.tool_call_fact` gives `v = true, s = "verified"` for both routes (20 calls and a 0.9 rate required,
the 20 being this smoke's choice). The operator's `.ddflow/local/models.toml` does not exist yet, so nothing was written.

These are short, scripted runs of 5 to 14 calls. They say nothing about long sessions.

## The stream, read at last

`run --format json` on kilo 7.8.18 matches `STREAM_RULES` field for field:

- `step_start` with `sessionID`. It comes once per model turn, not once per run, so `started` repeats.
- `tool_use` with `part.tool`, `part.state.input.action`, `part.state.error`, plus `part.state.status`, `.input` and `.output`.
- `error` with `error.data.message`, read on a 404 (run 10). The same line carries the endpoint URL in `metadata.url` and the response body: the rules drop them, and a new rule must keep them out.
- `step_finish` carries `part.tokens` and `part.cost`, so `usage` can be advertised. `text` carries the model's prose. Neither is mapped.
- Tool names arrive as `ddflow_ddflow_claim`, which `conformance.norm_tool` handles.
- Calls sent in one model turn appear together, so a call right after a refused one may already have been in flight.

Measured: about 20 s from start to the first line, 30 to 68 s for a whole run, and about 27,000 to 31,000 tokens in every model turn.

## Conditions on go

1. **B7d091d0f35 (high).** The lane's worktree must be the item's own tree and branch. SW-lane-run must pass that, or `claim` must adopt the lane's tree. Otherwise the hook refuses the tree `claim` named.
2. **B72bcd17b4e (high).** Conformance defaults grade every live lane level 0. Until that is fixed, set both knobs by hand.
3. **B09100a25b3, B6eaa5f27c2 (medium).** Level 7 cannot be reached: `write` and `bash` count as malformed, and a refused ddflow call counts as success. Do not admit a lane on level 7, and do not read levels 2 to 4 as proof without the log.
4. **B8478a28c60 (medium).** On one clone, any agent can release a peer's lease. It served as this smoke's revocation; it should not survive a real swarm.
5. Already filed and unchanged: B5c8139e564 (no route starts a lane), B8647b69e06 (no template approval), Ba655b17982 (`ddflow lanes smoke` does not exist).

## What this task did not deliver

`ddflow lanes smoke` is not provided. The verb needs `surfaces/declared/lanes.py`, outside this item's globs, and it would have to start
a lane, which is launcher work for SW-lane-run. `services/lanes/smoke.py` is the judgement such a route needs, and B-ac-smoke's planned
`agent_smoke` remains the one runner. Run counts are 3 and 2 per model on the ladder, so B-ac-smoke's majority of 3 is met only on flash.
