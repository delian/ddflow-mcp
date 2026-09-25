# Delta: GitHub Copilot (coding agent / CLI)

The driver is `templates/drivers/implement-phase.md`. Read and follow it in full.

1. **Iteration.** Copilot's coding agent works one issue/PR at a time. Map one ddflow
   task to one PR. Put `ddflow claim <ID>` in the setup step and
   `ddflow complete <ID>` in the finish step.
2. **Subagents.** None; parallelism comes from multiple concurrent agent runs, which
   ddflow's leases already coordinate.
3. **Asking the operator.** Comment on the PR and stop.
4. **File references.** Plain paths.
5. **Instructions file.** `.github/copilot-instructions.md` — point it at the canonical
   driver. Copilot also reads `AGENTS.md`.
6. **Worktrees.** Copilot's agent already runs in an ephemeral clone. Set
   `worktree.enabled = false` and rely on the lease alone for coordination; the isolation
   the worktree would have provided is already there.
