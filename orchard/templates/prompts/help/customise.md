# Changing the workflow for this project

    orchard workflow                          the rules in force here, and where each came from
    orchard workflow pipeline task a,b,c      set the gates every task passes
    orchard workflow gate NAME --command ...  define or change one gate
    orchard workflow drop NAME                take a gate out of the pipelines

All of it reaches MCP: `orchard_workflow`, `orchard_workflow_pipeline`,
`orchard_workflow_gate`, `orchard_workflow_drop`. An agent can change the workflow
**with the operator's agreement** — ask first, and use `dry_run` to show them what it
would do. A gate in a pipeline is a check somebody added on purpose.

## Adding your own gate

```console
$ orchard workflow gate lint --command "ruff check ." --into task --after implement --required
configured gate lint: gate.lint.command, gates.task_pipeline, gates.required
```

`--command` makes it a **command gate**: Orchard runs it and the exit code is the
evidence. `--prompt` makes it an **agent gate**: you perform it and record what you did.
A gate needs one of the two, and one with neither is refused — it would tell an agent
nothing and give a reviewer no contract.

## Nothing is written until it is checked

Every write goes through the same path: compose the change, validate the RESULT, then
replace the file atomically.

- **A pipeline naming an undefined gate is refused**, and the error names the near miss.
  That one is silent and permanent otherwise: the outcome folds to empty, completion
  refuses it forever, and `gate record` rejects the id as unknown, so the item can never
  be completed at all.
- **An unknown section or knob is refused**, with a suggestion. `[gatez]` is valid TOML
  and would have been written happily.
- **Dropping a gate takes it out of `required` too**, or it becomes a requirement that
  quietly requires nothing.

`orchard workflow` re-runs those checks against what is on disk and exits 1 if anything
does not hang together. So does `orchard doctor`.

## Editing the files directly

Everything above writes `.orchard/config.toml`, and you can edit it by hand instead.
Gates live in `[gate.<id>]` there or in `.orchard/gates.toml`; reviewers in
`[[reviewer]]`; companions in `.orchard/companions.toml`. Run `orchard workflow`
afterwards to check it.

The prompts are files too, including the ones an agent is handed at every gate:
`orchard prompts eject gate_instruction` copies the shipped default into
`.orchard/prompts/`, where it wins. Same for `mcp_instructions`, which is the text your
agent receives the moment it connects.

**One caveat with MCP.** The connection instructions are computed once, when the server
starts. A workflow changed mid-session is live for every tool call immediately, but the
text your agent was handed at connect is stale — tell it to call `orchard_workflow`, or
restart the server.
