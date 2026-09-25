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


def resolve_command(name: str, repo: Path | None = None) -> Template:
    """A workflow command template, project override first.

    Same precedence as any other template: ``.ddflow/prompts/commands/<name>.md``
    beats the shipped default, so a project can rewrite a whole workflow without
    touching code -- which is the point of shipping them as text.
    """
    if name not in COMMANDS:
        raise TemplateError(f"unknown command {name!r}. Known: {', '.join(sorted(COMMANDS))}")
    if repo:
        local = Path(repo) / ".ddflow" / "prompts" / "commands" / f"{name}.md"
        if local.is_file():
            return Template(name, local.read_text("utf-8"), "project", local)
    path = command_dir() / f"{name}.md"
    if not path.is_file():
        raise TemplateError(f"shipped command {name}.md is missing from the package")
    return Template(name, path.read_text("utf-8"), "builtin", path)


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


_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")
_IF = re.compile(
    r"\{%\s*if\s+([A-Za-z_][A-Za-z0-9_.]*)\s*%\}(.*?)"
    r"(?:\{%\s*else\s*%\}(.*?))?\{%\s*endif\s*%\}",
    re.S,
)
_FOR = re.compile(
    r"\{%\s*for\s+([A-Za-z_]\w*)\s+in\s+([A-Za-z_][A-Za-z0-9_.]*)\s*%\}"
    r"(.*?)\{%\s*endfor\s*%\}",
    re.S,
)


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


def _render_stdlib(text: str, vars: dict[str, Any]) -> str:
    """Jinja's common subset, without Jinja.

    Handles `{{ var }}`, `{% if %}`/`{% else %}`/`{% endif %}` and `{% for %}`. Blocks
    are resolved innermost-first by repeated substitution, which is enough for prompt
    templates and nowhere near a general template language — by design. A project that
    wants the rest installs Jinja2 and gets it automatically.
    """

    def for_sub(m: re.Match) -> str:
        var, seq_name, body = m.group(1), m.group(2), m.group(3)
        seq = _lookup(vars, seq_name)
        out = []
        for element in seq or []:
            out.append(_render_stdlib(body, {**vars, var: element}))
        return "".join(out)

    def if_sub(m: re.Match) -> str:
        name, yes, no = m.group(1), m.group(2), m.group(3) or ""
        try:
            truthy = bool(_lookup(vars, name))
        except TemplateError:
            truthy = False  # `{% if x %}` on an absent name is False, not fatal
        return yes if truthy else no

    prev = None
    while prev != text:
        prev = text
        text = _FOR.sub(for_sub, text)
        text = _IF.sub(if_sub, text)
    return _VAR.sub(lambda m: str(_lookup(vars, m.group(1))), text)


def list_all(repo: Path | None = None, overrides: dict[str, str] | None = None) -> list[Template]:
    return [resolve(n, repo, overrides) for n in TEMPLATE_NAMES]
