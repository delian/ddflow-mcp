"""Reviewer independence: the family of each reviewer and whether one differs from the author."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...config import Config, family_for, router_set
from ...config_sections.agent import family_name_of
from ...core.bookkeeping import STATE_EXCLUDE
from ...core.digest import content_digest, hasher
from ...core.model import State
from ...infra import git as GIT
from ..approvals import hints
from .defs import DEFAULT_GATES, GateDef


def family_of(model: str, cfg: Config) -> str:
    """This project's view of a model's family: `[agent].families`, which defaults to
    the shipped map. ``""`` means "not recognised" — see `config.family_for`."""
    return family_for(model, cfg.agent.families)


def _declared_family(evidence: dict[str, Any]) -> str:
    """The family `ddflow review` recorded from the operator's reviewer entry, or ``""``.

    A served model name can belong to another family -- this project's Qwen critic is
    served as `google/gemma-4-31B-it` -- which is why a reviewer entry declares one
    (B6ed8b9edb8). Trusted only in evidence `ddflow review` wrote (it names the
    `reviewer`); an agent's `gate record` cannot write a family, so a manual record is
    judged by its model name as before.
    """
    if not evidence.get("reviewer"):
        return ""
    fam = str(evidence.get("family") or "").strip().lower()
    return "" if fam == "unknown" else fam


def _unapproved_reviewer(state: State, evidence: dict[str, Any]) -> str:
    """The reviewer's name when ``evidence`` came from a tool-written, unapproved entry.

    `""` for everything else, and in particular for an entry no tool wrote (the
    operator's own, which counts as it always did) and for evidence recorded before
    digests were (nothing to judge it by, so it is judged as before).
    """
    dig = str(evidence.get("reviewer_digest") or "")
    if not dig or not evidence.get("reviewer"):
        return ""
    if dig in state.reviewer_writes and dig not in state.reviewer_approvals:
        # The name is the `reviewer.configured` subject (kept verbatim); the evidence's
        # own `reviewer` copy is redacted from the committed log (bug B8ef0d325ed).
        wrote = state.reviewer_writes[dig]
        name = wrote.get("name") if isinstance(wrote, dict) else ""
        return str(name or evidence["reviewer"])
    return ""


#: The built-in gates whose recorded model counts toward reviewer independence. A gate
#: whose definition declares `reviewer = "different_family"` counts too (`reviewer_gates`).
#: `standards` is here without declaring it: roborev records the model that reviewed.
REVIEWER_GATES = ("rubber_duck", "critic", "standards")

#: An author of a single (unknown) family is guaranteed one reviewer outside it once the
#: recorded panel comes from this many distinct families (bug B1a30dc3303).
_CROSS_FAMILY_PANEL = 2


def is_reviewer_gate(gate: str, gdef: GateDef | None) -> bool:
    """Is `gate` one a cross-family reviewer records through? Its definition says so
    (B0e1330bf74) -- `gdef`, or the built-in definition when none is given:
    `reviewer = "different_family"` is one, `same_family_ok` is not (a same-family
    reviewer is enough there, so its record shows no independence and its model is not
    the author-family mistake `gate record` refuses). A gate declaring neither is one
    only when it is in `REVIEWER_GATES`: that is `standards`, which declares nothing
    and counts because roborev records the model that reviewed."""
    gdef = gdef if gdef is not None else DEFAULT_GATES.get(gate)
    declared = gdef.reviewer if gdef is not None else ""
    if declared:
        return declared == "different_family"
    return gate in REVIEWER_GATES


def reviewer_gates(gates: Mapping[str, GateDef] | None) -> list[str]:
    """Every reviewer gate among `gates` (a built-in one they leave undefined included);
    with no definitions to read, among the built-in ones."""
    defs = {**DEFAULT_GATES, **(gates or {})}
    return [g for g, d in defs.items() if is_reviewer_gate(g, d)]


def reviewer_ran(state: State, item_id: str, gates: Mapping[str, GateDef] | None) -> bool:
    """Did ANY reviewer gate truly run -- record an outcome from a REVIEWER, not a
    hand-record by an agent (whose ``by`` carries no reviewer identity)? The generated-
    class waiver of B-lockfile-only-item-cannot-complete may not excuse independence
    once a review really ran: content WAS sent and judged, so the ordinary cross-family
    rule governs it and the reviewer independence check stays on. A passed/failed/
    partial record without a reviewer identity (no ``reviewer`` name, no ``model``) is a
    gate someone recorded by hand -- nothing reviewed anything."""
    recs = state.items[item_id].gates
    for gate in reviewer_gates(gates):
        rec = recs.get(gate)
        if rec is None or rec.outcome not in ("passed", "failed", "partial"):
            continue
        evidence = {**(rec.evidence or {})}
        if evidence.get("reviewer") or evidence.get("model"):
            return True
    return False


def _reviewer_fams(
    state: State, it, gates: Mapping[str, GateDef] | None, cfg: Config
) -> tuple[list[tuple[str, str]], list[str], list[tuple[str, str]]]:
    """(fams, anonymous, unapproved) over every reviewer gate of ``it`` with an outcome a
    review recorded: its (gate, family) pairs, names with no recognisable family, and
    tool-written entries a person has not approved."""
    fams: list[tuple[str, str]] = []
    anonymous: list[str] = []
    unapproved: list[tuple[str, str]] = []
    for gname in reviewer_gates(gates):
        rec = it.gates.get(gname)
        if not rec or rec.outcome not in ("passed", "failed", "partial"):
            continue
        if who := _unapproved_reviewer(state, rec.evidence):
            unapproved.append((gname, who))
            continue
        m = str(rec.evidence.get("model", rec.by) or "").strip()
        fam = (
            (
                _declared_family(rec.evidence)
                or family_name_of(m, cfg.agent.families)
                or family_of(m, cfg).strip().lower()
            )
            .strip()
            .lower()
        )
        # An UNIDENTIFIED reviewer cannot establish independence — see `family_of`.
        if not fam:
            anonymous.append(f"{gname}={m or 'no model'}")
            continue
        fams.append((gname, fam))
        # A gate dispatched to several reviewers records only its identifying pass above;
        # the panel's other families are carried in `reviewer_families` so a clean
        # cross-family round is still visible (bug B1a30dc3303).
        for f in rec.evidence.get("reviewer_families") or []:
            ff = str(f).strip().lower()
            if ff and ff != fam:
                fams.append((gname, ff))
    return fams, anonymous, unapproved


def _author_identity(author_model: str, cfg: Config) -> tuple[list[str] | None, str]:
    """(routed, family) the AUTHOR resolves to, so `reviewer_independence` only carries
    the branches (r-small-files budget): the family SET a router model draws on (NONE
    when it is no router) and its single family, stripped and lowercased.

    Compared case-blind on BOTH sides: a map value 'Alibaba' and a declared 'ALIBABA'
    are one family (B-fam-case). A bare family name is an identity of its own:
    `[agent].family` / DDFLOW_AGENT_FAMILY says "the AUTHOR is this family" with no
    model id to record (DX-author-family), so it must not resolve to "" and read as an
    unrecognised model, and it is judged BEFORE the router map: a router key spelling
    the family would otherwise hijack that identity, and an empty entry would
    misdiagnose the family as a router. A family is EXACT and case-blind
    (`family_name_of`); a router is matched by substring and consulted only when the
    author is no configured family.
    """
    bare_family = family_name_of(author_model, cfg.agent.families)
    routed = None if bare_family else router_set(author_model, cfg.agent.routers)
    author_fam = (
        (bare_family or family_of(author_model, cfg)).strip().lower() if routed is None else ""
    )
    return routed, author_fam


def _author_no_identity_refusal(author_model: str, routed, author_fam: str) -> str:
    """The refusal when the author resolved to no identity a reviewer can stand against,
    "" for anything else. Each branch was a `reviewer_independence` early return; moved
    out so that function keeps its branch count under the architecture guard."""
    if routed == []:
        return (
            f"the author's model {author_model!r} is a router in [agent].routers with "
            f"no families listed, so no reviewer can be shown to sit outside the models "
            f"it drew on. List every family your plan routes it to, e.g. "
            f'routers = {{ hydrafusion = ["anthropic", "openai", "google"] }}.'
        )
    if not author_model.strip():
        # Quoting `''` back at the agent named nothing it could act on (B7a5c63e3d2).
        return (
            "the author's model is unknown, so no reviewer can be shown to differ from "
            "it: pass `--model <author model>` (or an `[agent].families` family name) "
            "(`model` over MCP), declare it once with `ddflow session start --model "
            "<author model>` under the same identity, or set `[agent].family` "
            "(DDFLOW_AGENT_FAMILY) so no model id is ever written to the log."
        )
    if routed is None and not author_fam:
        return (
            f"the author's model {author_model!r} is not in [agent].families, so no "
            f"reviewer can be shown to differ from it. Add it to the map (or, for a "
            f"model that routes across providers, to [agent].routers with the families "
            f"it draws on), pass `--model` with a name the map recognises, or pass a "
            f"family name value of [agent].families (`[agent].family` / "
            f"`DDFLOW_AGENT_FAMILY` does the same): the family is the author then, with "
            f"no model id recorded."
        )
    return ""


def reviewer_independence(
    state: State,
    cfg: Config,
    item_id: str,
    author_model: str,
    gates: Mapping[str, GateDef] | None = None,
) -> tuple[bool, str]:
    """Is the review independent enough at `[agent].reviewer_independence`?

    (satisfied, explanation); `independence_tier` has the tier. Level `family` reports a
    same-family panel unsatisfied even when every reviewer passed: agreement among models
    trained on the same distribution measures shared priors, not correctness."""
    tier, why = independence_tier(state, cfg, item_id, author_model, gates)
    return tier != "none", why


def _bare(model: str) -> str:
    """A model id without its provider prefix, lowercased: `a/claude-x` is `claude-x`."""
    return model.strip().lower().rsplit("/", 1)[-1]


def is_author_model(model: str, author: str) -> bool:
    """Is ``model`` the author's own model (the hard floor), prefix and case blind?"""
    return bool(_bare(model)) and _bare(model) == _bare(author)


def review_tier(cfg: Config, author_model: str, rev_model: str, rev_family: str = "") -> str:
    """The independence tier of ONE review against the author: `family` (the reviewer's
    family is outside the author's), `model` (the same family, a different model id), or
    `none`. The author's own model never counts; a reviewer whose family is not
    recognised, or an author that is a bare family or an unknown model, establishes
    nothing. The one place a tier is decided (D-reviewer-independence-soft)."""
    routed, author_fam = _author_identity(author_model, cfg)
    fam = (
        (_declared_family({"reviewer": "x", "family": rev_family}) or family_of(rev_model, cfg))
        .strip()
        .lower()
    )
    # A provider prefix does not make another model: compare the last path segment.
    m, a = _bare(rev_model), _bare(author_model)
    if not fam or (routed is None and not author_fam) or (m and m == a):
        return "none"  # nothing established, or the author's own model
    if fam not in (routed if routed is not None else [author_fam]):
        return "family"
    bare = bool(family_name_of(author_model, cfg.agent.families))
    named_family = bool(family_name_of(rev_model, cfg.agent.families))  # a family is no model
    return (
        "model"
        if routed is None and m and a and m != a and not bare and not named_family
        else "none"
    )


def model_tier_of(state: State, cfg: Config, item_id: str, author_model: str, gates, fams) -> str:
    """The recorded model of a reviewer gate that reached tier `model`, "" when none."""
    it = state.items[item_id]
    for g, fam in fams:
        rec = it.gates.get(g)
        m = str((rec.evidence.get("model") if rec else "") or "").strip()
        if m and review_tier(cfg, author_model, m, fam) == "model":
            return m
    return ""


def independence_level(cfg: Config) -> str:
    """The effective reviewer-independence level: `family` when `[agent].reviewer_independence`
    says so (the strictest wins over the legacy boolean), `off` when the legacy
    `reviewer_family_must_differ` is false, else the knob."""
    if cfg.agent.reviewer_independence == "family":
        return "family"
    if not cfg.agent.reviewer_family_must_differ:
        return "off"
    return cfg.agent.reviewer_independence


def tier_notes(it, gates: Mapping[str, GateDef] | None = None) -> list[str]:
    """One neutral line when a recorded review reached tier `model` (a different model of
    the author's family): a normal state, stated once, with the way to a cross-family
    review as a hint."""
    # Only a review that RAN counts: an unavailable gate carrying a tier establishes nothing.
    recs = {
        g: r
        for g in reviewer_gates(gates)
        if (r := it.gates.get(g)) and r.outcome in ("passed", "failed", "partial")
    }
    tier = lambda r: r.evidence.get("independence_tier")  # noqa: E731
    if any(tier(r) == "family" for r in recs.values()):
        return []  # a cross-family review exists: nothing to hint at
    got = [
        f"{g} by {r.evidence.get('model') or r.by}" for g, r in recs.items() if tier(r) == "model"
    ]
    if not got:
        return []
    return [
        f"reviewed within the author's model family ({', '.join(got)}; tier model). "
        f"Hint: a reviewer of another family, if one is configured, is run with "
        f"`ddflow review --reviewer <name>`."
    ]


def _no_reviewer_refusal(fams, anonymous, unapproved, author_desc: str) -> str:
    """Why no reviewer could count at all, "" when at least one identified reviewer ran."""
    if unapproved and not fams:
        # The names as recorded, never re-parsed out of the text: a name with a space
        # in it would print an approve command for the wrong reviewer (rubber duck).
        names = sorted({who for _g, who in unapproved})
        return (
            f"{'; '.join(f'{g} came from reviewer {w!r}' for g, w in unapproved)}, whose entry a tool wrote and a person has "
            f"not approved, so it is not counted (decision D-reviewer-trust): an agent can "
            f"write a reviewer, so it cannot vouch for one. The operator reviews the "
            f"entry and runs "
            + " and ".join(f"`ddflow reviewers approve {shlex.quote(n)}`" for n in names)
            + " at the host (a command reviewer can only be approved that way). "
            "For an HTTP entry the operator can answer over chat instead: " + hints.route()
        )
    if not fams:
        if anonymous:
            return (
                f"no reviewer named a model this project recognises "
                f"({', '.join(anonymous)}), so nothing shows the review came from a "
                f"different family than the author ({author_desc}). Re-record with "
                f"`--model <the reviewer's model>`, or teach [agent].families the name."
            )
        return "no reviewer ran at all"
    return ""


def independence_tier(
    state: State,
    cfg: Config,
    item_id: str,
    author_model: str,
    gates: Mapping[str, GateDef] | None = None,
) -> tuple[str, str]:
    """The ONE independence check (D-reviewer-independence-soft): (tier, explanation) with
    tier `family` (a reviewer outside the author's family), `model` (only a different
    model of the author's family, allowed at level `model`) or `none`. At level `off`
    the tier is `off`. The author's own model never counts; an unrecognised model
    establishes nothing."""
    level = independence_level(cfg)
    if level == "off":
        return "off", "reviewer independence is off ([agent].reviewer_independence)"
    it = state.items.get(item_id)
    if not it:
        return "none", f"no such item {item_id}"
    # A router author (Copilot's HydraFusion) is a SET of families: any of them may
    # have written the diff, so a reviewer must sit outside all of them.
    routed, author_fam = _author_identity(author_model, cfg)
    fams, anonymous, unapproved = _reviewer_fams(state, it, gates, cfg)
    if why := _author_no_identity_refusal(author_model, routed, author_fam):
        # An author with no resolvable family -- an empty model, or a name no map
        # recognises -- still has exactly ONE family. Reviewers from TWO OR MORE distinct
        # families therefore include one outside the author's family whatever it is, so a
        # clean cross-family panel satisfies independence even though the author's own
        # family cannot be named (bug B1a30dc3303). A router author (`routed` is not None)
        # is a SET of families and is exempt: it may overlap every reviewer's family.
        families = {f for _g, f in fams}
        if routed is None and len(families) >= _CROSS_FAMILY_PANEL:
            return (
                "family",
                f"reviewers from {len(families)} families ({', '.join(sorted(families))}) "
                f"reviewed, so one is outside the author's single unknown family",
            )
        return "none", why
    # `router_set` returns its members stripped and lowercased, so the router side is
    # case-blind too: `["Anthropic"]` against a reviewer resolved to 'anthropic' must
    # overlap, or a reviewer from inside the set would pass as independent.
    author_set = routed if routed is not None else [author_fam]
    author_desc = author_fam if routed is None else f"{author_model} ({', '.join(author_set)})"
    if why := _no_reviewer_refusal(fams, anonymous, unapproved, author_desc):
        return "none", why
    different = [(g, f) for g, f in fams if f not in author_set]
    if different:
        return "family", f"{different[0][0]} was {different[0][1]} vs author {author_desc}"
    if routed is not None:
        return "none", (
            f"every identified reviewer was inside the families the router "
            f"{author_model!r} draws on: "
            + ", ".join(f"{g}={f}" for g, f in fams)
            + f" overlaps [agent].routers ({', '.join(author_set)}). A reviewer from a "
            f"family the router used may be reviewing its own work."
            + (f" ({', '.join(anonymous)} named no model at all.)" if anonymous else "")
        )
    if level == "model" and (m := model_tier_of(state, cfg, item_id, author_model, gates, fams)):
        return "model", (
            f"reviewed by {m}, a different model of the author's family {author_fam!r} "
            f"(tier model). For a review from another family, configure a reviewer of "
            f"one and run `ddflow review --reviewer <name>`."
        )
    return "none", (
        f"every identified reviewer ({', '.join(g for g, _ in fams)}) was family "
        f"{author_fam!r}, the same as the author. Same-family agreement is not "
        f"independent evidence."
        + (f" ({', '.join(anonymous)} named no model at all.)" if anonymous else "")
    )


#: ddflow's own state (the event log its heartbeats append to, run logs) changes while a
#: reviewer runs in a checkout that holds it; it is not the tool's doing.
_OURS = STATE_EXCLUDE


def git_state(where: Path | str) -> dict[str, str] | None:
    """What a reviewer tool must leave as it found it: HEAD, the index and working
    tree of ``where`` (bug B5ce30dd94d).

    Only what belongs to THIS tree. The stash list is shared by every worktree, so a
    sibling lane's `git stash` moves it mid-review and is no reviewer's doing
    (B708761957b); a reviewer's `git stash apply|pop` or `stash push` still shows in the
    working tree and index, which is where it does its harm. Accepted gap: a bare
    `stash drop|clear`, or a pop followed by a reset, leaves nothing here to see. Each part is a digest, so
    the evidence stays small.
    None when git could not answer: "could not tell" is not "unchanged"."""
    parts = {
        "head": ("rev-parse", "HEAD"),
        # The branch HEAD is on (or "HEAD" when detached): a checkout of the same commit
        # moves it without moving the sha.
        "ref": ("rev-parse", "--symbolic-full-name", "HEAD"),
        "status": ("status", "--porcelain=v2", "--untracked-files=all", "--", ".", *_OURS),
        "diff": ("diff", "HEAD", "--binary", "--", ".", *_OURS),
    }
    # From the repository top, whatever directory the reviewer was started in: a pathspec
    # of `.` would narrow every query to that directory.
    top = GIT.run(where, "rev-parse", "--show-toplevel")
    if not top.ok or not top.out:
        return None
    where = Path(top.out)
    state: dict[str, str] = {}
    for name, args in parts.items():
        r = GIT.run(where, *args, binary=True)
        if not r.ok:
            return None
        state[name] = content_digest(r.out_bytes or b"", length=16)
    state["untracked"] = _untracked_content_digest(where)
    return state


#: A file larger than this is digested by its size and mtime, not its bytes.
_BIG_UNTRACKED = 256 << 20


def _file_digest(path: Path) -> str:
    """A file's content digest, read in pieces: an artifact may be hundreds of MiB."""
    h = hasher()
    with path.open("rb") as fh:
        while piece := fh.read(1 << 20):
            h.update(piece)
    return h.hexdigest()[:16]


#: The digest of a part git could not list; never compared as a value.
_UNREADABLE = "unreadable"


def _untracked_content_digest(where: Path | str) -> str:
    """A digest of the untracked (not ignored) files' CONTENTS: ``status`` lists their
    paths only and ``diff HEAD`` omits them, so a tool rewriting one would pass unseen."""
    r = GIT.run(
        where, "ls-files", "--others", "--exclude-standard", "-z", "--", ".", *_OURS, binary=True
    )
    names = r.paths() if r.ok else None
    if names is None:
        return _UNREADABLE
    h = []
    for name in sorted(names):
        path = Path(where) / name
        try:
            st = path.lstat()
            if path.is_symlink() or st.st_size > _BIG_UNTRACKED:
                h.append(f"{name}:{st.st_size}:{st.st_mtime_ns}")
            else:
                h.append(f"{name}:{_file_digest(path)}")
        except OSError:
            h.append(f"{name}:gone")
    return content_digest("\n".join(h), length=16)


def git_state_change(before: dict[str, str] | None, after: dict[str, str] | None) -> str:
    """ "" when the state is as it was (or could not be compared), else a sentence naming
    which parts changed. A state that was readable before and is not now is a change:
    the tool broke the repository. An unreadable START cannot be compared, and a part
    (the untracked listing) that either snapshot could not read is left out: "could not
    list" is not "changed" (B049f8ce85d), at the cost of not watching that part then."""
    if before is None or before == after:
        return ""
    if after is None:
        changed = "unreadable afterwards"
    else:
        # A part one snapshot could not read (a listing that failed or timed out under
        # load, B049f8ce85d) says nothing about a change: it is left out.
        changed = ", ".join(
            k
            for k in before
            if before[k] != after.get(k) and _UNREADABLE not in (before[k], after.get(k))
        )
        if not changed:
            return ""
    return (
        f"the reviewer tool changed git state ({changed}) -- a review must leave HEAD, "
        f"the index and the working tree as it found them (e.g. `git stash "
        f"apply` or `pop`). This is NOT a clean review; nothing was restored, so check "
        f"the tree and `git stash list` before going on, and run the reviewer against a "
        f"clean throwaway worktree of the reviewed sha."
    )


def run_watching_git(
    gdef: GateDef, cwd: Path, run: Callable[[], tuple[str, dict[str, Any]]]
) -> tuple[str, dict[str, Any]]:
    """``run()`` -- a command gate's execution -- with a reviewer gate's git state checked
    around it. A reviewer tool (roborev, kilo) that moved HEAD, the index, the working
    tree reviewed nothing trustworthy: the gate is ``unavailable`` and
    the evidence says what moved. Nothing is restored -- ddflow cannot prove which change
    was the tool's. Any other gate is run as it is (a test suite may write the tree)."""
    watch = is_reviewer_gate(gdef.id, gdef) and gdef.is_command_gate and cwd.exists()
    before = git_state(cwd) if watch else None
    outcome, ev = run()
    after = git_state(cwd) if watch else None
    moved = git_state_change(before, after)
    if moved:
        return "unavailable", {
            **ev,
            "reason": moved,
            "git_state_changed": {"before": before, "after": after},
        }
    return outcome, ev
