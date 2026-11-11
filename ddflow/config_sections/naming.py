"""The `[naming]` section: how a lane or agent name is GENERATED (D-lane-naming-2026-11-11).

Re-exported from `ddflow.config`, which assembles `Config` from every section. The rules
that read these knobs live in `core/naming.py`. A name a person or harness declares
(`--agent`, `DDFLOW_AGENT`, `as_agent`, `[agent].id`) never passes through here: it is used
as spelled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ._docs import declare, int_at_least, knob

#: The tokens a name template may use.
NAME_TOKENS = ("role", "family", "host", "rand", "seq")
#: What a generated name may hold outside its tokens: lower-case ASCII letters, digits, '-'.
#: It is a git branch part, a shard file name (case-insensitive file systems), a tmux
#: session and a DNS label, so nothing wider is safe.
NAME_LITERAL = re.compile(r"[a-z0-9-]*")
NAME_SCOPES = ("global", "host", "family")
NAME_COLLISIONS = ("suffix", "refuse")


def name_template_problem(template: Any) -> str:
    """Why ``template`` cannot make lane names, or "". Scope-free: the scope's own
    requirement is `core.naming.template_problem`."""
    if not isinstance(template, str) or not template:
        return "must be a non-empty template string"
    tokens = re.findall(r"\{([^{}]*)\}", template)
    literal = re.sub(r"\{[^{}]*\}", "", template)
    if bad := [t for t in tokens if t not in NAME_TOKENS]:
        known = ", ".join(f"{{{t}}}" for t in NAME_TOKENS)
        return f"unknown token {{{bad[0]}}}; the tokens are {known}"
    if "{" in literal or "}" in literal:
        return "has an unbalanced brace"
    if not NAME_LITERAL.fullmatch(literal):
        bad_c = sorted({c for c in literal if not NAME_LITERAL.fullmatch(c)})[0]
        return (
            f"holds {bad_c!r} outside its tokens: a name is a branch part, a shard file and "
            "a tmux session, so only lower-case ASCII letters, digits and '-' may appear"
        )
    if len(tokens) != len(set(tokens)):
        return "uses a token twice"
    if "role" not in tokens:
        return "needs {role}: the human-readable part"
    if not {"rand", "seq"} & set(tokens):
        return "needs {rand} or {seq}: without one, two lanes of one role are one name"
    return ""


def _max_length_problem(v: Any) -> str:
    # 63 is a DNS label (RFC 1123), so a name fits a branch, a shard and a tmux session.
    if isinstance(v, int) and not isinstance(v, bool) and 16 <= v <= 63:
        return ""
    return "must be an integer from 16 to 63 (a DNS label is at most 63)"


def _rand_length_problem(v: Any) -> str:
    return "" if isinstance(v, int) and not isinstance(v, bool) and 3 <= v <= 12 else (
        "must be an integer from 3 to 12"
    )  # fmt: skip


def _host_label_problem(v: Any) -> str:
    if v == "":
        return ""
    if isinstance(v, str) and re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,10}[a-z0-9])?", v):
        return ""
    return "must be empty or a short lower-case label: letters, digits, '-', at most 12 characters"


@declare("naming")
@dataclass
class NamingConfig:
    """`[naming]`: how a generated lane or agent name is built and kept unique."""

    template: str = knob(
        "{role}-{family}-{host}-{rand}",
        doc=(
            "The shape of a GENERATED lane or agent name. Tokens: {role} (the human-readable "
            "label: chosen, else derived from the item or tree), {family} (the harness or "
            "model family, such as kilo), {host} (a short, stable host label; see "
            "`host_label`), {rand} (a short random suffix) and {seq} (the next free number). "
            "A template needs {role} and one of {rand} or {seq}; outside the tokens only "
            "lower-case ASCII letters, digits and '-' may appear, so a name is safe as a git "
            "branch part, a shard file on a case-insensitive file system, a tmux session and "
            "a DNS label. An empty token drops out with its '-'. A name a person or harness "
            "DECLARES (--agent, DDFLOW_AGENT, as_agent, [agent].id) is never generated and "
            "never rewritten: this applies to names ddflow makes. A recorded name is never "
            "renamed. `config --set` refuses an invalid template (exit 3); a file holding one "
            "is warned about and the default stays in effect. Change it for the project "
            "(`ddflow config --set naming.template '{role}-{host}-{seq}'`), for this machine "
            "(add --local), per run (DDFLOW_NAMING_TEMPLATE), or over MCP with "
            "`ddflow_configure`."
        ),
        check=name_template_problem,
    )
    scope: str = knob(
        "global",
        doc=(
            "How far a generated name must be unique BY CONSTRUCTION, because clones and hosts "
            "that work offline cannot coordinate. 'global' (default): across every host and "
            "clone of the project, so the template must hold {rand}, or {seq} together with "
            "{host}. 'host': among the lanes of one machine, so {rand} or {seq}. 'family': "
            "among the lanes of one agent family, so {family} with {rand} or {seq}. A name is "
            "always also checked against every taken name (case-folded) before it is used."
        ),
        choices=NAME_SCOPES,
        strictest=("global", "a name unique in every scope cannot clash in a narrower one"),
    )
    on_collision: str = knob(
        "suffix",
        doc=(
            "What happens when a generated name is already taken by another agent. 'suffix' "
            "(default): draw a new {rand} (or take the next {seq}) until it is free, within a "
            "bounded number of tries. 'refuse': stop with a message naming the taken name "
            "(exit 3). The same agent resuming reuses its own name under either; a declared "
            "name is never changed."
        ),
        choices=NAME_COLLISIONS,
        strictest=("refuse", "a clash stops the launch instead of making a different name"),
    )
    max_length: int = knob(
        48,
        doc=(
            "Longest generated name, 16 to 63 (a DNS label is at most 63, and the branch "
            "`ddflow/<name>` and a tmux session add to it). A longer name is shortened: "
            "{role}, then {family}, lose characters first; {host} and {rand} are never cut."
        ),
        check=_max_length_problem,
    )
    rand_length: int = knob(
        4,
        doc=(
            "How many characters {rand} has, 3 to 12. The alphabet has 27 symbols with no "
            "vowels and no 0, 1 or 3 (as Kubernetes' generated names), so 4 characters are "
            "about 19 bits: with {role}, {family} and {host} in the name, a clash needs all "
            "of them equal. Raise it for projects with very many offline clones."
        ),
        check=_rand_length_problem,
    )
    max_tries: int = knob(
        32,
        doc=(
            "How many names a collision may draw before the launch is refused (the bound of "
            "`on_collision = suffix`). With a 4-character {rand} a clash is rare, so more than "
            "a few tries means the template or `rand_length` is too narrow."
        ),
        check=int_at_least(1),
    )
    host_length: int = knob(
        12,
        doc="Longest {host} label in a generated name, cut from the end; 4 to 24.",
        check=lambda v: "" if isinstance(v, int) and not isinstance(v, bool) and 4 <= v <= 24 else "must be an integer from 4 to 24",
    )
    host_label: str = knob(
        "",
        doc=(
            "This machine's short host label for {host}. Empty (default) derives the "
            "lower-cased first label of the hostname, cut to 12 characters. Machine-local: "
            "set it with `--local` so a host name never enters committed config."
        ),
        check=_host_label_problem,
    )

