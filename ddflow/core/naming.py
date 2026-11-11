"""Lane and agent names: generated from a template, unique by construction (D-lane-naming-2026-11-11).

Pure: strings in, a name out. The caller supplies the taken names (shard files, lease
holders -- derived from the log, never stored here), the entropy and the host label.

Two kinds of name never mix. A name a person or harness DECLARES (`--agent`, `DDFLOW_AGENT`,
`as_agent`) is used as spelled, under today's rule (`core.agentname`): the user's word wins.
A name ddflow GENERATES is `[naming].template` rendered from role, family, host and a short
random suffix, held to lower-case `[a-z0-9-]` (a git branch part, a shard file on a
case-insensitive file system, a tmux session, a DNS label) and to `[naming].max_length`.

Uniqueness: a template must guarantee it for `[naming].scope` (`template_problem`), and a
generated name is always also compared, case-folded, with every taken name, because two
shard files that differ only by case are one file on macOS and Windows. The agent that
already holds a name and resumes keeps it: that is not a collision.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..config import Config, name_template_problem
from . import agentname

#: Vowel-free and without 0, 1, 3 (the Kubernetes alphabet), as Kubernetes' generated names:
#: nothing read aloud or typed on a phone is mistaken for another character or for a word.
RAND_ALPHABET = "bcdfghjklmnpqrstvwxz2456789"
#: What an empty role becomes.
DEFAULT_ROLE = "agent"

GENERATED = "generated"
EXPLICIT = "explicit"
RESUMED = "resumed"


class NameRefused(ValueError):
    """A name could not be made or used; the message says why and what to do."""


@dataclass(frozen=True)
class Resolved:
    """A name and where it came from."""

    name: str
    source: str  #: "generated" | "explicit" | "resumed"
    #: A warning worth showing (an explicit name that is a case twin of a taken one), or "".
    note: str = ""


def fold(name: str) -> str:
    """The form names are compared in: case-folded."""
    return name.casefold()


def clean(part: str, limit: int | None = None) -> str:
    """``part`` as a name part: lower-case, every run of characters outside ``[a-z0-9]``
    one ``-``, no edge dashes, cut to ``limit``."""
    text = re.sub(r"[^a-z0-9]+", "-", str(part).lower()).strip("-")
    if limit is not None:
        text = text[:limit].strip("-")
    return text


def host_part(cfg: Config, hostname: str) -> str:
    """This machine's label: `[naming].host_label`, else the first label of ``hostname``."""
    return clean(
        cfg.naming.host_label or hostname.split(".", maxsplit=1)[0], cfg.naming.host_length
    )


def template_problem(template: str, scope: str) -> str:
    """Why ``template`` does not guarantee uniqueness for ``scope``, or ""."""
    if problem := name_template_problem(template):
        return problem
    tokens = set(re.findall(r"\{([^{}]*)\}", template))
    # name_template_problem already requires {rand}: {seq} counts only the names one clone
    # sees and a host label can alias, so neither makes two offline clones differ.
    if scope == "family" and "family" not in tokens:
        return "is not unique within a family ([naming].scope = family): it needs {family}"
    return ""


def name_problem(name: str, *, max_length: int | None = None) -> str:
    """Why ``name`` is not a safe GENERATED name, or "": the generated charset and shape."""
    if not name:
        return "is empty"
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        return "must be lower-case letters and digits in groups joined by single '-'"
    if max_length is not None and len(name) > max_length:
        return f"is {len(name)} characters, over the {max_length} allowed"
    return ""


def draw_rand(length: int, draw: Callable[[str], str] | None = None) -> str:
    """``length`` characters of `RAND_ALPHABET`; ``draw(alphabet)`` yields one (tests)."""
    pick = draw or secrets.choice
    return "".join(pick(RAND_ALPHABET) for _ in range(length))


def _join(template: str, values: dict[str, str]) -> str:
    """``template`` with its tokens filled; an empty token drops out with its dash."""
    out = re.sub(r"\{([^{}]*)\}", lambda m: values[m.group(1)], template)
    return re.sub(r"-{2,}", "-", out).strip("-")


def render(cfg: Config, *, role: str, family: str, host: str, rand: str = "", seq: int = 0) -> str:
    """The template filled and shortened to `[naming].max_length`: {role}, then {family},
    lose characters first; {host}, {rand} and {seq} are never cut."""
    n = cfg.naming
    values = {
        "role": clean(role) or DEFAULT_ROLE,
        "family": clean(family),
        "host": clean(host, n.host_length),
        "rand": rand,
        "seq": str(seq) if seq else "",
    }
    name = _join(n.template, values)
    for key in ("role", "family"):
        over = len(name) - n.max_length
        if over <= 0:
            break
        keep = max(len(values[key]) - over, 1 if key == "role" else 0)
        values[key] = values[key][:keep].strip("-")
        name = _join(n.template, values)
    if len(name) > n.max_length:
        raise NameRefused(
            f"the name {name!r} is {len(name)} characters, over [naming].max_length "
            f"{n.max_length} even with {{role}} and {{family}} cut: shorten the host label "
            "or the template, or raise max_length (at most 63)"
        )
    return name


def generate(
    cfg: Config,
    *,
    role: str,
    family: str = "",
    host: str = "",
    taken: Iterable[str] = (),
    current: str = "",
    draw: Callable[[str], str] | None = None,
) -> Resolved:
    """A name for a lane or agent ddflow names itself.

    ``taken`` is every name in use (shard files, lease holders). ``current`` is the name
    this same agent already holds: a valid one is kept (resume, restart and re-claim
    change nothing). Otherwise the template is rendered; a taken result is re-drawn
    (`on_collision = suffix`) up to `[naming].max_tries` times, or refused (`refuse`)."""
    n = cfg.naming
    if current and not name_problem(current, max_length=63):
        # Before the template is read: a restart changes nothing, whatever the template says
        # now. A case twin among the taken names is noted, as for a declared name.
        return Resolved(current, RESUMED, _twin_note(current, taken))
    if problem := template_problem(n.template, n.scope):
        raise NameRefused(f"[naming].template {n.template!r} {problem}")
    used = {fold(t) for t in taken}
    uses_rand = "{rand}" in n.template
    name = ""
    for attempt in range(1, n.max_tries + 1):
        name = render(
            cfg,
            role=role,
            family=family,
            host=host,
            rand=draw_rand(n.rand_length, draw) if uses_rand else "",
            seq=attempt if "{seq}" in n.template else 0,
        )
        if fold(name) not in used:
            return Resolved(name, GENERATED)
        if n.on_collision == "refuse":
            raise NameRefused(
                f"the name {name!r} is already taken by another agent "
                "([naming].on_collision = refuse): launch again for a new suffix, or set "
                "on_collision = suffix"
            )
    raise NameRefused(
        f"no free name after {n.max_tries} tries for {name or n.template!r}: widen the template with "
        "{rand} or a longer [naming].rand_length"
    )


def declared(name: str, taken: Iterable[str] = ()) -> Resolved:
    """A name a person or harness declared: used AS SPELLED (it keeps `core.agentname`'s
    rule, wider than a generated name, so every existing name such as ``lane3-kilo`` or
    ``Monster3-x-7267fe`` stays valid). Never rewritten; a case twin of a taken name is
    noted, because the two are one shard file on a case-insensitive file system."""
    if not agentname.is_valid(name):
        raise NameRefused(agentname.refusal(name))
    return Resolved(name, EXPLICIT, _twin_note(name, taken))


def _twin_note(name: str, taken: Iterable[str]) -> str:
    """A warning when ``name`` differs only by case from a taken name, else ""."""
    twins = sorted(t for t in set(taken) if t != name and fold(t) == fold(name))
    if not twins:
        return ""
    return (
        f"{name!r} differs only by case from the taken name {twins[0]!r}: one shard file on "
        "a case-insensitive file system"
    )
