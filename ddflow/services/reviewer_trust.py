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
  person runs `ddflow reviewers approve <name>`, which appends `reviewer.approved`.

An entry no tool ever wrote has no `reviewer.configured` event, so it is the operator's
and counts exactly as before: nothing configured by hand stops counting when this lands.
Like human gates, this makes tampering VISIBLE; it does not stop a shell edit of
`.ddflow/local/*.toml`.
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..core.events import canonical
from ..infra import tomlcfg as TC

#: The fields that decide WHO is reviewing and what it is called. A change to any of
#: them is a different reviewer; a change to a tuning knob (max_tokens, hedge, gates)
#: is not, so an operator's entry does not need re-approval when an agent tunes it.
IDENTITY = ("name", "kind", "base_url", "model", "family", "command")

#: Environment variables an agent harness sets in the shells it runs, so a command it
#: runs is known not to come from a person at their own terminal.
HARNESS_MARKERS = ("CLAUDECODE",)


class ReviewerRefused(ValueError):
    """A tool tried to write a command reviewer without a person behind it."""


def digest(rev: Any) -> str:
    """A stable fingerprint of a reviewer's identity fields."""
    body = {k: str(getattr(rev, k, "") or "") for k in IDENTITY}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()[:16]


def snapshot(repo: Path) -> dict[str, tuple[str, str]]:
    """name -> (digest, kind) for every reviewer configured now; {} if none load."""
    from .review import load_reviewers

    try:
        return {r.name: (digest(r), r.kind) for r in load_reviewers(Path(repo))}
    except Exception:  # a broken file is reported by whatever reads it next
        return {}


def digest_of(repo: Path, name: str) -> str:
    """The current digest of reviewer ``name``, or ``""``."""
    return snapshot(repo).get(name, ("", ""))[0]


def agent_marker(requested_agent: str = "") -> str:
    """Why this invocation is an agent's, or ``""`` when nothing says it is.

    An explicit `--agent`, `DDFLOW_AGENT`, or a harness's own marker. A person at their
    own terminal sets none of them; an agent that unsets all three is the shell edit the
    decision accepts it cannot stop.
    """
    if requested_agent:
        return f"--agent {requested_agent}"
    if os.environ.get("DDFLOW_AGENT"):
        return f"DDFLOW_AGENT={os.environ['DDFLOW_AGENT']}"
    for var in HARNESS_MARKERS:
        if os.environ.get(var):
            return f"{var} is set (an agent harness's shell)"
    return ""


def _files(repo: Path) -> tuple[Path, ...]:
    return TC.config_paths(Path(repo), "reviewers.toml")


def _log(repo: Path, agent: str):
    from ..config import Config
    from ..infra.log import EventLog, resolve_agent_id

    cfg = Config.load(repo)
    who, _layer = resolve_agent_id(repo, cfg, agent)
    return EventLog(repo, who, lock_timeout_s=cfg.lease.acquire_timeout_s, log_cfg=cfg.log)


@contextlib.contextmanager
def guarded(repo: Path, *, person: bool = False, agent: str = "") -> Iterator[None]:
    """Wrap one tool write of the config: refuse a command reviewer, record the rest.

    Judged on the EFFECTIVE reviewers before and after, not on the text written: a
    block can change an entry defined in another layer, and the overlay merges by name.
    A refused write is undone -- every reviewer file is put back as it was.
    """
    repo = Path(repo)
    before = snapshot(repo)
    saved = {p: (p.read_bytes() if p.exists() else None) for p in _files(repo)}
    yield
    after = snapshot(repo)
    changed = {n: v for n, v in after.items() if before.get(n) != v}
    if not changed:
        return
    commands = sorted(n for n, (_d, kind) in changed.items() if kind == "command")
    if commands and not person:
        for p, data in saved.items():
            if data is None:
                p.unlink(missing_ok=True)
            else:
                TC.atomic_write(p, data.decode("utf-8"))
        raise ReviewerRefused(
            f"refusing to write the command reviewer(s) {', '.join(commands)}: a "
            f"kind='command' reviewer runs any program and can print any verdict, so only "
            f"a person adds one (decision D-reviewer-trust). Add it by editing "
            f".ddflow/local/reviewers.toml, or with `ddflow reviewers add` from your own "
            f"terminal. Nothing was written."
        )
    log = _log(repo, agent)
    for name, (dig, kind) in sorted(changed.items()):
        log.append(
            "reviewer.configured",
            name,
            {"digest": dig, "kind": kind, "person": bool(person), "user": _user()},
        )


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown-user"


def pending(repo: Path, st: Any) -> list[dict[str, Any]]:
    """Reviewers whose CURRENT entry a tool wrote and no person has approved."""
    out = []
    for name, (dig, kind) in sorted(snapshot(repo).items()):
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

    if why := agent_marker(requested_agent):
        raise ReviewerRefused(
            f"refusing: approving a reviewer is a person's act, and this command runs "
            f"under an agent identity ({why}). Run it from your own terminal."
        )
    revs = {r.name: r for r in load_reviewers(Path(repo))}
    rev = revs.get(name)
    if rev is None:
        known = ", ".join(sorted(revs)) or "none configured"
        raise ReviewerRefused(f"no reviewer named {name!r} ({known})")
    dig = digest(rev)
    who = _user()
    _log(Path(repo), "").append(
        "reviewer.approved",
        name,
        {
            "digest": dig,
            "user": who,
            "host": socket.gethostname().split(".")[0],
            "note": note,
            "human": True,
            **{k: str(getattr(rev, k, "") or "") for k in IDENTITY},
        },
    )
    where = rev.command if rev.kind == "command" else rev.base_url
    return (
        f"reviewer {name!r} approved by {who}: kind {rev.kind}, {where}, model "
        f"{rev.model or '-'}, family {rev.resolved_family() or 'unknown'} (digest {dig})"
    )
