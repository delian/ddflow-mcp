"""Reviewer independence: the family of each reviewer and whether one differs from the author."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.bookkeeping import STATE_EXCLUDE
from ...core.digest import content_digest, hasher
from ...core.model import State
from ...infra import git as GIT
from .defs import DEFAULT_GATES, GateDef


def family_of(model: str, cfg: Config) -> str:
    """This project's view of a model's family: `[agent].families`, which defaults to
    the shipped map. ``""`` means "not recognised" — see `config.family_for`."""
    from ...config import family_for

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
        return str(evidence["reviewer"])
    return ""


#: The built-in gates whose recorded model counts toward reviewer independence. A gate
#: whose definition declares `reviewer = "different_family"` counts too (`reviewer_gates`).
#: `standards` is here without declaring it: roborev records the model that reviewed.
REVIEWER_GATES = ("rubber_duck", "critic", "standards")


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


def reviewer_independence(
    state: State,
    cfg: Config,
    item_id: str,
    author_model: str,
    gates: Mapping[str, GateDef] | None = None,
) -> tuple[bool, str]:
    """Did at least one reviewer come from a different pretraining family?

    Returns (satisfied, explanation). A same-family panel is reported as unsatisfied
    even when every reviewer passed, because agreement among models trained on the same
    distribution measures shared priors, not correctness.
    """
    from ...config import router_set

    it = state.items.get(item_id)
    if not it:
        return False, f"no such item {item_id}"
    # A router author (Copilot's HydraFusion) is a SET of families: any of them may
    # have written the diff, so a reviewer must sit outside all of them.
    routed = router_set(author_model, cfg.agent.routers)
    # Compared case-blind on BOTH sides: a map value 'Alibaba' and a declared
    # 'ALIBABA' are one family (B-fam-case).
    author_fam = family_of(author_model, cfg).strip().lower() if routed is None else ""
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
        fam = _declared_family(rec.evidence) or family_of(m, cfg).strip().lower()
        # An UNIDENTIFIED reviewer cannot establish independence — see `family_of`.
        if not fam:
            anonymous.append(f"{gname}={m or 'no model'}")
            continue
        fams.append((gname, fam))
    if routed == []:
        return False, (
            f"the author's model {author_model!r} is a router in [agent].routers with "
            f"no families listed, so no reviewer can be shown to sit outside the models "
            f"it drew on. List every family your plan routes it to, e.g. "
            f'routers = {{ hydrafusion = ["anthropic", "openai", "google"] }}.'
        )
    if not author_model.strip():
        # Quoting `''` back at the agent named nothing it could act on (B7a5c63e3d2).
        return False, (
            "the author's model is unknown, so no reviewer can be shown to differ from "
            "it: pass `--model <author model>` (`model` over MCP), or declare it once "
            "with `ddflow session start --model <author model>` under the same identity."
        )
    if routed is None and not author_fam:
        return False, (
            f"the author's model {author_model!r} is not in [agent].families, so no "
            f"reviewer can be shown to differ from it. Add it to the map (or, for a "
            f"model that routes across providers, to [agent].routers with the families "
            f"it draws on), or pass `--model` with a name the map recognises."
        )
    # `router_set` returns its members stripped and lowercased, so the router side is
    # case-blind too: `["Anthropic"]` against a reviewer resolved to 'anthropic' must
    # overlap, or a reviewer from inside the set would pass as independent.
    author_set = routed if routed is not None else [author_fam]
    author_desc = author_fam if routed is None else f"{author_model} ({', '.join(author_set)})"
    if unapproved and not fams:
        # The names as recorded, never re-parsed out of the text: a name with a space
        # in it would print an approve command for the wrong reviewer (rubber duck).
        names = sorted({who for _g, who in unapproved})
        return False, (
            f"{'; '.join(f'{g} came from reviewer {w!r}' for g, w in unapproved)}, whose entry a tool wrote and a person has "
            f"not approved, so it is not counted (decision D-reviewer-trust): an agent can "
            f"write a reviewer, so it cannot vouch for one. The operator reviews the "
            f"entry and runs "
            + " and ".join(f"`ddflow reviewers approve {shlex.quote(n)}`" for n in names)
            + " from their own terminal."
        )
    if not fams:
        if anonymous:
            return False, (
                f"no reviewer named a model this project recognises "
                f"({', '.join(anonymous)}), so nothing shows the review came from a "
                f"different family than the author ({author_desc}). Re-record with "
                f"`--model <the reviewer's model>`, or teach [agent].families the name."
            )
        return False, "no reviewer ran at all"
    different = [(g, f) for g, f in fams if f not in author_set]
    if different:
        return True, f"{different[0][0]} was {different[0][1]} vs author {author_desc}"
    if routed is not None:
        return False, (
            f"every identified reviewer was inside the families the router "
            f"{author_model!r} draws on: "
            + ", ".join(f"{g}={f}" for g, f in fams)
            + f" overlaps [agent].routers ({', '.join(author_set)}). A reviewer from a "
            f"family the router used may be reviewing its own work."
            + (f" ({', '.join(anonymous)} named no model at all.)" if anonymous else "")
        )
    return False, (
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
    tree, and the stash list (bug B5ce30dd94d).

    The stash list is shared by every worktree of the repository, so a reviewer that
    runs ``git stash apply|pop`` rewrites the working tree of whoever ran it and
    consumes another lane's work. Each part is a digest, so the evidence stays small.
    None when git could not answer: "could not tell" is not "unchanged"."""
    parts = {
        "head": ("rev-parse", "HEAD"),
        # The branch HEAD is on (or "HEAD" when detached): a checkout of the same commit
        # moves it without moving the sha.
        "ref": ("rev-parse", "--symbolic-full-name", "HEAD"),
        "status": ("status", "--porcelain=v2", "--untracked-files=all", "--", ".", *_OURS),
        "diff": ("diff", "HEAD", "--binary", "--", ".", *_OURS),
        "stash": ("stash", "list", "--format=%H %gs"),
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
    the tool broke the repository. Only an unreadable START cannot be compared."""
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
        f"the index, the working tree and the stash list as it found them (e.g. `git stash "
        f"apply` or `pop`). This is NOT a clean review; nothing was restored, so check "
        f"the tree and `git stash list` before going on, and run the reviewer against a "
        f"clean throwaway worktree of the reviewed sha."
    )


def run_watching_git(
    gdef: GateDef, cwd: Path, run: Callable[[], tuple[str, dict[str, Any]]]
) -> tuple[str, dict[str, Any]]:
    """``run()`` -- a command gate's execution -- with a reviewer gate's git state checked
    around it. A reviewer tool (roborev, kilo) that moved HEAD, the index, the working
    tree or the stash list reviewed nothing trustworthy: the gate is ``unavailable`` and
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
