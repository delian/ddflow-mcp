# Shell completion

Put ONE line in your shell startup file, after the shell's completion system is loaded:

- zsh (`~/.zshrc`, after `autoload -Uz compinit && compinit`): `eval "$(ddflow completion zsh)"`
- bash (`~/.bashrc`): `eval "$(ddflow completion bash)"`
- fish (`~/.config/fish/config.fish`): `ddflow completion fish | source`

`ddflow completion install` writes a guarded block with the equivalent line.

The output is a single comment-free line, so each of `eval "$(...)"`, the unquoted
`eval $(...)` and `source <(...)` registers it (an unquoted `eval` joins output lines with
spaces, which would turn a leading `#` comment into the whole script). zsh needs
`compdef`: when the completion system is not loaded yet the line runs `compinit -C`
(cached dump, no security scan) itself, once; load `compinit` first in `.zshrc` to keep the
dump your own. The registration code is argcomplete's, so no command or option name is
written in the activation text.

`ddflow doctor` reports a registration whose engine does not answer.
