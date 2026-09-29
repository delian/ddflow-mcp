"""Quota profiles, as operations every surface calls (D-quotas).

Profiles are user-level (`services.quota.store_path`), so `repo` is accepted for the
surface convention and not read: a quota belongs to the account that pays, not to the
project asking.
"""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import quota as Q


def quota_declare(
    repo: Path,
    subject: str,
    *,
    windows: str = "",
    unlimited: bool = False,
    unknown: bool = False,
    by: str = "agent",
    note: str = "",
    agent: str = "",
) -> O.Outcome:
    """Declare what `subject` may spend. Exit 3 when an agent would overwrite the operator."""
    try:
        profile = Q.Profile(
            subject=subject,
            unlimited=unlimited,
            unknown=unknown,
            windows=Q.parse_windows(windows),
            declared_by=by,
            note=note,
        )
        stored, old = Q.declare(profile)
    except Q.OperatorOwned as exc:
        return O.refused("quota.declared", str(exc), subject=subject)
    except Q.QuotaError as exc:
        return O.failed("quota.declared", str(exc), subject=subject)
    return O.ok(
        "quota.declared",
        subject=subject,
        profile=stored.to_dict(),
        replaced=old.to_dict() if old else None,
        store=str(Q.store_path()),
    )


def quota_show(repo: Path, subject: str, *, agent: str = "") -> O.Outcome:
    """One subject's profile. Exit 2 when it has never been declared."""
    try:
        profiles = Q.load()
    except Q.QuotaError as exc:
        return O.failed("quota.show", str(exc), subject=subject)
    p = profiles.get(subject)
    if p is None:
        return O.nothing(
            "quota.show",
            f"no quota declared for {subject}: ask the agent, and the operator if it "
            f"cannot tell",
            subject=subject,
            profile=None,
        )
    return O.ok("quota.show", subject=subject, profile=p.to_dict())


def quota_list(repo: Path, *, agent: str = "") -> O.Outcome:
    """Every declared profile, grouped by state. An unreadable store is a failure."""
    try:
        s = Q.summary(Q.load())
    except Q.QuotaError as exc:
        return O.failed("quota.list", str(exc))
    return O.ok(
        "quota.list",
        store=str(Q.store_path()),
        limited=[p.to_dict() for p in s.limited],
        unlimited=[p.to_dict() for p in s.unlimited],
        unknown=[p.to_dict() for p in s.unknown],
    )


def quota_forget(repo: Path, subject: str, *, by: str = "agent", agent: str = "") -> O.Outcome:
    """Remove a profile so the subject is asked for again. Exit 2 if there was none,
    exit 3 when an agent would forget what the operator declared."""
    try:
        old = Q.forget(subject, by=by)
    except Q.OperatorOwned as exc:
        return O.refused("quota.forgotten", str(exc), subject=subject)
    except Q.QuotaError as exc:
        return O.failed("quota.forgotten", str(exc), subject=subject)
    if old is None:
        return O.nothing("quota.forgotten", f"no quota declared for {subject}", subject=subject)
    return O.ok("quota.forgotten", subject=subject, profile=old.to_dict())
