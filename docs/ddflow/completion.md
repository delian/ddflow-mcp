# Shell completion

Put ONE line in your shell startup file, after the shell's completion system is loaded:

- zsh (`~/.zshrc`, after `autoload -Uz compinit && compinit`): `eval "$(ddflow completion zsh)"`
- bash (`~/.bashrc`): `eval "$(ddflow completion bash)"`
- fish (`~/.config/fish/config.fish`): `ddflow completion fish | source`

`ddflow completion install` writes a guarded block with the equivalent line.

The output is a single comment-free line, so in bash and zsh each of `eval "$(...)"`, the
unquoted `eval $(...)` and `source <(...)` registers it (fish has no `<(...)`: use
`| source`; its `eval (...)` works too) (an unquoted `eval` joins output lines with
spaces, which would turn a leading `#` comment into the whole script). zsh needs
`compdef`: when the completion system is not loaded yet the line runs `compinit -C`
(cached dump, no security scan) itself, once; load `compinit` first in `.zshrc` to keep the
dump your own. The registration code is argcomplete's, so no command or option name is
written in the activation text.

`ddflow doctor` reports a registration whose engine does not answer.

## How a TAB is answered

The registration runs `ddflow` with `_ARGCOMPLETE` set (argcomplete's protocol: the
candidates are written to fd 8). `ddflow/surfaces/__init__.py` answers that request before
the CLI is imported or run, using the real argparse parser: commands, options and closed
`choices` come from it, and record ids, gate names, states, agent names and paths come from
`services/completion/values.py` through completers attached from each argument's declared or
inferred value kind (`services/completion/kinds.py`). Without argcomplete installed nothing
answers and `ddflow` runs normally. A TAB costs one process start plus the parser build
(about a second); the tests hold a ceiling.
