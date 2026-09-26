"""External, overridable prompt templates — extend by writing text, not code.

Every string this system sends to a model, and every instruction it gives an agent,
lives in a template file rather than inline in Python. That is not tidiness: prompts are
**operator-tunable behaviour**. Inline them and improving a reviewer's instructions
becomes a code change, a release and a version bump; externalise them and it is an edit
to a text file in the operator's own repository.

Resolution order for template ``name``, first hit wins:

1. ``[prompts].<name>`` in ``.ddflow/config.toml`` — an explicit path;
2. ``<repo>/.ddflow/prompts/<name>.md`` — the conventional project override;
3. the shipped default in ``ddflow/templates/prompts/<name>.md``.

**Rendering is Jinja2 when Jinja2 is importable, and a strict stdlib renderer
otherwise.** ddflow takes no runtime dependencies — that is what lets it install inside
a sandbox with no package index — so Jinja cannot be required. But a project that
already has it gets loops, conditionals and filters for free, and the shipped templates
are written in the subset both renderers agree on: ``{{ var }}``, ``{% if %}`` /
``{% endif %}``, ``{% for %}`` / ``{% endfor %}``.

The stdlib renderer is deliberately **strict about unknown variables**: an undefined
name raises rather than rendering empty. A prompt silently missing the diff it was
supposed to carry is the vacuous-review failure in template form — the model dutifully
reviews nothing and reports no findings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Every template the system uses. Registered here so `ddflow prompts list` can show
#: them all, and so a typo in a template name fails loudly instead of falling back to a
#: default nobody notices.
TEMPLATE_NAMES: tuple[str, ...] = (
    "review_system",  # the reviewer's system prompt
    "review_user",  # the per-chunk user message
    "gate_instruction",  # what an agent is told to do for an agent gate
    "session_brief_header",
    # What the MCP client injects on connect: the workflow, the reporting duties and
    # the companion tools. Text, because it is the file you edit to change how this
    # project works rather than what the code does.
    "mcp_instructions",
)


class TemplateError(RuntimeError):
    pass


@dataclass
class Template:
    name: str
    text: str
    source: str  # "config" | "project" | "builtin"
    path: Path | None = None
    #: "template" (machinery: the review prompt, the gate instruction, the MCP
    #: handshake) or "command" (a workflow an operator invokes). Both are
    #: operator-editable text with the same override precedence, and they are used for
    #: entirely different things -- a flat list of eleven names invites
    #: `prompts show mcp_instructions` in the expectation of a workflow.
    kind: str = "template"


#: Workflow commands, surfaced over MCP as PROMPTS -- which is what a client turns into
#: a slash command. Tools are things an agent calls; prompts are things an operator
#: invokes, and these four are operator workflows, not primitives.
#: name -> (title, one-line description, [argument names])
COMMANDS: dict[str, tuple[str, str, list[str]]] = {
    "import-existing-project": (
        "Import an existing project's work into the queue",
        "For a project that adopts ddflow mid-stream. Reads its todo checklists, "
        "lessons, ADRs, research log, engineering journal, memory store and unmerged "
        "branches, then walks the agent through the half that needs judgement — globs, "
        "dependencies, what is actually live — WITH the operator. A queue that starts "
        "empty tells the next agent nothing is in flight. "
        "Safe and useful to re-run at any time: it starts by checking what is already "
        "imported and switches to finishing and refreshing it rather than repeating it.",
        ["scope"],
    ),
    "research-companions": (
        "Research MCP servers worth enabling for this project's stack",
        "The shipped registry covers the gates every project shares. It cannot know "
        "your stack. This walks the agent from the pipeline's UNCOVERED gates, through "
        "the repository's actual manifests, to candidate servers checked against their "
        "primary sources -- provenance, maintenance, what they execute and what "
        "credential they want -- and produces `[[companion]]` blocks the operator can "
        "read and delete. Proposes only; installs nothing. A decline is part of the "
        "report, so the next session does not re-research it.",
        ["scope"],
    ),
    "bug-hunt": (
        "Hunt and fix bugs",
        "Bounded bug hunt under the empirical-repro rule: a finding may not change "
        "source unless a runnable probe demonstrates it and ships as the regression "
        "test, mutation-verified.",
        ["scope"],
    ),
    "code-deduplication": (
        "Find and remove duplicated code",
        "Mechanical clone report first, then grep by OPERATION. Second occurrence: "
        "reuse or extract. Third: extraction is mandatory. Prefer eliminating a "
        "duplicate over guarding it twice.",
        ["scope"],
    ),
    "code-clean": (
        "Land everything in flight and leave the tree clean",
        "Classify every worktree and branch, deal with dirty trees by hand, land the "
        "unmerged work through its gates, remove what is finished, then bug-hunt, "
        "deduplicate and commit.",
        [],
    ),
    "all-tests": (
        "Run every suite and fix what fails",
        "Clean first, then run every configured suite (unit, integration, UI, e2e) in "
        "full, and fix failures under the bug-hunt rule. An unconfigured suite is "
        "absent, not passing.",
        [],
    ),
}


def builtin_dir() -> Path:
    from ..infra.paths import templates_dir

    return templates_dir() / "prompts"


def command_dir() -> Path:
    return builtin_dir() / "commands"


def macro_commands(repo: Path | None = None) -> dict[str, tuple[str, str, list[str]]]:
    """Operator-defined `[[macro]]` blocks, in the same shape as `COMMANDS`.

    So every surface that lists workflow commands lists macros too, without knowing which
    registry a name came from. A macro that had to be asked for separately is a macro an
    agent never finds.
    """
    if repo is None:
        return {}
    from .macros import MacroError, load_macros

    try:
        macros = load_macros(repo)
    except (MacroError, ValueError, OSError):
        # A malformed macro must not take `prompts/list` down with it: the shipped
        # commands are still there, and `ddflow prompts show <name>` reports the error
        # when the operator asks for that one. Losing the whole list to one bad block is
        # how a feature becomes something people switch off.
        return {}
    return {name: (m.title or name, m.description, list(m.params)) for name, m in macros.items()}


def all_commands(repo: Path | None = None) -> dict[str, tuple[str, str, list[str]]]:
    """Shipped workflow commands plus the project's macros.

    Shipped ones win a name collision, and `services.macros` refuses the collision at the
    source too — silent shadowing is the class that once had `api.review` bind a function
    over its own submodule.
    """
    return {**COMMANDS, **macro_commands(repo)}


def resolve_command(name: str, repo: Path | None = None) -> Template:
    """A workflow command template, project override first.

    Same precedence as any other template: ``.ddflow/prompts/commands/<name>.md``
    beats the shipped default, so a project can rewrite a whole workflow without
    touching code -- which is the point of shipping them as text.

    Falls through to `[[macro]]` blocks, so an operator-defined mode resolves by the same
    call every surface already makes.
    """
    from .macros import load_macros

    if name not in COMMANDS:
        macro = (load_macros(repo) if repo else {}).get(name)
        if macro is not None:
            # A macro's body is its own; there is no shipped default to fall back to, and
            # its `source` says `config` so `ddflow prompts list` shows where it came from.
            from .macros import MacroError

            try:
                return Template(name, macro.body(Path(repo)), "config", None, "command")
            except MacroError as exc:
                raise TemplateError(str(exc)) from exc
        known = sorted({*COMMANDS, *(load_macros(repo) if repo else {})})
        raise TemplateError(f"unknown command {name!r}. Known: {', '.join(known)}")
    if repo:
        local = Path(repo) / ".ddflow" / "prompts" / "commands" / f"{name}.md"
        if local.is_file():
            return Template(name, local.read_text("utf-8"), "project", local, "command")
    path = command_dir() / f"{name}.md"
    if not path.is_file():
        raise TemplateError(f"shipped command {name}.md is missing from the package")
    return Template(name, path.read_text("utf-8"), "builtin", path, "command")


def overrides_from(cfg) -> dict[str, str]:
    """`[prompts]` as the override map `resolve` takes.

    Was `_prompt_overrides(c)` in `surfaces/cli.py`, taking a whole `Ctx` to read one
    config section — so the api layer could not resolve a template with the operator's
    overrides honoured without reaching up into a surface. Three call sites, one of which
    is the gate instruction an agent is handed.
    """
    from dataclasses import fields

    return {
        f.name: getattr(cfg.prompts, f.name)
        for f in fields(cfg.prompts)
        if getattr(cfg.prompts, f.name)
    }


def resolve(
    name: str, repo: Path | None = None, overrides: dict[str, str] | None = None
) -> Template:
    if name not in TEMPLATE_NAMES:
        raise TemplateError(f"unknown template {name!r}. Known: {', '.join(TEMPLATE_NAMES)}")
    explicit = (overrides or {}).get(name, "")
    if explicit:
        path = Path(explicit)
        if not path.is_absolute() and repo:
            path = repo / path
        if not path.is_file():
            # A configured override that does not exist is an error, never a silent
            # fallback: the operator asked for THEIR prompt and would otherwise get
            # the default while believing their edit was live.
            raise TemplateError(f"[prompts].{name} points at {path}, which does not exist")
        return Template(name, path.read_text("utf-8"), "config", path)
    if repo:
        path = repo / ".ddflow" / "prompts" / f"{name}.md"
        if path.is_file():
            return Template(name, path.read_text("utf-8"), "project", path)
    path = builtin_dir() / f"{name}.md"
    if not path.is_file():
        raise TemplateError(f"shipped template {name}.md is missing from the package")
    return Template(name, path.read_text("utf-8"), "builtin", path)


def render(tmpl: Template | str, **vars: Any) -> str:
    text = tmpl.text if isinstance(tmpl, Template) else tmpl
    try:
        import jinja2
    except ImportError:
        return _render_stdlib(text, vars)
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
    )
    try:
        return env.from_string(text).render(**vars)
    except jinja2.UndefinedError as exc:
        raise TemplateError(f"template {getattr(tmpl, 'name', '?')}: {exc}") from exc


#: One pass over the source finds every construct. Ordered so a comment wins over the
#: tags that may appear inside it.
_TOKEN = re.compile(
    r"(?P<comment>\{#.*?#\})"
    r"|\{%\s*(?P<tag>.*?)\s*%\}"
    r"|\{\{\s*(?P<expr>.*?)\s*\}\}",
    re.S,
)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")

#: `for`, the loop variable, `in`, the sequence. Grammar, not a tunable: a
#: `{% for %}` with a different number of words is a template this engine cannot
#: read, and the remedy is Jinja2 rather than a knob.
_FOR_WORDS = 4


def _lookup(vars: dict[str, Any], dotted: str) -> Any:
    cur: Any = vars
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif hasattr(cur, part):
            cur = getattr(cur, part)
        else:
            raise TemplateError(
                f"template variable {dotted!r} is not defined. Available: {', '.join(sorted(vars))}"
            )
    return cur


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Source -> a flat token list, applying Jinja's `trim_blocks` + `lstrip_blocks`.

    Both are ON in the Jinja environment above, so the fallback has to honour them or
    the same template renders with different whitespace depending on which engine is
    installed -- which is the divergence this module exists to prevent, in its quietest
    form.
    """
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _TOKEN.finditer(text):
        chunk = text[pos : m.start()]
        pos = m.end()
        # A `{{ }}` is inline and takes no whitespace with it; a block tag or a comment
        # standing alone on a line takes the whole line.
        if m.group("expr") is None:
            cut = chunk.rfind("\n")
            if not chunk[cut + 1 :].strip():
                chunk = chunk[: cut + 1]  # lstrip_blocks; cut == -1 leaves ""
            if text[pos : pos + 1] == "\n":
                pos += 1  # trim_blocks
        if chunk:
            out.append(("text", chunk))
        if m.group("comment") is not None:
            continue
        out.append(
            ("block", m.group("tag")) if m.group("expr") is None else ("var", m.group("expr"))
        )
    if text[pos:]:
        out.append(("text", text[pos:]))
    return out


def _parse(tokens: list[tuple[str, str]]) -> list:
    """Tokens -> a tree, so a block is a SCOPE rather than a pair of markers.

    The previous implementation substituted innermost-first with `re.sub`, which has no
    notion of an enclosing block: it evaluated a `{% for %}` nested inside an
    `{% if adopted %}` that was false, over a name only the true branch defines, and
    took the whole handshake down for every unadopted repository. A tree cannot express
    that bug -- a branch not taken is never walked.
    """
    root: list = []
    stack: list[tuple[str, list, list]] = [("root", root, root)]
    for kind, val in tokens:
        body = stack[-1][2]
        if kind in ("text", "var"):
            body.append((kind, val))
            continue
        words = val.split()
        head = words[0] if words else ""
        if head == "if":
            node = ["if", val[len("if") :].strip(), [], None]
            body.append(node)
            stack.append(("if", node, node[2]))
        elif head == "else":
            if stack[-1][0] != "if":
                raise TemplateError("{% else %} outside an {% if %}")
            node = stack[-1][1]
            node[3] = []
            stack[-1] = ("if", node, node[3])
        elif head == "endif":
            if stack[-1][0] != "if":
                raise TemplateError("{% endif %} with no open {% if %}")
            stack.pop()
        elif head == "for":
            if len(words) != _FOR_WORDS or words[2] != "in":
                raise TemplateError(f"only `{{% for x in seq %}}` is supported, not {val!r}")
            node = ["for", words[1], words[3], []]
            body.append(node)
            stack.append(("for", node, node[3]))
        elif head == "endfor":
            if stack[-1][0] != "for":
                raise TemplateError("{% endfor %} with no open {% for %}")
            stack.pop()
        else:
            # LOUD, never silent. The regex engine left an unrecognised tag in the
            # output verbatim, so `{% if a or b %}` -- which its single-name pattern
            # could not match -- was emitted as literal template source into a model's
            # context, along with everything up to the orphaned `{% endif %}`.
            raise TemplateError(
                f"the fallback template engine does not support {{% {val} %}}. "
                f"Install Jinja2 (`pip install jinja2`) for the full language."
            )
    if len(stack) != 1:
        raise TemplateError(f"unclosed {{% {stack[-1][0]} %}} block")
    return root


def _truthy(cond: str, vars: dict[str, Any]) -> bool:
    """`and`, `or`, `not` over names — the subset the shipped templates use.

    Strict about undefined names, because Jinja is configured with `StrictUndefined`
    and the two engines agreeing matters more than either one's leniency.
    """
    cond = cond.strip()
    if " or " in cond:
        return any(_truthy(p, vars) for p in cond.split(" or "))
    if " and " in cond:
        return all(_truthy(p, vars) for p in cond.split(" and "))
    if cond.startswith("not "):
        return not _truthy(cond[4:], vars)
    if not _NAME.fullmatch(cond):
        raise TemplateError(
            f"the fallback template engine cannot evaluate {cond!r}; it understands "
            f"names joined by `and`/`or`/`not`. Install Jinja2 for the full language."
        )
    return bool(_lookup(vars, cond))


def _emit(nodes: list, vars: dict[str, Any]) -> str:
    out: list[str] = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "var":
            expr = node[1]
            if not _NAME.fullmatch(expr):
                raise TemplateError(
                    f"the fallback template engine cannot evaluate {{{{ {expr} }}}}; it "
                    f"understands plain names. Install Jinja2 for the full language."
                )
            out.append(str(_lookup(vars, expr)))
        elif kind == "if":
            out.append(_emit(node[2] if _truthy(node[1], vars) else (node[3] or []), vars))
        elif kind == "for":
            for element in _lookup(vars, node[2]) or []:
                out.append(_emit(node[3], {**vars, node[1]: element}))
    return "".join(out)


def _render_stdlib(text: str, vars: dict[str, Any]) -> str:
    """Jinja's common subset, without Jinja.

    Handles `{{ var }}`, `{% if %}`/`{% else %}`/`{% endif %}`, `{% for %}` and
    `{# comments #}`, with `trim_blocks` and `lstrip_blocks` to match the Jinja
    environment in `render`. Anything else RAISES rather than passing through, so an
    unsupported construct is a message naming it instead of template source delivered
    to a model as if it were prose.

    Parsed into a tree rather than substituted by regex. See `_parse`.
    """
    # Jinja's `keep_trailing_newline` defaults to False: it drops exactly one newline
    # at the end of the source. Matching that is not pedantry -- the templates are
    # concatenated into prompts, and one engine ending a block with a blank line and
    # the other not is a diff in every prompt the project sends.
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    return _emit(_parse(_tokenize(text)), vars)


def list_all(repo: Path | None = None, overrides: dict[str, str] | None = None) -> list[Template]:
    """BOTH registries.

    This walked `TEMPLATE_NAMES` only, so `ddflow prompts list` printed five templates
    and none of the six workflow commands -- which the MCP surface has served through
    `prompts/list` all along. Half the prompt library was reachable from an agent and
    invisible from a terminal, which is where an operator goes to edit one.
    """
    out = [resolve(n, repo, overrides) for n in TEMPLATE_NAMES]
    out += [resolve_command(n, repo) for n in all_commands(repo)]
    return out


def resolve_any(name: str, repo: Path | None = None, overrides: dict[str, str] | None = None):
    """A template or a command, whichever this name is.

    The surfaces should not have to know which registry a name lives in before they can
    look it up -- that knowledge is exactly what leaked out as `unknown template
    'research-companions'`, a message that listed the five templates to someone who had
    typed a real command name correctly.
    """
    if name in TEMPLATE_NAMES:
        return resolve(name, repo, overrides)
    if name in all_commands(repo):
        return resolve_command(name, repo)
    raise TemplateError(
        f"unknown prompt {name!r}.\n"
        f"  templates: {', '.join(TEMPLATE_NAMES)}\n"
        f"  commands:  {', '.join(sorted(all_commands(repo)))}"
    )
