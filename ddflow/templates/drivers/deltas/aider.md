# Delta: Aider

The driver is `templates/drivers/implement-phase.md`. **Read it and follow it in full.**
Only the items below differ.

1. **No MCP.** Aider has no MCP client support, so ddflow is driven from the **shell**
   here: `ddflow brief`, `ddflow next`, `ddflow claim`, `ddflow gate run`,
   `ddflow complete`. That is the complete surface — the CLI is primary and every MCP
   tool maps onto it.
2. **Iteration is operator-driven.** Run one task per Aider session. Because Aider edits
   and commits directly, claim the item BEFORE you start it: `ddflow claim <id>`.
3. **Subagents.** None. Parallelism means a second Aider process on a different item; the
   lease keeps them apart.
4. **Asking the operator.** Stop and ask in the chat.
5. **Where the rules live.** Aider has no `AGENTS.md` convention. It loads a read-only
   context file you name yourself:

   ```yaml
   # .aider.conf.yml
   read:
     - AGENTS.md
   ```

   **`ddflow adopt` adds that entry for you**, merging into any `read:` list already there
   rather than replacing it. **Without it Aider never sees the queue rules at all** — it
   discovers no instruction file, so this is the one agent where doing nothing is silent
   total failure rather than degraded behaviour. `ddflow doctor` reports a
   `.aider.conf.yml` that has stopped listing it.

## MCP registration

None — there is nothing to register. The commit hook `ddflow adopt` installs still works,
which is what enforces "claim before you edit" regardless of the agent.
