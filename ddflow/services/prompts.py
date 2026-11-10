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
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ..config import Config
from ..infra.paths import templates_dir
from .overlay import OverlayError, OverlayLoader

#: Every template the system uses. Registered here so `ddflow prompts list` can show
#: them all, and so a typo in a template name fails loudly instead of falling back to a
#: default nobody notices.
TEMPLATE_NAMES: tuple[str, ...] = (
    "review_system",  # the reviewer's system prompt
    "review_user",  # the per-chunk user message
    "gate_instruction",  # what an agent is told to do for an agent gate
    "session_brief_header",
    "agent_brief",  # the launch brief of a subagent (`ddflow brief --for-agent`)
    "agent_report",  # the completion report of an item (`ddflow report`)
    # What the MCP client injects on connect: the workflow, the reporting duties and
    # the companion tools. Text, because it is the file you edit to change how this
    # project works rather than what the code does.
    "mcp_instructions",
)


from ..adapters import templates as _tpl  # noqa: E402
from ..adapters.templates import TemplateError, _render_stdlib  # noqa: E402,F401  (re-exported)


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
#: invokes, and these are operator workflows, not primitives.
#: name -> (title, one-line description, [argument names])
COMMANDS: dict[str, tuple[str, str, list[str]]] = {
    "implement": (
        "Drive the queue to completion, unattended",
        "The autonomous loop: one item per iteration — recover, pick, claim, every gate, "
        "merge, complete, capture, cadences — until nothing actionable is left. Carries "
        "what the per-item driver does not: the four cases in which the loop may stop, "
        "keeping it running across turns, when to ask the operator, and the termination "
        "checklist. Scope it to a phase or task id, or leave it empty for the whole queue.",
        ["scope"],
    ),
    "onboard": (
        "Onboard this project onto ddflow, end to end, with the operator",
        "The whole path from 'the MCP server is attached' to a workflow ddflow manages: "
        "preflight (leftover worktrees and branches: merged or holding work), setup from "
        "the durable place, a measured test gate, the history import, cutting the "
        "project's rulebook over from its old todo/lessons/journal files to ddflow, "
        "freezing those files, and an end-to-end verification before the commit. Asks "
        "the operator at each decision rather than in one list at the end. Scope it to "
        "one stage (`preflight`, `verify`, ...) or leave it empty for the whole path.",
        ["scope"],
    ),
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
    "install-companions": (
        "Install and register the companion tools the gates expect",
        "Surveys the registry, sorts each companion into register / install / check / "
        "nothing, asks the operator per install with the exact command and what it "
        "executes, runs only what they approved, proves each by re-probing rather than "
        "trusting the installer's exit code, registers the MCP ones (dry run first), and "
        "records every decline so its gate is `unavailable`, never passed unaided. "
        "Scope it to companion ids, or leave it empty for the defaults.",
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
    "dedupe_sweep": (
        "Settle near-duplicate records already in the log",
        "The knowledge-dedupe cadence pass: run `ddflow dupes --open-only`, read each "
        "pair, and settle it with `ddflow link <a> --duplicate-of <b>` (a duplicate, and "
        "for two lessons a merge), `--related`, or `--distinct` when the pair is really "
        "different — a dismissed pair never returns. Record the pass with "
        "`ddflow cadence --ran dedupe_sweep`.",
        [],
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
    "perf_report": (
        "Answer performance questions from the perf report",
        "When the operator asks how slow, how expensive or how a gate, reviewer, model or "
        "agent is trending: run the perf report, read its figures against the sample-size "
        "and not-collected rules, and answer in chat with the exact next call in CLI and "
        "MCP form (the report already names both). A probe that could not run is "
        "unavailable, never zero or passed.",
        [],
    ),
}


def builtin_dir() -> Path:

    return templates_dir() / "prompts"


def command_dir() -> Path:
    return builtin_dir() / "commands"


def _macros():
    """`services.macros`, imported on first use: it imports this module for `COMMANDS` and
    `render`, so neither side can import the other at module level."""
    from . import macros

    return macros


def _macro_report(repo: Path | None) -> tuple[dict, dict[str, str]]:
    """`load_macros_report`, or nothing when the macro config cannot be read at all.

    A malformed macro file must not take `prompts/list` down with it: the shipped commands
    are still there, and `macro_problems` names the error -- in doctor, `prompts list` and
    the "unknown prompt" message. Losing the whole list to one bad block is how a feature
    becomes something people switch off.
    """
    if repo is None:
        return {}, {}
    M = _macros()
    try:
        return M.load_macros_report(repo)
    except (M.MacroError, ValueError, OSError):
        return {}, {}


def not_loaded_note(repo: Path | None) -> str:
    """One `\\n  not loaded: <why>` line per reason a configured macro is unusable, or "".

    Includes a config that cannot be read at all (parse error, undecodable prompt_file),
    which `all_commands` swallows -- so a surface that says "unknown prompt" can say why
    (B57fc667efc).
    """
    if not repo:
        return ""
    return "".join(f"\n  not loaded: {p}" for p in _macros().macro_problems(Path(repo)))


def macro_commands(repo: Path | None = None) -> dict[str, tuple[str, str, list[str]]]:
    """Operator-defined `[[macro]]` blocks, in the same shape as `COMMANDS`.

    So every surface that lists workflow commands lists macros too, without knowing which
    registry a name came from. A macro that had to be asked for separately is a macro an
    agent never finds.
    """
    return _as_commands(_macro_report(repo)[0])


def _as_commands(macros: dict) -> dict[str, tuple[str, str, list[str]]]:
    return {name: (m.title or name, m.description, list(m.params)) for name, m in macros.items()}


def all_commands(repo: Path | None = None) -> dict[str, tuple[str, str, list[str]]]:
    """Shipped workflow commands plus the project's macros.

    Shipped ones win a name collision, and `services.macros` refuses the collision at the
    source too — silent shadowing is the class that once had `api.review` bind a function
    over its own submodule.
    """
    macros, refused = _macro_report(repo)  # one read of the config, not two
    out = {**COMMANDS, **_as_commands(macros)}
    # A `[[macro]]` refused for taking a shipped command's name is said ON that command's
    # entry: MCP `prompts/list` has no field for notes, and a client listing prompts must
    # still see why the operator's block is not the one it gets (B-macro-clash-silent).
    for name, why in refused.items():
        if name in out:
            title, desc, args = out[name]
            out[name] = (title, f"{desc} [NOTE: {why}]".strip(), args)
    return out


def resolve_command(name: str, repo: Path | None = None) -> Template:
    """A workflow command template, project override first.

    Same precedence as any other template: ``.ddflow/prompts/commands/<name>.md``
    beats the shipped default, so a project can rewrite a whole workflow without
    touching code -- which is the point of shipping them as text.

    Falls through to `[[macro]]` blocks, so an operator-defined mode resolves by the same
    call every surface already makes.
    """
    M = _macros()
    if name not in COMMANDS:
        macro = (M.load_macros(repo) if repo else {}).get(name)
        if macro is not None:
            # A macro's body is its own; there is no shipped default to fall back to, and
            # its `source` says `config` so `ddflow prompts list` shows where it came from.
            try:
                return Template(
                    name, macro.body(Path(repo)), "config", macro.file(Path(repo)), "command"
                )
            except M.MacroError as exc:
                raise TemplateError(str(exc)) from exc
        known = sorted({*COMMANDS, *(M.load_macros(repo) if repo else {})})
        raise TemplateError(f"unknown command {name!r}. Known: {', '.join(known)}")
    try:
        got = _command_loader().resolve(name, repo)
    except OverlayError as exc:
        raise TemplateError(str(exc)) from exc
    return Template(name, got.text, got.source, got.path, "command")


def _command_loader() -> OverlayLoader:
    """The overlay of the shipped workflow commands (D-unify, B-uni-overlay.4):
    ``.ddflow/prompts/commands/<name>.md`` over the package default."""
    return OverlayLoader(
        "command", shipped_dir=command_dir(), project_subdir="prompts/commands", suffix=".md"
    )


def overrides_from(cfg) -> dict[str, str]:
    """`[prompts]` as the override map `resolve` takes.

    Was `_prompt_overrides(c)` in `surfaces/cli.py`, taking a whole `Ctx` to read one
    config section — so the api layer could not resolve a template with the operator's
    overrides honoured without reaching up into a surface. Three call sites, one of which
    is the gate instruction an agent is handed.
    """

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
    # A configured override that does not exist is an error, never a silent fallback: the
    # operator asked for THEIR prompt and would otherwise get the default while believing
    # their edit was live.
    try:
        got = _loader().resolve(name, repo, (overrides or {}).get(name, ""))
    except OverlayError as exc:
        raise TemplateError(str(exc)) from exc
    return Template(name, got.text, got.source, got.path)


def _loader() -> OverlayLoader:
    """The overlay of the shipped prompts (D-unify, B-uni-overlay.2): config path, then
    ``.ddflow/prompts/<name>.md``, then the package default."""
    return OverlayLoader(
        "template",
        shipped_dir=builtin_dir(),
        project_subdir="prompts",
        suffix=".md",
        missing_configured=lambda name, path: (
            f"[prompts].{name} points at {path}, which does not exist"
        ),
    )


def render(tmpl: Template | str, **vars: Any) -> str:
    text = tmpl.text if isinstance(tmpl, Template) else tmpl
    try:
        return _tpl.DEFAULT.render(text, **vars)
    except TemplateError as exc:
        # Every template failure, not only an undefined variable: a SYNTAX error escaped
        # as jinja2's own exception, through callers that promise never to raise
        # (`services.review.review`), as a traceback (bug Baf7d5082f5).
        raise TemplateError(f"template {getattr(tmpl, 'name', '?')}: {exc}") from exc


def list_all(repo: Path | None = None, overrides: dict[str, str] | None = None) -> list[Template]:
    """BOTH registries.

    This walked `TEMPLATE_NAMES` only, so `ddflow prompts list` printed five templates
    and none of the six workflow commands -- which the MCP surface has served through
    `prompts/list` all along. Half the prompt library was reachable from an agent and
    invisible from a terminal, which is where an operator goes to edit one.
    """
    out = [resolve(n, repo, overrides) for n in TEMPLATE_NAMES]
    for n in all_commands(repo):
        try:
            out.append(resolve_command(n, repo))
        except TemplateError:
            # A MACRO whose body cannot be read: listed under `macro_problems` by name,
            # instead of taking the whole list down with it. A shipped command that fails
            # is a broken install and still raises.
            if n in COMMANDS:
                raise
            continue
    return out


def suite_gates(repo: Path) -> list[str]:
    """Every configured gate that looks like a test suite, beyond `unit_tests`.

    Read from the project's own config so the `all-tests` command names the suites
    that actually exist here, rather than a generic list the reader has to translate.
    """
    try:
        cfg = Config.load(repo)
        from .gates import (
            load_gates,  # deferred: services.gates imports reviewer_trust -> review -> prompts
        )

        gates = load_gates(repo, cfg)
    except Exception:
        return []
    return sorted(
        g.id
        for g in gates.values()
        if g.id != "unit_tests" and g.is_command_gate and _looks_like_a_suite(g.id)
    )


def _looks_like_a_suite(gate_id: str) -> bool:
    """`ui` only as a whole word: as a substring it matched `build` and `require`
    (B-suite-gates-ui, found reviewing B5a2a2933c9)."""
    words = re.split(r"[^a-z0-9]+", gate_id.lower())
    return "ui" in words or any(w in gate_id for w in ("test", "e2e", "smoke", "integration"))


def render_command(name: str, repo: Path, args: dict[str, Any] | None = None) -> str:
    """A workflow command or `[[macro]]` as an agent receives it: the ONE rendering MCP
    `prompts/get`, the `ddflow_prompts` tool's `get` and `ddflow prompts get` share (B5a2a2933c9).

    A shipped command binds every declared argument, empty when absent: the renderer is
    strict about undefined names, and a command that raises because the operator omitted
    an optional argument is a command nobody uses twice. A macro's declared params are
    REQUIRED and its tool preamble goes on top (`macros.render`): an operator who declares
    a parameter is saying the mode does not make sense without it.

    Raises `TemplateError` for an unknown name, a macro config that could not be read
    (with the reason, as MCP says it) and a macro that refuses (a missing parameter).
    """
    args = dict(args or {})
    if name in COMMANDS:
        declared = dict.fromkeys(COMMANDS[name][2], "")
        return render(
            resolve_command(name, repo), **{**declared, **args, "test_gates": suite_gates(repo)}
        )
    M = _macros()
    macro = _macro_report(repo)[0].get(name)
    if macro is None:
        raise TemplateError(
            f"unknown prompt {name!r}. Known: {', '.join(sorted(all_commands(repo)))}"
            + not_loaded_note(repo)
        )
    try:
        return M.render(macro, repo, {k: str(v) for k, v in args.items()})
    except M.MacroError as exc:
        raise TemplateError(str(exc)) from exc


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
    # Why a configured macro is not among the commands, said HERE: "unknown prompt" about
    # a macro the operator can see in their config, with the reason only in doctor, is the
    # silence B-macro-clash-silent was about.
    raise TemplateError(
        f"unknown prompt {name!r}.\n"
        f"  templates: {', '.join(TEMPLATE_NAMES)}\n"
        f"  commands:  {', '.join(sorted(all_commands(repo)))}" + not_loaded_note(repo)
    )
