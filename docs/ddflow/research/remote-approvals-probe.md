# Remote approvals: what the harness can attest (RA-probe-harness)

Task `RA-probe-harness` (phase `P-remote-approvals`), 2026-10-22. Decisions it feeds: `D-RA-Q5` (transcript-attested
`AskUserQuestion` as level L2), `RA-channel-harness`. Evidence labels: **CONFIRMED** = read or run on this host today;
**UNAVAILABLE** = could not be run from where this agent runs, and why; nothing here is "passed" by assumption.

Environment probed: Claude Code 2.1.296, `--print --sdk-url https://api.anthropic.com/v1/code/sessions/cse_...`
(`CLAUDE_CODE_ENTRYPOINT=sdk-cli`, `CLAUDE_CODE_ENVIRONMENT_KIND=bridge`), i.e. a session the operator drives from the
Claude app while the CLI process, its tools and its files live on this host. The agent is a subagent of that session.

## Findings

1. **CONFIRMED: the transcript is a local file ddflow can read.** `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`
   holds every row of the session (5.8k rows here). A harness hook payload carries its path as `transcript_path`
   (`ddflow/services/compaction.py` already reads it from the PreCompact payload).
2. **CONFIRMED: an `AskUserQuestion` answer is written by the harness, not the model.** For each of the 24
   `AskUserQuestion` calls in this session the transcript holds an assistant row with the `tool_use` and a later `user`
   row with a `tool_result` for its id, `sourceToolAssistantUUID` equal to the assistant row's uuid,
   `permissionDecision = {"decision": "accept", "source": "user_temporary"}`, and `toolUseResult.answers`, a map from the
   exact question text to the chosen label (or typed text; a list of labels for a multi-select question; a question the
   person skipped is absent). The model sees the same answer only as the tool result string.
   `probes/approvals/transcript_attest.py` extracts and checks these: 24 of 24 `row_ok` on this transcript (`row_ok`: accepted, row ids line up). A caller never trusts the row alone: `answer_for(path, question)` returns
   the answer to exactly the question ddflow rendered, and only when that question has a non-empty answer.
3. **CONFIRMED: typed human prompts carry their origin.** `user` rows for what the operator typed have
   `origin.kind = "human"`, `promptSource = "sdk"`, `turnOrigin = "human"`; background notices have
   `origin.kind = "task-notification"`. A relayed message from another agent session is a `human`-origin row too in
   this bridge setup (the operator's coordinator session is itself an agent), so `origin` does NOT separate "the
   operator" from "a coordinating agent". An approval must therefore be given as an answer to ddflow's own rendered
   question, never inferred from prompt text.
4. **CONFIRMED: the row says nothing about WHO sat at the client.** `source: user_temporary` means the client UI
   accepted the permission for this call. Whoever drives the Claude app (the operator, or a script holding the
   session) produces it.
5. **CONFIRMED limit: the file is writable by the agent.** It is owned by the agent's uid and not append-only. A shell
   command can append rows of exactly this shape, so transcript attestation is **visible forgery, not impossible**
   (the same class as L1; research section 5). It stops accident and a model that merely *claims* an answer in text; it does
   not stop an agent that edits its own transcript, which includes an agent following an injected instruction to do so.
6. **CONFIRMED: this session runs with `permissionMode: bypassPermissions`.** Tool permission prompts are skipped, so a
   PreToolUse hook that returns `ask` cannot be relied on to prompt in such sessions; `AskUserQuestion` is a question
   tool, not a permission prompt, and is still put to the person.

## Verdict for the harness channel

* **Weakest honest option that works today (L2, "attested by transcript"):** ddflow renders the question (the same
  text `request.render` builds, digest included) and tells the agent to put exactly that text in `AskUserQuestion`;
  `ddflow approvals ...` then finds, in the session transcript named by the hook payload or the newest transcript of
  this session id, an attested answer whose question text equals the rendered text and whose answer is the approve
  label, and records the ask answered with `via=harness`, `level=2`, the transcript row uuid as proof. Accepted only
  for tier T2, never T3. An agent-typed claim of an answer is refused: only a row with the five properties in
  finding 2 counts, and the question text must equal ddflow's rendering byte for byte.
* **Not verifiable:** that a person, not a script holding the session, answered; that the file was not edited. Doctor
  says so.
* **Cloud sandbox sessions (the process runs in Anthropic's sandbox):** **UNAVAILABLE**: no such session was reachable
  from this host, so whether the transcript exists there, where, and in which shape is unconfirmed. Until someone runs
  the script in one, the harness channel is inert there (D-RA-Q5 already says so).

## Unavailable (not run; reasons)

* **MCP elicitation** (`elicitation/create` from the ddflow server during a tool call, answered by the client UI
  without the model): UNAVAILABLE. This agent cannot make the ddflow MCP server issue a request, and the running
  server (started before this phase) has no elicitation code; whether Claude Code renders it and withholds the
  response from the model is unverified. `RA-channel-harness` does NOT implement elicitation until it is confirmed.
* **PreToolUse hook returning `ask` over an allow-listed command** and **PostToolUse payload for AskUserQuestion**
  (`tool_response`, `transcript_path`): UNAVAILABLE. Hooks are read when a session starts; adding a probe hook to this
  running session's settings would not take effect and editing harness settings is not this task's to do. The
  transcript row (finding 2) carries the same information a PostToolUse hook would.
* **A different client** (Cursor, Codex, a web chat): UNAVAILABLE.

## Reproduce

```
python probes/approvals/transcript_attest.py --selftest
python probes/approvals/transcript_attest.py ~/.claude/projects/<slug>/<session-id>.jsonl
```
