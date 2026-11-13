# Configuration

    ddflow config --explain               every knob, its value, where it came from
    ddflow config --explain --filter lease   just one section
    ddflow config --append-toml '...'     add config without a shell editor
    ddflow config defaults                the packaged default.toml, every default in one file

Resolution, lowest to highest: the packaged `default.toml` → `.ddflow/config.toml` →
`.ddflow/local/config.toml` → `DDFLOW_<SECTION>_<KNOB>` in the environment. Every knob
carries its documentation in place, and a test asserts it, so the reference cannot rot away
from the code.

`default.toml` ships with ddflow and is generated from the section declarations: never edit
it, and set a project's value in `.ddflow/config.toml` instead. Working on ddflow itself,
`ddflow config defaults --write` (or `ddflow pins regenerate --only default-toml`) rewrites
it after a knob or a default gate changed; the `default-toml` pin fails until you do. Over
MCP, `ddflow_configure` with no arguments lists the same values with their meaning. A
damaged or missing file warns and leaves the built-in defaults in effect; a stale one warns
and its values apply.

**An unknown section or knob is an ERROR, never a silent drop.** `[leases]` for `[lease]`
used to leave every knob at its default while the operator believed the file was in
effect — which is the silent-knob-drop class, in the module written to prevent it.

Gates, reviewers and companion servers live in their own tables and their own files:

    .ddflow/gates.toml       [gate.<name>]  command, cwd, mutations
    .ddflow/config.toml      [[reviewer]]   a model from a different family
    .ddflow/companions.toml  [[companion]]  MCP servers that serve the gates

## Behind means behind in code

Most commits on the base touch only the event log (`.ddflow/`), so counting every commit made
every worktree "behind". A tree is behind in CODE when a commit it lacks touches a path outside
`[worktree].behind_ignore_globs` (default `.ddflow/`). `ddflow sync`, the merge queue's re-merge,
the commit gate's `max_behind` count and the session-start hint read that count: they report
`behind by N commit(s) (K touch code)`, and when K is 0 and no rulebook changed they do
nothing (no merge commit). The exception is a rulebook changed under an ignored path
(`.ddflow/rules/`): it is still drift (`stale_rules`), the commit gate blocks on it and `ddflow
sync` and the queue merge it, because the gate's remedy is that sync. An empty list counts every
commit, as before.

    ddflow config --set worktree.behind_ignore_globs '[".ddflow/", "docs/generated/"]'
    ddflow_configure set=worktree.behind_ignore_globs value='[".ddflow/"]'   (MCP; local=true for this machine)
    DDFLOW_WORKTREE_BEHIND_IGNORE_GLOBS='[".ddflow/"]'                       (environment)
    prompt: "do not count docs/generated/ as being behind main" -> the agent runs the first line.

## Prompts are configuration too

Every prompt this tool sends to a model, and every help page you are reading, is a
template on disk. `ddflow prompts list` shows where each resolves from;
`ddflow prompts eject <name>` copies the shipped default into `.ddflow/prompts/`,
where it takes precedence. Rewrite the workflow without touching code.
