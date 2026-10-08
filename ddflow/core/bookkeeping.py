"""Which paths are ddflow's own: one name per meaning (D-unify, B-uni-git-queries).

Four spellings of "ddflow's bookkeeping" had grown, each with its own meaning. They stay
different sets because they answer different questions; each is named once, here.

- `STATE_DIR` / `is_state` / `STATE_EXCLUDE`: the whole ``.ddflow/`` directory. What a
  gate's evidence, a doc scan and a test selection ignore, because recording the outcome
  writes an event into it (git pathspecs, so git does the matching).
- `EVENTS_EXCLUDE`: only the event shards, for a count of the commits that carry work.
- `SELF_MANAGED`: every path ddflow itself writes, wherever it lives; a commit touching
  only these needs no lease.
- `QUEUE_STATE`: the part of ``.ddflow/`` that is the queue's own record (event shards,
  the derived index, machine-local state). Config, rules and prompts under ``.ddflow/``
  are deliverables a task may own, so they are not in it.
"""

from __future__ import annotations

STATE_DIR = ".ddflow"

#: Git pathspecs that drop `.ddflow/` from a query.
STATE_EXCLUDE: tuple[str, ...] = (f":(exclude){STATE_DIR}", f":(exclude){STATE_DIR}/**")

#: Git pathspec dropping the event shards alone (`[log].commit_events` commits are no work).
EVENTS_EXCLUDE: tuple[str, ...] = (f":(exclude){STATE_DIR}/events",)

#: Paths ddflow's own bookkeeping writes. Requiring a lease for these would make it
#: impossible to commit the event log that records the lease.
SELF_MANAGED = (".ddflow/", "docs/ddflow/", "AGENTS.md", "CLAUDE.md", ".cursor/rules/")

#: The queue's own record: event shards, the derived index, machine-local state.
QUEUE_STATE = (".ddflow/events/", ".ddflow/local/", ".ddflow/index.db")


def is_state(path: str) -> bool:
    """Is ``path`` the `.ddflow/` directory or something inside it?"""
    return path == STATE_DIR or path.startswith(STATE_DIR + "/")
