"""The shape of a migration (decision D-compat, task B-uni-compat-migrations).

A migration is the declared, tested answer to one BREAKING change a release makes to
something a project already holds: a file format, a marker grammar, a reference, a stored
shape. It is `{id, since_version, format_level, kinds}` plus four functions:

- ``detect(ctx)``  pure: reads the project, returns findings (nothing when it is already
  migrated -- the idempotence test);
- ``plan(ctx, findings)``  the dry-run: the changes `apply` would make, each naming the file
  it rewrites, so the runner can back those files up first and `upgrade --plan` can show them;
- ``apply(ctx, findings)``  makes the changes (files only through `infra.fsio`) and returns
  CORRECTIVE events to append -- the log is append-only, a migration never edits a line.
  It raises `Unavailable` only BEFORE it writes anything (the runner reports `unavailable`);
  once it has written, a failure is an OSError or ValueError (reported `failed`);
- ``verify(ctx)``  after apply: problems that remain, empty when the project is as the new
  release expects it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..repairs.base import Context, Corrective, Unavailable, context

#: Who may apply a migration, as for a repair: `agent` unasked, `operator` only when named
#: (it rewrites something a person may have written).
AGENT = "agent"
OPERATOR = "operator"

#: The kinds of artifact a migration may declare it touches. A migration naming another kind
#: is refused at registration: the plan groups, backs up and reports by kind.
KINDS = frozenset(
    {
        "log",  # the event log (corrective events only)
        "config",  # .ddflow/config.toml and its layers
        "rules",  # rules files and their generated views
        "docs",  # generated and managed documents, driver docs, instruction blocks
        "hooks",  # git and harness hooks, MCP launch entries
        "references",  # command and tool names written into project files
        "local",  # machine-local stores under .ddflow/local
    }
)


@dataclass(frozen=True)
class Finding:
    #: Stable across runs for the same thing to migrate.
    key: str
    #: One line for a human: what is to be migrated and where.
    detail: str
    #: The repo-relative file it lives in, "" for the log or nowhere in particular.
    path: str = ""


@dataclass(frozen=True)
class Change:
    """One step of a plan: ``path`` (repo-relative, or absolute for a file outside the
    project) is rewritten, created or removed; ``action`` says how, in a line."""

    path: str
    action: str


@dataclass(frozen=True)
class Migration:
    id: str
    #: The first release that makes the breaking change; a project older than it holds the
    #: old shape. A migration newer than the running ddflow is not offered.
    since_version: str
    #: The FORMAT_LEVEL the migrated shape belongs to (what an older ddflow cannot read).
    format_level: int
    #: Which artifact kinds it touches (a subset of `KINDS`).
    kinds: tuple[str, ...]
    title: str
    #: What applying it does, in one line.
    action: str
    detect: Callable[[Context], list[Finding]]
    plan: Callable[[Context, list[Finding]], list[Change]]
    apply: Callable[[Context, list[Finding]], list[Corrective]]
    verify: Callable[[Context], list[str]]
    consent: str = AGENT

    def data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "since_version": self.since_version,
            "format_level": self.format_level,
            "kinds": list(self.kinds),
            "title": self.title,
            "action": self.action,
            "consent": self.consent,
        }


__all__ = ["Context", "Corrective", "Unavailable", "context"]
