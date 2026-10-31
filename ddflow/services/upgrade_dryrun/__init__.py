"""The dry-run upgrade: rehearse upgrading a project on a throwaway clone.

Decisions D-dry-run-from-running-version and D-upgrade-on-mcp-connect, task
B-upgrade.12-dry-run. Before an operator upgrades a project, this proves on a COPY that going
from the ddflow they run now to the one on the tree is smooth. Nothing here touches the
committed state of the project it rehearses (a failed run may leave a git-ignored transcript
under its `.ddflow/local/`), the ddflow installed on the machine or an MCP server that is running:

- the project is only READ, by `git clone --local` of what is COMMITTED at one pinned commit
  (tried once more if it fails), and its log, snapshot tags and backups are compared before and
  after, so the report can say the rehearsal left no trace. Other agents work in the live
  project meanwhile (B-dryrun-flake): each shard is read once, into memory, checked by its
  size and mtime; a snapshot tag is the rehearsal's only if signed with its run id, a backup
  only if made while it ran for the versions it upgraded; a shard rewritten under the run is
  the result "live repo changed during rehearsal" (the run is repeated once, and if the
  project moved again the check is `unavailable`, never a pass);
- a run that fails, one that could not run (a clone or a build that failed; not an old ddflow
  that was never offered) or one whose live project moved writes its step table and every
  command's bounded stdout and stderr to `.ddflow/local/dryrun/<stamp>-<run id>.json` (git-ignored;
  `--diag-dir` or `DDFLOW_DRYRUN_DIAG_DIR` choose another; a directory that cannot be written
  falls back to a new one in the system temp directory) and names the file under the table;
  a run that passes writes nothing;
- every command runs in a scratch directory with a private HOME, no `DDFLOW_*` variables and no
  network (`DDFLOW_NO_UPDATE_CHECK`), against a clone;
- the NEW ddflow is this source tree, run as a subprocess with PYTHONPATH; the OLD one is a
  command the caller names (an installed release, or one built from the clone at the commit
  the installed ddflow came from) or, for tests, this same code declaring an older version.
  Without an old one the steps that need it report `unavailable`, never a pass.

The rehearsal, in the order a report lists it (the numbers are the task's):

1. clone the project at the commit the installed ddflow was built from;
2. read it with the new code (status, board, doctor, brief, history, replay, show) and digest
   the state it folds into;
3. `upgrade --plan`: every pending change, repair and migration; an item the operator would be
   asked to confirm is a FINDING (the preview names it);
4. `upgrade --apply --backup snapshot`: the digest of the user's records is unchanged, doctor is
   clean, a second apply is a no-op, `--restore` brings the files back and the shards keep
   every original byte;
5. the OLD ddflow reads the upgraded copy, and its write is refused with the remedy or lands
   without disturbing a byte;
6. the connect flow (D-upgrade-on-mcp-connect): the new MCP server over stdio on the old
   project proposes the upgrade, applies NOTHING until consent, then applies on consent;
7. two agents claim, work and release a task with the new version;
8. shards two agents wrote with the old version merge (`merge=union`) and fold identically;
9. the old log folds through one registered event upcaster (the machinery has never met a real
   bump), and an older reader skips the new payload version.

Every check is a row: pass, fail, or unavailable (it could not run). The exit is 0 only when
every row passes, 1 when any fails, 2 when none failed but one could not run.
"""

from __future__ import annotations

from ...config import Config
from ...core import clock
from ...core import digest as D
from ...core import outcome as O
from ...core import upcasters as U
from ...core import version as V
from ...core.events import Event, is_older
from ...core.model import State, fold
from ...infra import fsio
from ...infra import git as G
from ...infra import proc as P
from ...infra.log import EventLog
from .. import upgrade_start as US
from ..backups import SIDECAR_SUFFIX, SNAPSHOT_PREFIX
from ._cli import COULD_NOT_RUN, choose_commit, main, old_from, parser, tools_budget
from ._drive import _rehearse, checks, diag_directory, run_all, write_diagnostics
from ._model import (
    _SIMULATED_OLD,
    APPLYING,
    CLONE_RETRY_S,
    DIAG_ENV,
    FAIL,
    FORCED_TIMEOUT_S,
    HAND_EDIT,
    KEPT,
    LIVE_CHANGED,
    MAX_COMMANDS,
    PASS,
    READS,
    START_TIMED_OUT,
    START_TIMEOUT_S,
    STREAM_BOUND,
    TIMEOUT_S,
    UNAVAILABLE,
    UPGRADE_OWNED,
    Check,
    Cmd,
    CouldNotRun,
    Options,
    Report,
    bounded,
)
from ._read import (
    Footprint,
    Live,
    _fields,
    _of,
    _records,
    _stripped,
    backup_versions,
    events_of,
    folded_state,
    live_leaks,
    live_moved,
    new_cmd,
    prefixes_kept,
    previous_release,
    read_stable,
    registered_upcaster,
    release_commit,
    shard_bytes,
    shard_bytes_checked,
    simulated_old,
    snapshot_live,
    source_root,
    state_digests,
    tree_digest,
    upcast_problems,
    users_part,
)
from ._rehearsal import Rehearsal
from ._support import (
    _HELLO,
    _TOOLS_LIST,
    _asked,
    _call,
    _edit_kept,
    _first_phase,
    _items,
    _key,
    _pipeline,
    _tool_body,
    doctor_problems,
    new_problems,
    problem_identity,
    state_part_digest,
    tools_problems,
)

__all__ = [
    # the data model
    "APPLYING",
    "CLONE_RETRY_S",
    # the command line
    "COULD_NOT_RUN",
    "DIAG_ENV",
    "FAIL",
    "FORCED_TIMEOUT_S",
    "HAND_EDIT",
    "KEPT",
    "LIVE_CHANGED",
    "MAX_COMMANDS",
    "PASS",
    "READS",
    "SIDECAR_SUFFIX",
    "SNAPSHOT_PREFIX",
    "START_TIMED_OUT",
    "START_TIMEOUT_S",
    "STREAM_BOUND",
    "TIMEOUT_S",
    "UNAVAILABLE",
    "UPGRADE_OWNED",
    "US",
    # the shared judgement helpers and MCP wiring
    "_HELLO",
    "_SIMULATED_OLD",
    "_TOOLS_LIST",
    "Check",
    "Cmd",
    # the module aliases the single module exposed to its callers
    "Config",
    "CouldNotRun",
    "D",
    "Event",
    "EventLog",
    # the readers
    "Footprint",
    "G",
    "Live",
    "O",
    "Options",
    "P",
    # the rehearsal and its drive
    "Rehearsal",
    "Report",
    "State",
    "U",
    "V",
    "_asked",
    "_call",
    "_edit_kept",
    "_fields",
    "_first_phase",
    "_items",
    "_key",
    "_of",
    "_pipeline",
    "_records",
    "_rehearse",
    "_stripped",
    "_tool_body",
    "backup_versions",
    "bounded",
    "checks",
    "choose_commit",
    "clock",
    "diag_directory",
    "doctor_problems",
    "events_of",
    "fold",
    "folded_state",
    "fsio",
    "is_older",
    "live_leaks",
    "live_moved",
    "main",
    "new_cmd",
    "new_problems",
    "old_from",
    "parser",
    "prefixes_kept",
    "previous_release",
    "problem_identity",
    "read_stable",
    "registered_upcaster",
    "release_commit",
    "run_all",
    "shard_bytes",
    "shard_bytes_checked",
    "simulated_old",
    "snapshot_live",
    "source_root",
    "state_digests",
    "state_part_digest",
    "tools_budget",
    "tools_problems",
    "tree_digest",
    "upcast_problems",
    "users_part",
    "write_diagnostics",
]
