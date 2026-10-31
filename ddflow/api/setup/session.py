"""Session start, the commit-msg/probe hooks, and the MCP handshake helpers.

One family of operator-facing one-shot work: the SessionStart report, the hooks that are
checked or run around a commit, and the per-process start/notice caches the MCP handshake
reads.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import outcome as O
from ...core.model import fold
from ...services import enforce as E
from ...infra import git as G
from ...infra import worktree as W
from ...infra.log import EventLog
from ...services import companions as CO
from ...services import compaction as CP
from ...services import identity as ID
from ...services import upgrade_check as UC
from ...services import upgrade_notice as UN
from ...services import upgrade_start as US
from .._base import _load
from ..lifecycle import brief
from ..operations import cadence, external_sync


def _worktree_drift(repo: Path, here: Path) -> str:
    """A warning when `here` is a worktree behind the base branch, else "".

    INFORMS, never refuses: this runs at session start, where the only useful thing is
    to say so. The source project's `check_worktree_sync.py --hook` did the same job; a
    rulebook changed on the base branch since this tree forked is the case that bit it.
    Only a renderer: the drift itself is `enforce.drift`, the one computation the commit
    gate (`enforce.check_drift`, which BLOCKS) reads too -- two copies of "behind" and
    "which rules" would disagree about the same tree.
    """

    # Outside any git tree there is no branch to be behind, and nothing to say.
    if not W.git(here, "rev-parse", "--show-toplevel").ok:
        return ""
    d = E.drift(repo, here)
    if d.behind is None:
        return f"Could not tell whether this checkout is behind `{d.base}`: {d.detail}"
    if d.behind == 0:
        return ""
    msg = f"This worktree is {d.behind} commit(s) behind `{d.base}`: `git merge {d.base}` before starting."
    if d.rules:
        msg += f" The rules changed there: {', '.join(d.rules)} -- re-read them after merging."
    return msg


def _check_msg(repo: Path, cfg, msg_file: str) -> O.Outcome:
    """The commit-msg hook's check: the trailer, read from the message being committed.

    `repo` is the primary checkout, resolved from wherever git runs the hook exactly as
    for `check-commit`, so a commit in a linked worktree is checked against the one
    queue every worktree shares.
    """

    data: dict[str, Any] = {"message": "", "installed": True, "policy": ""}
    forbidden = list(cfg.enforce.forbidden_trailers)
    if not cfg.enforce.require_item_trailer and not forbidden:
        return O.ok("hooks", **data)
    path = Path(msg_file)
    if not msg_file or not path.is_file():
        # git always passes the file; no file means we were not called by git, and
        # inventing a failure from missing input is the vacuous-FAIL mirror.
        return O.ok("hooks", **data)
    text = path.read_text("utf-8", errors="replace")
    # Before the merge exemption, on purpose: a merge message is written by whoever
    # concludes the merge, which is exactly who adds an attribution line.
    code, msg = E.check_forbidden_trailers(text, forbidden)
    if code:
        data["message"] = msg
        return O.Outcome(kind="hooks", data=data, exit=code, reason=msg)
    if not cfg.enforce.require_item_trailer:
        return O.ok("hooks", **data)
    # A merge really in progress: MERGE_HEAD resolves AND is not already contained in
    # HEAD. A stale or planted MERGE_HEAD pointing at HEAD exempted every ordinary
    # commit from the trailer rule (rubber-duck).
    head = G.run(".", "rev-parse", "-q", "--verify", "MERGE_HEAD", timeout=G.PROBE_TIMEOUT)
    merging = False
    if head.ok:
        # A probe git could not answer is not "not an ancestor": it would exempt the commit.
        ancestor = G.run(
            ".", "merge-base", "--is-ancestor", head.out, "HEAD", timeout=G.PROBE_TIMEOUT
        )
        merging = not ancestor.unavailable and ancestor.code != 0
    code, msg = E.check_item_trailer(
        text,
        list(cfg.enforce.item_trailer_keys) or ["Item"],
        ids=lambda: E.queue_ids(repo, cfg),
        waivers=dict(cfg.enforce.trailer_waivers),
        merging=merging,
    )
    data["message"] = msg
    if code == 0:
        return O.ok("hooks", **data)
    return O.Outcome(kind="hooks", data=data, exit=code, reason=msg)


def _record_compaction(repo: Path, stdin: str, agent: str) -> O.Outcome:
    """What the PreCompact hook does (B195): record the transcript's last turns as a
    session note. NEVER blocks or fails a compaction -- the exit is always 0 and nothing
    is printed (a `decision` on stdout would block it)."""

    try:
        payload = json.loads(stdin) if stdin.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("the hook's stdin is not a JSON object")
        cfg = Config.load(repo)
        log, who = ID.open_log(
            repo, cfg, agent, lock_timeout_s=min(cfg.lease.acquire_timeout_s, 3.0)
        )
        st = fold(log.read_all(), strict=False)
        held = sorted(i.id for i in st.items.values() if i.lease and i.lease.holder == who.id)
        result = CP.record(log, cfg, payload, held, set(st.sessions))
        return O.ok("hooks", message="", result=result)
    except Exception as exc:  # a hook must never stand between the operator and /compact
        return O.ok("hooks", message="", result="skipped", why=f"{type(exc).__name__}: {exc}")


def _after_compaction(repo: Path, stdin: str) -> list[str]:
    """The SessionStart lines for `source: compact`: the digest PreCompact recorded, or
    a request to write one when none landed. [] for any other start or any problem."""

    try:
        payload = json.loads(stdin) if stdin.strip() else {}
    except ValueError:
        return []
    if not isinstance(payload, dict) or payload.get("source") != "compact":
        return []

    cfg = Config.load(repo)
    if cfg.session.compaction_digest_chars <= 0:
        return []  # turned off on purpose: nothing was meant to land, so nothing to ask
    log = EventLog(repo, log_cfg=cfg.log)
    text = CP.latest(log, str(payload.get("session_id") or ""))
    if text:
        return ["## Before this compaction", "", text, ""]
    return [
        "## Before this compaction",
        "",
        "No record of what this session was doing reached ddflow. Write one now: "
        '`ddflow session note --text "<what you were doing, what is next>"`. '
        "To have it recorded automatically at every compaction: "
        "`ddflow hooks install --claude` (installs the PreCompact hook).",
        "",
    ]


#: The static text the SessionStart hook prints around the brief (LS-charter): a heading and
#: the contract in a few lines, <= 1K chars together (tests/test_charter_contract.py). The hooks,
#: leases and gate refusals enforce it; the text only says what they will refuse.
SESSION_START_HEADER = "# ddflow session start\n"
SESSION_START_FOOTER = (
    "_Contract: `ddflow_brief` first; claim an item (`ddflow_claim`) before you edit; ask the "
    "operator when something is missing or ambiguous, never guess; a tool or reviewer that could "
    "not run is `unavailable`, never `passed`. A subagent passes `as_agent` on every call. More: "
    "`ddflow_recall`, `ddflow_help`, `ddflow_rule_show id=<id>`; `ddflow_memory_add` records a "
    "fact about this machine._"
)


def _sync_sections(repo: Path, agent: str) -> list[str]:
    """The session-start lines the syncing work adds: sibling-dependency observations and
    the periodic passes due now. Both can WRITE (`external.observed` on an observed
    change), which is why a dry run does not call this."""

    parts: list[str] = []
    try:
        # Work waiting on a sibling repository becomes ready the moment its dependency
        # is observed done, and session start is when an agent decides what to take.

        ext = external_sync(repo, agent=agent)
        changed = [o for o in ext.data.get("observed", []) if o["changed"]]
        if changed:
            parts += [
                "Observed in sibling repositories: "
                + ", ".join(f"{o['dep']} is {o['state']}" for o in changed),
                "",
            ]
        if ext.exit == O.FAIL:
            parts += [f"_(external dependencies not observed: {ext.reason})_", ""]
    except Exception as exc:
        parts += [f"_(external sync failed: {exc})_", ""]
    try:
        due = cadence(repo, agent=agent)
        if due.exit == O.FAIL:
            parts += [f"_(cadence check failed: {due.reason})_", ""]
        elif due.exit == O.OK and due.data.get("due"):
            parts += [
                "Periodic passes DUE: "
                + ", ".join(f"{d['cadence']} (last: {d['since']})" for d in due.data["due"])
                + ". Record each with `ddflow cadence --ran <name>` when done.",
                "",
            ]
    except Exception as exc:
        parts += [f"_(cadence check failed: {exc})_", ""]
    return parts


def _session_start(repo: Path, agent: str, stdin: str = "", *, dry_run: bool = False) -> O.Outcome:
    """What the Claude Code SessionStart hook prints. ALWAYS exit 0.

    A hook that fails at session start blocks nothing useful and teaches the operator to
    remove it. Every failure becomes one line of text saying what to run instead.

    `dry_run` probes the reply without the syncing sections whose observation writes
    `external.observed` events: the brief, drift warning and companion lines are what a
    hook --dry-run must still show, and the committed log must stay untouched.
    """

    parts = [SESSION_START_HEADER]
    try:
        parts += _after_compaction(repo, stdin)
    except Exception as exc:  # never fail a session start
        parts += [f"_(compaction record unavailable: {exc})_", ""]
    try:
        drift = _worktree_drift(repo, Path.cwd())
        if drift:
            parts += [f"**{drift}**", ""]
    except Exception as exc:
        parts += [f"_(worktree drift check failed: {exc})_", ""]
    if not dry_run:
        parts += _sync_sections(repo, agent)
    try:
        out = brief(repo, check_recovery=True, agent=agent, dry_run=dry_run)
        parts.append(out.data.get("text", "") or out.reason)
    except Exception as exc:
        parts.append(f"ddflow could not build the brief ({exc}). Run `ddflow doctor`.")
    parts += _companion_lines(repo)
    parts += [
        "",
        SESSION_START_FOOTER,
    ]
    return O.ok("hooks", message="\n".join(parts), installed=True, policy="")


def upgrade_notice(repo: Path, *, agent: str = "") -> str:
    """The one-line upgrade notice (`services.upgrade_notice.line`) or "": said once per version on this
    machine, whichever surface asks first. For the MCP handshake; the brief says it itself.
    Nothing in a directory ddflow was never set up in: a handshake must not adopt it."""
    if not (repo / ".ddflow").is_dir():
        return ""
    log, cfg, st = _load(repo, agent)
    return UN.line(repo, log, cfg, st, agent=agent)


def release_proposal(repo: Path, *, agent: str = "") -> str:
    """The one-line proposal to upgrade ddflow (`services.upgrade_check.proposal`) or "": said
    once per version on this machine, from the cache alone (a handshake must be quick; the
    brief and `doctor` are what ask the index). Nothing in a directory ddflow was never set
    up in."""
    if not (repo / ".ddflow").is_dir():
        return ""
    _log, cfg, _st = _load(repo, agent)
    return UC.proposal(repo, cfg, consume=True, refresh=False)


#: The start report per project, for the life of this process: a handshake (`initialize`,
#: `server/discover`) can be asked for more than once, the start check is made once.
_START_REPORTS: dict[str, str] = {}
_START_LOCK = threading.Lock()  #: two handshakes at once make one start check, not two


def upgrade_start(repo: Path, *, agent: str = "", surface: str = "") -> str:
    """The upgrade-at-start report (`services.upgrade_start.start`) as text, "" when there is
    nothing to say: for the MCP handshake, which asks once per process. It never raises and
    never adopts a directory ddflow was not set up in."""
    if not (repo / ".ddflow").is_dir():
        return ""
    key = str(Path(repo).resolve())
    with _START_LOCK:
        if key in _START_REPORTS:
            return _START_REPORTS[key]
        try:
            log, cfg, st = _load(repo, agent)
            text = US.start(repo, log, cfg, st, agent=agent, surf=surface)["text"]
        except Exception as exc:  # an unreadable log must not fail a start -- nor stick
            return f"the upgrade check at start failed ({exc}); serving anyway."
        _START_REPORTS[key] = text
        return text


def stale_server_note(repo: Path, *, agent: str = "") -> str:
    """``restart the server`` when a newer ddflow than the running one is installed, else "".
    (`services.upgrade_notice.stale_server_note`; the MCP server decides through
    `stale_server_facts`.)"""
    if not (repo / ".ddflow").is_dir():
        return ""
    _log, _cfg, st = _load(repo, agent)
    return UN.stale_server_note(st, installed=UN.installed_version())


def stale_server_facts(repo: Path, *, agent: str = "") -> dict[str, Any] | None:
    """The facts behind `stale_server_note` (running, newest, where, reexec), or None when the
    running ddflow is current or the project is not adopted."""
    if not (repo / ".ddflow").is_dir():
        return None
    _log, _cfg, st = _load(repo, agent)
    return UN.stale_facts(st, installed=UN.installed_version())


def _companion_lines(repo: Path) -> list[str]:
    """The brief's "Companions not wired up" section, for an agent on the hook path that
    never saw the MCP handshake. Same actionable set as the handshake, and like it
    NEVER probes: a session start must not wait on `npx`, so an unprobed companion says
    "not checked", which is not "missing"."""
    try:
        gaps = CO.actionable(CO.scan(repo, probe=False))
    except Exception as exc:
        return ["", f"_(companions not read: {exc}; run `ddflow companions`)_"]
    if not gaps:
        return []
    lines = ["", "## Companions not wired up", ""]
    for st, word in gaps:
        c = st.companion
        lines.append(
            f"- **{c.id}** - {c.title} [{word}]. Serves: "
            f"{', '.join(c.gates) or '-'}. Install: `{c.install}` - {c.url}"
        )
    lines += [
        "",
        'Nothing was probed at session start: "not checked" is not "missing". Tell the '
        "operator, then run `ddflow companions` to check and the `install-companions` "
        "prompt (MCP: `/mcp__ddflow__install-companions`) to install with their consent. "
        "A declined companion's gates are recorded `unavailable`, never passed.",
    ]
    return lines
