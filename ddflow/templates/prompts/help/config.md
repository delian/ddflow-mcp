# Configuration

    ddflow config --explain               every knob, its value, where it came from
    ddflow config --explain --filter lease   just one section
    ddflow config --append-toml '...'     add config without a shell editor

Resolution, lowest to highest: dataclass defaults → `.ddflow/config.toml` →
`DDFLOW_<SECTION>_<KNOB>` in the environment. Every knob carries its documentation in
place, and a test asserts it, so the reference cannot rot away from the code.

**An unknown section or knob is an ERROR, never a silent drop.** `[leases]` for `[lease]`
used to leave every knob at its default while the operator believed the file was in
effect — which is the silent-knob-drop class, in the module written to prevent it.

Gates, reviewers and companion servers live in their own tables and their own files:

    .ddflow/gates.toml       [gate.<name>]  command, cwd, mutations
    .ddflow/config.toml      [[reviewer]]   a model from a different family
    .ddflow/companions.toml  [[companion]]  MCP servers that serve the gates

## Prompts are configuration too

Every prompt this tool sends to a model, and every help page you are reading, is a
template on disk. `ddflow prompts list` shows where each resolves from;
`ddflow prompts eject <name>` copies the shipped default into `.ddflow/prompts/`,
where it takes precedence. Rewrite the workflow without touching code.
