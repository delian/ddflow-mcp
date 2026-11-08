"""The `[agent]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._docs import declare, knob
from ..services.modelreg import family_hints as _catalog_family_hints
from ..services.modelreg import load as _load_catalog

#: Model-name substring -> pretraining family, used to answer the one question the
#: review stack rests on: "is this reviewer independent of the author?"
#:
#: One map, in one module. There were two — this one and a richer `FAMILY_HINTS` in
#: `reviewer.py` — with different entries AND different answers for an unrecognised
#: name, which is the duplicate-then-drift class that has already cost this package
#: three bugs. The table is now READ from the model catalog (`modelreg.family_hints`,
#: DRV-model-catalog), one source for the one question; the reviewer config's own
#: `family` field is how a project classifies a model this table does not know.
FAMILY_HINTS: dict[str, str] = _catalog_family_hints(_load_catalog())


def family_for(model: str, families: dict[str, str] | None = None) -> str:
    """Pretraining family for a model name, or ``""`` when it is not recognised.

    The empty string is deliberate. Both former implementations returned a non-empty
    stand-in for an unknown model — one the literal name, one the string "unknown" —
    and both therefore compared unequal to every real family, so an unclassified
    reviewer *established* independence. Two unrecognised names may well be the same
    family; the honest answer is "cannot tell", and a check built to refuse unverified
    independence must not be satisfied by the absence of information.
    """
    low = (model or "").lower()
    for needle, fam in (families if families is not None else FAMILY_HINTS).items():
        # Case-blind, and an empty key matches nothing -- exactly as `router_set` reads
        # its needles. A capitalised key used to be ignored and an empty one matched
        # every model (B7f815b7d88).
        if needle and needle.lower() in low:
            return fam
    return ""


def family_name_of(model: str, families: dict[str, str] | None = None) -> str:
    """The family ``model`` IS when it is itself a family name, else ``""``.

    `family_for` maps a MODEL name to its family; this answers the other half of
    "accept a family in place of a model id" (DX-author-family): a bare family name --
    an `[agent].families` VALUE such as "alibaba" -- is a legal author identity, so it
    must not fall into ``""`` = "unknown" and be refused as an unrecognised model. The
    comparison is exact and case-blind: a model id never EQUALS a family value, so a
    model is judged by `family_for` as before, never re-classified here.
    """
    low = (model or "").strip().lower()
    known = {
        v.strip().lower() for v in (families if families is not None else FAMILY_HINTS).values()
    }
    return low if low and low in known else ""


def router_set(model: str, routers: dict[str, list[str]]) -> list[str] | None:
    """The family SET a router model draws on, or ``None`` when ``model`` is no router.

    A router (Copilot's HydraFusion) is chosen like a model but sends each task to
    models from several providers, so it has no one family to compare a reviewer with.
    Mapping it to a single family in `[agent].families` is the trap: a reviewer from one
    of the OTHER families it drew on would then pass as independent of work that family
    helped write. ``[]`` -- a known router whose members nobody has filled in -- is
    returned as such, never as ``None``: "a router, set unknown" must refuse, and
    falling through to `family_for` would refuse with the wrong remedy.

    Matched like `family_for`, by case-blind substring, and checked BEFORE it: a router
    name may contain a family needle and must not be taken for that one family. But
    where `family_for` may stop at the first match, this takes the UNION of every
    matching entry: first-match-wins on key order let `hydra = ["anthropic"]` hide
    `hydrafusion = ["openai", ...]`, and an openai reviewer then passed as independent
    of work openai wrote (B15af2d6420). Any matching entry left empty keeps the whole
    answer "set unknown" -- another entry's members are not the full set.
    """
    low = (model or "").lower()
    found: set[str] | None = None
    for needle, members in routers.items():
        if not (needle and needle.lower() in low):
            continue
        these = {m.strip().lower() for m in members if m.strip()}
        if not these:
            return []
        found = (found or set()) | these
    return None if found is None else sorted(found)


def _routers_members(v: Any) -> tuple[Any, list[str]]:
    """`[agent].routers` from a file. An entry in a TABLE shape this ddflow lacks (a newer
    release's) is KEPT with an empty family set, which `router_set` reads as "unknown" -- so
    the router still counts as a router and its author is refused as independent of nothing,
    rather than the entry vanishing and the model passing as an ordinary one. Each is noted.
    Any other malformed entry (a string, a list of non-words) is a typo and is left to the
    check, which refuses it."""
    if not isinstance(v, dict):
        return v, []
    shaped = [k for k, m in v.items() if isinstance(m, dict)]
    kept = {k: ([] if k in shaped else m) for k, m in v.items()}
    return kept, [
        f"kept router {k!r} with unknown families (a shape this ddflow does not know)"
        for k in shaped
    ]


#: Shortest and longest one-time code: below this a guess within the attempts is thinkable.
MIN_CODE, MAX_CODE = 8, 64


def _positive(v: object) -> str:
    return "" if isinstance(v, int) and not isinstance(v, bool) and v > 0 else "must be above 0"


def _code_length(v: object) -> str:
    fits = isinstance(v, int) and not isinstance(v, bool) and MIN_CODE <= v <= MAX_CODE
    return "" if fits else f"must be {MIN_CODE}..{MAX_CODE}"


def _verb_table(v: object) -> str:
    fits = isinstance(v, dict) and all(
        isinstance(m, list) and all(isinstance(x, str) for x in m) for m in v.values()
    )
    return "" if fits else 'must be a table of lists of verbs, e.g. { read = ["status"] }'


def _capability_verbs() -> dict[str, list[str]]:
    return {
        "read": [
            "help", "brief", "next", "status", "show", "board", "progress", "history", "list",
            "search", "recall", "similar", "gate status", "gate list", "decision list", "decision show",
            "decision search", "decision applicable", "rule list", "rule show", "rule search",
            "lesson search", "memory list", "version show", "flow show", "job list",
        ],
        "claim": [
            "claim", "heartbeat", "release", "wait", "gate record", "gate run", "merge", "complete",
        ],
        "note": ["session note", "session message", "bug found"],
        "start_lane": [],
        "approve_marked": [],
    }  # fmt: skip


@declare("agent")
@dataclass
class AgentConfig:
    """How ddflow talks to whichever agent is driving it."""

    #: "" = derive from hostname+pid
    id: str = knob(
        "",
        doc="Stable identity for this agent process, used to shard the event log so concurrent agents never write the same file. Empty auto-derives host-pid.",
    )
    reviewer_family_must_differ: bool = knob(
        True,
        doc="Legacy switch for `reviewer_independence`: false means `off`; true (the default) defers to `reviewer_independence`. Same-family agreement shares the author's blind spots, which is why a different family is preferred.",
    )
    reviewer_independence: str = knob(
        "model",
        doc="How independent a reviewer must be of the author (D-reviewer-independence-soft). `family`: at least one reviewer from a different pretraining family (the strictest; pin it for the whole organisation with a lock). `model`: a different family is preferred, and a DIFFERENT MODEL of the author's family also satisfies the gate, recorded as tier `model` -- the normal state for a single-subscription account. `off`: no independence is demanded. The author's own model never counts, and a model no map recognises establishes nothing, at any level. The legacy boolean `reviewer_family_must_differ = false` still means `off`.",
        choices=("family", "model", "off"),
        strictest=("family", "an unknown level asks for a reviewer of another family"),
    )
    families: dict[str, str] = knob(
        factory=lambda: dict(FAMILY_HINTS),
        doc="Model-name substring (matched case-blind; an empty key matches nothing) to pretraining-family map, used to enforce the rule above. Extend it as new families appear; an unknown model establishes nothing -- an unrecognised author is refused, an unrecognised reviewer is not counted as different.",
    )
    family: str = knob(
        "",
        doc="The author model's pretraining family, so `complete` never needs a model id written into this repository's log: `[agent].family` (or DDFLOW_AGENT_FAMILY, per run) is accepted by `complete` and `gate record` in place of `--model <author model>` -- a `--model` flag still wins, and the configured family otherwise wins over the model the session declared -- and the completion event then stores the family only, never a model name (a non-canonical spelling that is a `[agent].families` NEEDLE, such as \"gemini\", resolves to its canonical family value there). Set it once per harness, and let onboarding fill it in per harness. Change it for the project (`ddflow config --set agent.family deepseek`), for this machine (add --local), per run (DDFLOW_AGENT_FAMILY), or over MCP with `ddflow_configure`.",
    )
    # HydraFusion ships with NO members: GitHub publishes no fixed roster (research
    # R-hydrafusion-families), and a guessed set that misses a provider passes that
    # provider's reviewer as independent.
    routers: dict[str, list[str]] = knob(
        factory=lambda: {"hydrafusion": []},
        members=_routers_members,
        doc='Model-name substring to the SET of families a router author draws on -- a model that routes each task across providers, such as Copilot\'s HydraFusion. A reviewer is independent of a router only when its family is outside the whole set; every entry whose name matches adds its families, and one left empty makes the set unknown. Checked before `families`. Default {hydrafusion = []}: GitHub publishes no fixed roster, so the set is empty and `complete --model hydrafusion` refuses until you list the families your plan routes to, e.g. routers = { hydrafusion = ["anthropic", "openai", "google"] }. From the env, JSON only.',
        # TOML arrives typed and `_coerce` passes it through untouched, so a string where a
        # list belongs (`hydrafusion = "openai"`) would iterate as letters: a set of nonsense
        # families that matches no reviewer, and so clears every one.
        check=lambda v: (
            ""
            if isinstance(v, dict)
            and all(isinstance(m, list) and all(isinstance(x, str) for x in m) for m in v.values())
            else 'must be a table of lists of family names, e.g. { hydrafusion = ["openai"] }'
        ),
    )

    # The actor model (SE-actors, decisions D-remote and D-shared-engines-r2 10): a role is
    # a name, a capability is a name, and which role holds which capability is this table --
    # nothing is hard-coded per role. `services/actors.resolve_actor` is the one reader.
    roles: dict[str, list[str]] = knob(
        factory=lambda: {
            "operator": ["*"],
            "bot": ["read", "claim", "note", "start_lane", "approve_marked"],
            "agent": ["read", "claim", "note"],
            "observer": ["read"],
        },
        doc='Role name to the capabilities it holds: the one table every auth and approval path is to ask (serve auth, hub and scoped tokens, the approvals engine, waiver approvers: each lands as its own item and reads it through `services/actors`). A capability is any name a consumer asks for; the shipped ones are read, claim, note, start_lane (start a lane within the caps the operator sets), approve (answer any approval), approve_marked (answer only approvals marked bot-answerable), adopt, waive. "*" holds every capability. A role missing from the table, or a capability missing from its list, is refused: the table fails closed. Default: operator = ["*"], bot = read, claim, note, start_lane, approve_marked; agent = read, claim, note; observer = read. Widening a role is an operator act. Change it for the project (`ddflow config --set agent.roles ...`), for this machine (add --local), per run (DDFLOW_AGENT_ROLES, JSON only), or over MCP with `ddflow_configure` (the operator is told when an agent does).',
        check=lambda v: (
            ""
            if isinstance(v, dict)
            and all(isinstance(m, list) and all(isinstance(x, str) for x in m) for m in v.values())
            else 'must be a table of lists of capability names, e.g. { bot = ["read", "start_lane"] }'
        ),
    )
    actors: dict[str, str] = knob(
        factory=dict,
        doc='Actor id to role, for named actors such as a chat gateway or n8n: `{ openclaw = "bot" }`. Applied ONLY to an id a credential has proven (a paired token); an id an agent merely declares with --agent or as_agent is a claim and is never looked up here, so naming a role cannot be done by saying the name. Empty by default. Change it for the project (`ddflow config --set agent.actors ...`), for this machine (add --local), per run (DDFLOW_AGENT_ACTORS, JSON only), or over MCP with `ddflow_configure`.',
        check=lambda v: (
            ""
            if isinstance(v, dict) and all(isinstance(r, str) for r in v.values())
            else 'must be a table of role names, e.g. { openclaw = "bot" }'
        ),
    )
    default_role: str = knob(
        "agent",
        doc='The role of a caller no credential and no terminal vouches for: an agent harness, `--agent`, `DDFLOW_AGENT`, the MCP surface. Default "agent". A person at their own terminal is the operator role (see `agent.operator_role`); setting this to "operator" makes every caller one and is the widening it sounds like. Change it for the project (`ddflow config --set agent.default_role observer`), for this machine (add --local), per run (DDFLOW_AGENT_DEFAULT_ROLE), or over MCP with `ddflow_configure`.',
    )
    operator_role: str = knob(
        "operator",
        doc='The role of a person at their own terminal (a CLI call with no --agent, no DDFLOW_AGENT and no agent-harness marker). Default "operator". Change it for the project (`ddflow config --set agent.operator_role operator`), for this machine (add --local), per run (DDFLOW_AGENT_OPERATOR_ROLE), or over MCP with `ddflow_configure`.',
    )
    ask_ttl_s: int = knob(
        86400,
        doc="How long an ask (a question or an approval put to a person through the approvals engine) stays answerable before it expires, in seconds. An answer after that is refused and the asker must ask again; 0 or less refuses every ask, so keep it above 0. Default 86400 (a day). Change it for the project (`ddflow config --set agent.ask_ttl_s 3600`), for this machine (add --local), per run (DDFLOW_AGENT_ASK_TTL_S), or over MCP with `ddflow_configure`.",
    )
    ask_code_length: int = knob(
        8,
        doc="How many characters the one-time code of an ask has, when the transport asks for one (a remote channel proves the person got the message by presenting it). Read aloud or typed on a phone, so it avoids look-alike characters; the engine never makes it shorter than 4. Default 8. Change it for the project (`ddflow config --set agent.ask_code_length 6`), for this machine (add --local), per run (DDFLOW_AGENT_ASK_CODE_LENGTH), or over MCP with `ddflow_configure`.",
    )
    # Paired tokens (`services/auth`, P-remote.auth). Beside the roles: which verbs a capability
    # allows is the other half of the table above. Deliberately no knob for where the hashes live:
    # a cloned project's config must not point verification at a file it wrote.
    token_ttl_h: int = knob(
        168,
        doc="Hours a paired token lives unless `ddflow pair --ttl-h` says otherwise (default 168, a week). There is no never-expiring token: pair again or rotate (`ddflow tokens rotate`).",
        check=_positive,
    )
    token_max_ttl_h: int = knob(
        720,
        doc="The longest `ddflow pair --ttl-h` may ask for, in hours (default 720, 30 days); above it the pairing is refused.",
        check=_positive,
    )
    pair_ttl_s: int = knob(
        300,
        doc="Seconds the one-time code `ddflow pair` shows stays redeemable (default 300). It is meant to be read out and used at once.",
        check=_positive,
    )
    pair_code_length: int = knob(
        10,
        doc=f"Characters in the secret part of the one-time pairing code ({MIN_CODE}..{MAX_CODE}, default 10).",
        check=_code_length,
    )
    pair_max_attempts: int = knob(
        5,
        doc="Wrong guesses a pairing survives (default 5): the last one burns it, and the operator pairs again.",
        check=_positive,
    )
    capability_verbs: dict[str, list[str]] = knob(
        factory=_capability_verbs,
        doc="Capability name (`[agent].roles`) to the verbs it allows a token holder: `gate status`, or a glob like `gate *`. Read with [agent].roles this is the per-role verb list, and it is deny by default: a verb no capability names is the operator's, whose role holds `*` (or the capability `operator`). A token is checked against it on every call; a person at a terminal with no token is not.",
        check=_verb_table,
    )
    require_operator_token: bool = knob(
        False,
        doc="Off by default, so with no token presented a shell that unsets its agent markers still passes as the operator: that gap stays open unless this is on (D-operator-token-default). On: the verbs that today only ask whether the caller carries an agent marker also need an operator token (`DDFLOW_TOKEN`), so a shell with its markers unset is no longer a person. While no live operator token exists, `ddflow pair --role operator` still works from a marker-free terminal, or nobody could mint the first one or recover after the last expired. A same-user process can still edit config and the store: this stops accidents and prompt injection, not a hostile process of the same user.",
    )
