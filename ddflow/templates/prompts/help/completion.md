# Shell completion

`ddflow completion` turns on TAB completion for commands, options, closed `choices`, and
the value kinds each argument declares or infers: record ids, phases, gate names, states,
agent names and paths. It is a person's terminal command — there is no MCP tool for it.

## Turning it on and off

`ddflow completion <shell>` prints the activation text for `bash`, `zsh` or `fish`; one
line in your shell startup file registers it. `ddflow completion install` appends that
line as ONE marker-guarded block to the shell's startup file, and `ddflow completion
uninstall` removes only that block (the default shell is `bash`). Both write with
consent, never silently: the bare command prints what it *would* change, and `--yes`
applies it. Installation is also offered at `onboard` and `adopt`. The exact one-line
spelling per shell, and what to do about `ZDOTDIR` / `XDG_CONFIG_HOME`, is in
`docs/ddflow/completion.md`.

## What completes

Commands, options and closed `choices` come from the argparse parser; values come from
each argument's declared or inferred `kind`. A new command, option, choice, record kind
or harness completes with no completion code; an argument that must not complete is an
explicit exemption, and the three activation scripts carry no command, option or choice
name.

## The stdlib-only decision

Completing needs no required third-party dependency. argcomplete is an OPTIONAL extra
(D-completion-scope): without it there is no TAB completion and `ddflow` runs normally.
A TAB through argcomplete starts `ddflow` once (~1 s); record ids are read from the
newest read-only `.ddflow/index.db-*` sqlite file in under 100 ms.
