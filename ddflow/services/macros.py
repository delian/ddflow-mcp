"""`[[macro]]` — operator-defined prompt macros: a named mode, invoked like a command.

"Enter debugger mode." A macro is a titled prompt with declared parameters and a declared
set of tools the mode expects, defined in `.ddflow/config.toml` and surfaced everywhere the
shipped workflow commands are: `prompts/list` and `prompts/get` over MCP, `ddflow prompts
list|show` in a terminal.

**Why config and not a template file.** `services/prompts.py` already resolves templates
with project-override precedence, and a project can already rewrite any shipped command by
dropping a file in `.ddflow/prompts/commands/`. What it could NOT do was add a NEW one:
`COMMANDS` is a Python dict, so a mode that is specific to one project — "enter
release-audit mode", "enter incident mode" — needed a code change in ddflow. That is the
gap, and it is the whole gap.

**What is deliberately NOT copied from `dx-zero/mcpn`** (the source of this idea): their
`toolMode: situational`, where the model freely picks which tool to call from a bound set
with no recorded ordering or rationale. That reintroduces exactly the non-reproducibility
the event log exists to remove — a session you cannot replay is a session you cannot
review. So `tools` here is DECLARATIVE: it is rendered into the prompt as the ordered set
the mode expects, and it is not a permission boundary. MCP has no mechanism to make it one,
and implying otherwise would be a security claim this cannot honour.

Three refusals, each because the alternative fails quietly:

* **An undeclared parameter is an error, not an empty string.** A macro rendered with a
  hole in it reads as a complete instruction.
* **A macro may not take the name of a shipped command.** Silent shadowing is the class
  that had `api.review` bind a function over its own submodule.
* **`prompt` and `prompt_file` are mutually exclusive.** Two sources for one body means one
  of them is dead and looks live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Macro:
    """One operator-defined mode.

    Fields are the dataclass `tomlcfg.overlay_array` validates against, so an unknown key
    in a `[[macro]]` block is refused by name rather than ignored.
    """

    name: str
    title: str = ""
    description: str = ""
    #: Parameter names the prompt may use as `{{ name }}`. Declared so `prompts/get` can
    #: advertise them to a client AND refuse a call that omits one.
    params: list[str] = field(default_factory=list)
    #: The tools this mode expects, IN ORDER. Rendered into the prompt; never enforced.
    tools: list[str] = field(default_factory=list)
    #: The body, inline. Mutually exclusive with `prompt_file`.
    prompt: str = ""
    #: The body, from a file relative to the repo root. For a mode long enough that TOML
    #: quoting gets in the way, which is most of the useful ones.
    prompt_file: str = ""

    def body(self, root: Path) -> str:
        if self.prompt and self.prompt_file:
            raise MacroError(
                f"macro {self.name!r} sets both `prompt` and `prompt_file`. Pick one — two "
                f"sources for one body means one of them is dead and looks live."
            )
        if self.prompt_file:
            path = Path(self.prompt_file)
            if not path.is_absolute():
                path = Path(root) / path
            if not path.is_file():
                raise MacroError(
                    f"macro {self.name!r}: prompt_file {path} does not exist. A configured "
                    f"body that is missing is an error, never a silent fallback to empty."
                )
            return path.read_text("utf-8")
        if not self.prompt.strip():
            raise MacroError(
                f"macro {self.name!r} has no `prompt` and no `prompt_file`. A named mode "
                f"with no instruction in it is a slash command that does nothing."
            )
        return self.prompt


class MacroError(RuntimeError):
    pass


def load_macros(root: Path) -> dict[str, Macro]:
    """Read `[[macro]]` blocks from `.ddflow/config.toml`, then `.ddflow/macros.toml`.

    Same two-file precedence as reviewers and companions: one obvious place to configure,
    plus a dedicated file for operators who prefer to split it out.
    """
    from ..infra import tomlcfg
    from .prompts import COMMANDS

    blocks = tomlcfg.overlay_array(
        tomlcfg.config_paths(root, "macros.toml"), "macro", Macro, key="name"
    )
    # REFUSED at the source, not filtered out downstream. A macro named `bug-hunt` that
    # silently loses to the shipped one leaves the operator editing a block that does
    # nothing, with every surface reporting the shipped description back at them — the
    # shadowing class that had `api.review` bind a function over its own submodule.
    clash = sorted(set(blocks) & set(COMMANDS))
    if clash:
        raise MacroError(
            f"macro(s) {clash} take the name of a shipped workflow command, which would "
            f"silently lose to it. Rename them, or override the shipped one instead by "
            f"writing .ddflow/prompts/commands/{clash[0]}.md."
        )
    return {name: Macro(**spec) for name, spec in sorted(blocks.items())}


#: Rendered above the operator's own text, so the agent is told the bounded set and the
#: recording duty WITHOUT the macro author having to remember to say it. Kept short: a
#: preamble longer than the instruction is a preamble that gets skipped.
_TOOL_PREAMBLE = """\
This mode expects these ddflow tools, in this order:

{steps}

Use them in that order and record what each one said. If you need one that is not listed,
say so before using it — the point of a named mode is that the next reader can tell what
it was supposed to do.

---

"""


def render(macro: Macro, root: Path, params: dict[str, str]) -> str:
    """The macro's prompt, with its parameters substituted.

    Refuses on a missing parameter. The renderer is the same one every other template uses,
    so `{% if %}` and `{% for %}` work here too and a project that installs Jinja2 gets the
    full language.
    """
    from . import prompts as P

    missing = [p for p in macro.params if not str(params.get(p, "")).strip()]
    if missing:
        raise MacroError(
            f"macro {macro.name!r} needs {', '.join(missing)}. A prompt rendered with a "
            f"hole in it reads as a complete instruction, which is worse than a refusal."
        )
    text = macro.body(root)
    if macro.tools:
        steps = "\n".join(f"  {i}. `{t}`" for i, t in enumerate(macro.tools, 1))
        text = _TOOL_PREAMBLE.format(steps=steps) + text
    # Declared parameters first, then whatever else the caller sent, merged into ONE dict:
    # `**a, **b` with a key in both is a TypeError, and every declared param is in both.
    # Extras are passed through rather than refused — a client may send more than the macro
    # declares, and the template simply will not reference them.
    variables = dict.fromkeys(macro.params, "")
    variables.update(params)
    return P.render(text, **variables)
