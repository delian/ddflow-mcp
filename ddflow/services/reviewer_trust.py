"""Who wrote a reviewer entry, and whether a person has vouched for it (D-reviewer-trust).

Bug B3f9b8a4ac0: an agent appended a `[[reviewer]]` of kind `command` whose command
printed `STATUS: NO FINDINGS`, declared it family google, and `ddflow review` recorded a
cross-family critic pass. The operator's decision (option C):

* every tool write refuses a NEW or CHANGED `kind = "command"` reviewer unless the
  caller is a person -- a command reviewer runs anything and can print any verdict;
* every tool write that creates or changes a reviewer's identity appends
  `reviewer.configured` (who, and a digest of the entry);
* `gates.record` stamps that digest into the evidence `ddflow review` writes;
* reviewer independence does not count a review from a digest a tool wrote until a
  person runs `ddflow reviewers approve <name>`, which records an approval of that digest
  through the shared primitive (`services.approval`: `approval.granted`, subject
  ``reviewer:<name>``; a log from before it holds `reviewer.approved`, which still counts).

An entry no tool ever wrote has no `reviewer.configured` event, so it is the operator's
and counts exactly as before: nothing configured by hand stops counting when this lands.
Like human gates, this makes tampering VISIBLE; it does not stop a shell edit of
`.ddflow/local/*.toml`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.digest import content_digest
from ..core.events import canonical
from ..infra import tomlcfg as TC

# The person-only checks are the approval primitive's (B-uni-approval); re-exported so
# `reviewer_trust.agent_marker` and friends keep working.
from . import approval as AP
from . import identity as ID
from .approval import os_user as _user
from .identity import HARNESS_MARKERS, agent_marker  # noqa: F401

#: The fields that decide WHO is reviewing and what it is called. A change to any of
#: them is a different reviewer; a change to a tuning knob (max_tokens, hedge, gates)
#: is not, so an operator's entry does not need re-approval when an agent tunes it.
IDENTITY = ("name", "kind", "base_url", "model", "family", "command")


class ReviewerRefused(ValueError):
    """A tool tried to write a command reviewer without a person behind it."""

    exit_code = 3  # REFUSED, not bad arguments (`core.outcome.exit_for`, B5f3a650c40)


def digest(rev: Any) -> str:
    """A stable fingerprint of a reviewer's identity fields."""
    body = {k: str(getattr(rev, k, "") or "") for k in IDENTITY}
    return content_digest(canonical(body), length=16)


def snapshot(repo: Path) -> dict[str, tuple[str, str]] | None:
    """name -> (digest, kind) for every reviewer configured now; None if they do not load."""
    from .review import load_reviewers

    try:
        return {r.name: (digest(r), r.kind) for r in load_reviewers(Path(repo))}
    except Exception:
        return None


def digest_of(repo: Path, name: str) -> str:
    """The current digest of reviewer ``name``, or ``""``."""
    return (snapshot(repo) or {}).get(name, ("", ""))[0]


def _files(repo: Path) -> tuple[Path, ...]:
    return TC.config_paths(Path(repo), "reviewers.toml")


def _log(repo: Path, agent: str):
    from ..config import Config

    return ID.open_log(repo, Config.load(repo), agent)[0]


def write(repo: Path, path: Path, text: str, *, person: bool = False, agent: str = "") -> None:
    """Write one config file as a tool: refuse a command reviewer, record the rest.

    Judged on the EFFECTIVE reviewers before and after, not on the text written: a
    block can change an entry defined in another layer, and the overlay merges by name.
    A refused write is undone -- ``path`` is put back as it was (only ``path``: the
    caller holds its lock and no other file was touched). The write itself is atomic,
    so it either lands whole or not at all; nothing can persist half-way and skip the
    check (critic).

    Fails CLOSED when the reviewers cannot be read before or after: a guard that saw
    "no reviewers" whenever the files did not load would wave through exactly the write
    it exists to stop (roborev).
    """
    repo, path = Path(repo), Path(path)
    before = snapshot(repo)
    if before is None:
        raise ReviewerRefused(_unreadable("before"))
    saved = path.read_bytes() if path.exists() else None
    TC.atomic_write(path, text)
    after = snapshot(repo)
    changed = {} if after is None else {n: v for n, v in after.items() if before.get(n) != v}
    commands = sorted(n for n, (_d, kind) in changed.items() if kind == "command")

    def undo() -> None:
        if saved is None:
            path.unlink(missing_ok=True)
        else:
            TC.atomic_write(path, saved.decode("utf-8"))

    if after is None or (commands and not person):
        undo()
        if after is None:
            raise ReviewerRefused(_unreadable("after"))
        raise ReviewerRefused(
            f"refusing to write the command reviewer(s) {', '.join(commands)}: a "
            f"kind='command' reviewer runs any program and can print any verdict, so only "
            f"a person adds one (decision D-reviewer-trust). Add it by editing "
            f".ddflow/local/reviewers.toml, or with `ddflow reviewers add` from your own "
            f"terminal. Nothing was written."
        )
    if not changed:
        return
    # The record is part of the write: a tool-written reviewer with no
    # `reviewer.configured` would count as the operator's. If it cannot be recorded --
    # the event lock busy past its timeout, a config that fails to load -- the write is
    # undone (rubber duck, critic). An event already appended for a digest no longer in
    # effect is harmless: it can only withhold trust, never grant it.
    try:
        log = _log(repo, agent)
        for name, (dig, kind) in sorted(changed.items()):
            log.append(
                "reviewer.configured",
                name,
                {"digest": dig, "kind": kind, "person": bool(person), "user": _user()},
            )
    except Exception as exc:
        undo()
        raise ReviewerRefused(
            f"could not record who wrote reviewer(s) {', '.join(sorted(changed))} "
            f"({exc}), so the write was undone: an unrecorded tool-written reviewer would "
            f"count as the operator's (decision D-reviewer-trust). Try again."
        ) from exc


def _unreadable(when: str) -> str:
    return (
        f"refusing to write config: the [[reviewer]] entries do not load {when} this "
        f"write, so whether it adds or changes a reviewer cannot be checked "
        f"(decision D-reviewer-trust). Fix the reviewer files by hand "
        f"(`ddflow reviewers list` names the problem). Nothing was written."
    )


def pending(repo: Path, st: Any) -> list[dict[str, Any]]:
    """Reviewers whose CURRENT entry a tool wrote and no person has approved."""
    out = []
    for name, (dig, kind) in sorted((snapshot(repo) or {}).items()):
        w = st.reviewer_writes.get(dig)
        if w and dig not in st.reviewer_approvals:
            out.append({"name": name, "digest": dig, "kind": kind, **w})
    return out


def approve(repo: Path, name: str, *, requested_agent: str = "", note: str = "") -> str:
    """A PERSON vouches for reviewer ``name`` as it is configured now. Returns the line.

    Refused under any agent identity, as `ddflow approve` is a person's act: an agent
    that could approve the reviewer it wrote would make the whole record decorative.
    """
    from .review import load_reviewers

    if why := agent_marker(requested_agent):  # before anything is read: not an agent's to see
        raise ReviewerRefused(AP.refusal(why))
    revs = {r.name: r for r in load_reviewers(Path(repo))}
    rev = revs.get(name)
    if rev is None:
        known = ", ".join(sorted(revs)) or "none configured"
        raise ReviewerRefused(f"no reviewer named {name!r} ({known})")
    dig = digest(rev)
    try:
        granted = AP.grant(
            _log(Path(repo), ""),
            "reviewer:" + name,
            dig,
            note=note,
            requested_agent=requested_agent,
            extra={k: str(getattr(rev, k, "") or "") for k in IDENTITY},
        )
    except AP.ApprovalRefused as exc:
        raise ReviewerRefused(str(exc)) from exc
    who = granted.actor.user
    where = rev.command if rev.kind == "command" else rev.base_url
    return (
        f"reviewer {name!r} approved by {who}: kind {rev.kind}, {where}, model "
        f"{rev.model or '-'}, family {rev.resolved_family() or 'unknown'} (digest {dig})"
        + (f" -- {note}" if note else "")
    )
