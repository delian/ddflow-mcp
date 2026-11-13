# Shell completion

`ddflow completion` activates, installs and uninstalls TAB completion for **bash, zsh and
fish**. A TAB completes the command names, their options and closed `choices`, and the value
kinds each argument declares or infers — record ids, phases, gate names, states, agent names
and paths — read from the live registries and the argparse parser, never a hand list, so a
new command, option, choice, record kind or harness completes with no completion code.

## Activating

`ddflow completion <shell>` prints the activation text for that shell. Put ONE line in your
shell startup file, after the shell's completion system is loaded:

- zsh (`~/.zshrc`, after `autoload -Uz compinit && compinit`): `eval "$(ddflow completion zsh)"`
- bash (`~/.bashrc`): `eval "$(ddflow completion bash)"`
- fish (`~/.config/fish/config.fish`): `ddflow completion fish | source`

The output is a single comment-free line, so in bash and zsh each of `eval "$(...)"`, the
unquoted `eval $(...)` and `source <(...)` registers it (fish has no `<(...)`: use
`| source`; its `eval (...)` works too) (an unquoted `eval` joins output lines with
spaces, which would turn a leading `#` comment into the whole script). zsh needs
`compdef`: when the completion system is not loaded yet the line runs `compinit -C`
(cached dump, no security scan) itself, once; load `compinit` first in `.zshrc` to keep the
dump your own. The registration code is argcomplete's, so no command or option name is
written in the activation text.

## Installing and uninstalling

`ddflow completion install [<shell>]` appends ONE marker-guarded block to the shell's
startup file; `ddflow completion uninstall [<shell>]` removes only that block. `<shell>`
defaults to `bash`. Neither touches a line it did not write: install is append-only and
idempotent, and uninstall drops the `# >>> ddflow completion >>>` … `# <<< ddflow completion <<<`
span, leaving everything else (a dangling begin marker drops only that line).

Both write with consent, never silently: the bare command prints what it *would* change —
`would append one guarded block to <path>` / `would uninstall ...` — and `--yes` applies
it. The edited file is the one the shell actually reads, so zsh honours `ZDOTDIR` and fish
honours `XDG_CONFIG_HOME` before the default `~/.zshrc` / `~/.config/fish/config.fish`.
Installation is also *offered* at `onboard` and `adopt`, so a project already running
ddflow is asked rather than handed a manual step to forget.

`ddflow doctor` reports a registration whose engine does not answer.

## What completes

Commands, options and closed `choices` come from the real argparse parser — the same one a
normal `ddflow` run builds — and record ids, phases, gate names, states, agent names and
paths come from each argument's declared or inferred value `kind`. The kinds are
`none` (free text; must NOT complete), `path`, `dir`, `agent`, `gate`, `state`, `kind`,
`id` (ids of any kind) and `id:<kind>` (ids of one record kind). Inference resolves the
same string from the argument itself, so a parameter named after a record kind, a closed
value set or a path gains its completer from its declaration. The three activation scripts
carry no command, option or choice name.

## The stdlib-only decision

Completing needs no required third-party dependency: the candidate sources are the Python
standard library and the argparse parser. **argcomplete is an OPTIONAL extra**
(D-completion-scope): install the `argcomplete` extra to turn the activation line on.
Without it there is no TAB completion and `ddflow` runs normally; a plain `ddflow`
invocation is unchanged. Stdlib-generated per-shell scripts — which a per-TAB `ddflow`
start would have made necessary — were considered and rejected for the argcomplete extra.

## How a TAB is answered

The registration runs `ddflow` with `_ARGCOMPLETE` set (argcomplete's protocol: the
candidates are written to fd 8). `ddflow/surfaces/__init__.py` answers that request before
the CLI is imported or run, using the real argparse parser: commands, options and closed
`choices` come from it, and record ids, gate names, states, agent names and paths come from
`services/completion/values.py` through completers attached from each argument's declared or
inferred value kind (`services/completion/kinds.py`).

### The latency budget

Each TAB through argcomplete starts `ddflow` once (about a second) unless the hook
short-circuits before importing `ddflow.api`. Record ids are read straight from the newest
read-only `.ddflow/index.db-*` sqlite file over that import-light path, so an id completes
in under **100 ms**; with no index present the id completer completes nothing rather than
building one on demand. The tests hold both ceilings.
