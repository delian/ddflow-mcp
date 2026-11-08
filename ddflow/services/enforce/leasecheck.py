"""The lease check and the commit-time gates that read a lease from the event log."""

from __future__ import annotations

import glob
import json
import os
import shlex
import time
from pathlib import Path

from ...config import Config
from ...core import clock
from ...core.admission import new_file_globs
from ...core.bookkeeping import SELF_MANAGED
from ...core.flow import env_chain
from ...core.globs import inside as path_in_glob
from ...core.model import Lease, fold
from ...core.outcome import FAIL, OK, Verdict
from ...core.schedule import is_shared, shared_globs
from ...infra import git as G
from ...infra import proc as P
from ...infra import worktree as W
from ...infra.log import EventLog
from .. import changes as CH
from .. import complexity_budget as CB
from .. import enforce_contested as EC
from .. import leases
from .. import pins_written as PW
from .. import upgrade_written as UW
from ..leaseclock import localize
from .hooks import MAX_LISTED_PATHS
from .staged import (
    _UNKNOWN_STAGED,
    _committing_tree,
    _index_blob,
    _index_tree,
    staged_bytes,
    staged_paths,
)


def _git_merge_temp(path: str) -> bool:
    """``git merge``'s own transient working-directory temp file, not work.

    A custom merge driver (`pinsregen`) makes git write the ancestor/ours/theirs
    blobs as ``.merge_file_XXXXXX`` temp files in the tree it is merging in; a
    concurrent ``git add -A`` stages them and then they vanish as git finishes,
    leaving an 'AD' index entry no lease covers (B-merge-driver-temp-staged). Down
    here it is refused as outside the lease, with a remedy to widen the globs to the
    temp names -- widening nothing real. Git cleans these files up itself, so they
    are never a commit the agent made and never belong to a lease.
    """
    return path.rsplit("/", 1)[-1].startswith(".merge_file_")


def _not_work(path: str) -> bool:
    """A staged path the lease check waves through: ddflow's own output, or git's
    transient merge temp file. Keeping one predicate here (rather than a second
    comprehension in `_check_lease`) holds `_check_lease` under the complexity
    budget `tests/test_architecture_guards.py` ratchets."""
    return any(path.startswith(prefix) for prefix in SELF_MANAGED) or _git_merge_temp(path)


def _made_tree(repo: Path, lease: Lease) -> Path | None:
    """The tree ``lease`` made, resolved; None for a lease with no tree."""
    return W.load_path(repo, lease.worktree).resolve() if lease.worktree else None


def _counts_as_mine(
    repo: Path, lease: Lease, me: str, here: Path | None, bound: bool = False
) -> bool:
    """A lease is "mine" if I hold it, OR if it created the very tree this commit is
    happening in. The second test is the robust one: the hook runs inside a worktree,
    and the lease that produced that worktree is the relevant claim no matter which
    process id or identity string made it.

    ``bound``: this commit's tree is some live lease's own. Then my lease bound to a
    DIFFERENT tree is that item's, not this commit's: counting it listed the other item's
    files under "You hold" and let them through from the wrong tree (Bc57d0b031a,
    B9929ecb6ea). A lease of mine with no tree of its own still covers it."""
    leased_tree = W.load_path(repo, lease.worktree) if lease.worktree else None
    if here and leased_tree and leased_tree.resolve() == here:
        return True
    return lease.holder == me and not (bound and leased_tree)


def _tree_bound(repo: Path, state, cfg: Config, here: Path | None, now: float) -> bool:
    """Is ``here`` the tree some lease created -- live or lapsed, finished item or not (its
    holder's other items are no more this tree's for either)?"""
    if here is None:
        return False
    held = [*state.active_leases(now, cfg.lease.grace_s).values()]
    held += state.expired_leases(now, cfg.lease.grace_s).values()
    return any(lz.worktree and W.load_path(repo, lz.worktree).resolve() == here for lz in held)


def _lapsed_lines(
    item_id: str, lease: Lease, now: float, me: str, *, allowed: bool = False
) -> list[str]:
    """What to say about a lapsed lease on this work: when it lapsed, and how to renew
    it -- as its holder, since a heartbeat from the tree will not revive an expired one.

    Matched by the tree alone (the holder is not the identity this hook resolved), the
    committer is USUALLY the holder under a derived name, but may be someone `recover`
    sent to take the abandoned work over. Both readings are offered rather than telling
    a newcomer to resurrect a claim its holder abandoned.
    """
    idle = clock.fmt_age(now - lease.renewed_at, "min")
    ago = clock.fmt_age(max(0, now - lease.renewed_at - lease.ttl_s), "min")
    ttl = clock.fmt_age(lease.ttl_s, "min")
    whose = "Your lease" if lease.holder == me else "The lease"
    lines = [
        f"{whose} on {item_id} (held by {lease.holder}) LAPSED {ago} ago: no",
        f"heartbeat for {idle}, past its {ttl} TTL, and nobody has taken",
        "it over.",
    ]
    if allowed:
        return [
            *lines,
            f"Renew it so later commits are not on a lapse: ddflow --agent {lease.holder} heartbeat {item_id}",
            "",
        ]
    if lease.holder == me:
        lines += ["Renew it, then commit again:"]
    else:
        lines += [f"This is that item's tree. If you are {lease.holder}, renew it, then commit:"]
    lines.append(f"    ddflow --agent {lease.holder} heartbeat {item_id}")
    if lease.holder != me:
        # The path `claim`'s own refusal names (`_claim_blocker`): a bare claim of an
        # expired lease is refused, since a crashed agent's tree often holds finished
        # work, and `--force` would also waive the dependency and overlap checks.
        lines += [
            f"If you are not {lease.holder}, the work was abandoned; take it over by:",
            f"    ddflow recover --item {item_id}    # what its tree holds; salvage it",
            f"    ddflow release {item_id} --note salvaged",
            f"    ddflow claim {item_id}",
        ]
    return [*lines, ""]


def _derived_identity_lines(me: str, held_by: dict[str, Lease]) -> list[str]:
    """Nobody said who is committing, so the identity was derived from this tree -- and
    "ANOTHER agent" may be the committer itself, working outside its item's tree under
    a name it declared only to its claim (an MCP `as_agent`, a `--agent`). The hook
    cannot tell (B9aeb141b9a); it says how the name was found and what the holder
    should do, rather than blaming it for its own lease.
    """
    lines = [
        f"Nothing declared who is committing, so this hook named you {me}, from this",
        "tree. If you ARE one of the holders above, you are outside its item's tree:",
    ]
    for item_id, lease in held_by.items():
        where = lease.worktree or "(no tree recorded)"
        lines += [
            f"  {lease.holder} on {item_id}: commit in {where},",
            f"    or declare yourself:  DDFLOW_AGENT={lease.holder} git commit ...",
        ]
    return [*lines, ""]


def _merged_in(tree: Path) -> str:
    """The one commit being merged into ``tree``'s HEAD, or "" when not exactly one.

    MERGE_HEAD where `git commit` concludes a merge, and at `git merge`'s commit-msg
    stage. At its pre-merge-commit stage MERGE_HEAD is not written yet, and git exports
    the merged commit as ``GITHEAD_<sha>`` instead (probed, git 2.43). An octopus merge
    names several, and gets no answer. A squash has neither: `ddflow merge` names the
    squashed commit in ``DDFLOW_SQUASH_OF`` for the commit it makes.

    All three are the committer's to set, like `--no-verify` is: this is a check on
    agents following the workflow, not a barrier (module docstring). Forging one buys
    nothing `--no-verify` does not, and the exactness test below still has to hold.
    """
    r = W.git(tree, "rev-parse", "--git-path", "MERGE_HEAD")
    if r.ok and r.out:
        f = Path(r.out) if Path(r.out).is_absolute() else tree / r.out
        try:
            heads = f.read_text().split()
        except OSError:
            heads = []
        if heads:
            return heads[0] if len(heads) == 1 else ""
    env = [k[len("GITHEAD_") :] for k in os.environ if k.startswith("GITHEAD_")]
    if env:
        return env[0] if len(env) == 1 else ""
    return os.environ.get(W.SQUASH_OF, "").strip()


def _squash_pending(tree: Path) -> bool:
    """A plain `git merge --squash` leaves SQUASH_MSG until the commit that concludes it:
    that commit lands a branch's work as one commit, which is a promotion by another name."""
    r = W.git(tree, "rev-parse", "--git-path", "SQUASH_MSG")
    if not (r.ok and r.out):
        return False
    return (Path(r.out) if Path(r.out).is_absolute() else tree / r.out).is_file()


def _merge_own_paths(tree: Path, paths: list[str]) -> list[str]:
    """The staged ``paths`` this commit is answerable for.

    A clean merge commit stages only what its parents already committed: none. A merge
    with conflicts resolved by hand stages every path the merged commit changed; those
    whose index entry IS the merged commit's came from there unchanged -- main's
    already-merged work, not this item's -- and are dropped (B07878037ab: 17 of them
    buried the one path that was the item's own). Outside a merge, or when git cannot
    say, ``paths`` unchanged: an unknown answer must not hide a path from the check.
    """
    if not paths or clean_merge_conclusion(tree):
        return []
    other = _merged_in(tree)
    if not other:
        return paths
    # `git_paths`, not `git`: a strict UTF-8 decode of `-z` output crashed on one
    # non-UTF-8 file name; it decodes as `staged_paths` does, so the names compare.
    differs = W.git_paths(tree, "diff", "--cached", "--no-renames", "--name-only", other)
    if differs is None:
        return paths
    return [p for p in paths if p in set(differs)]


def _commit_scope(tree: Path, paths: list[str], cfg: Config, *, lease: bool = False) -> list[str]:
    """The staged ``paths`` a check answers for: `_merge_own_paths` under
    `[enforce].merge_commit_scope = "own"`; every path under "all", save that the lease
    check still waves through git's own automatic merge (``lease``)."""
    if cfg.enforce.merge_commit_scope != "all":
        return _merge_own_paths(tree, paths)
    if lease and paths and clean_merge_conclusion(tree):
        return []
    return paths


def clean_merge_conclusion(tree: Path) -> bool:
    """Is the commit being made exactly the automatic merge of HEAD and one other commit?

    Then it adds nothing relative to its parents: every path it stages is already
    committed on one of them, and a lease check of the merge itself has nothing of its
    own to judge. (Whether the merged commits were made under a lease is their own
    commits' check -- or `--no-verify`'s, which this cannot see either.) Judging it
    against the committer's leases only ever went wrong: `ddflow merge` commits in the
    PRIMARY, whose derived identity holds nothing (B9b57176aac), from an event log the
    pre-commit framework may have stashed (Bc50bc7fb58), and the refusal left the
    primary mid-merge.

    Exact: the index must equal `git merge-tree --write-tree HEAD <it>`. A hand-resolved
    conflict, or anything added on top, differs and is checked as usual. A git too old
    for `merge-tree --write-tree` (< 2.38) answers False.
    """
    other = _merged_in(tree)
    if not other:
        return False
    auto = W.git(tree, "merge-tree", "--write-tree", "HEAD", other)
    if not auto.ok or not auto.out:
        return False  # conflicts (exit 1), or no --write-tree
    index = W.git(tree, "write-tree")
    return index.ok and index.out == auto.out.splitlines()[0]


def environment_branch_commit(repo: Path, cfg: Config) -> str:
    """Why this commit is refused as made directly on an environment branch, or "".

    GitLab flow's "upstream first": an environment branch ([flow].environments) only
    ever receives work by promotion, so a commit made on it is work the upstream branch
    never had, and the next promotion conflicts -- late. A merge or squash (what a
    promotion and `ddflow merge` commit) is not a direct commit and passes; so does a
    commit that stages nothing but ddflow's own files.
    """
    envs = list(getattr(getattr(cfg, "flow", None), "environments", None) or [])
    if not envs:
        return ""
    tree = _committing_tree(repo) or Path(repo)
    r = W.git(tree, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = r.out.strip() if r.ok else ""
    if branch not in envs or _merged_in(tree) or _squash_pending(tree):
        return ""
    staged = staged_paths(repo)
    if staged is not None and all(any(p.startswith(x) for x in SELF_MANAGED) for p in staged):
        return ""
    chain = " -> ".join(env_chain(cfg, W.default_branch(Path(repo))))
    mode = cfg.enforce.environment_commits
    return "\n".join(
        [
            (
                f"ddflow: refusing a commit made directly on {branch!r}, an environment branch."
                if mode == "block"
                else f"ddflow: warning: a commit made directly on {branch!r}, an environment branch."
            ),
            "",
            f"An environment only receives work by promotion ({chain}), upstream first; a",
            "commit made here exists nowhere upstream and the next promotion conflicts.",
            "",
            "Fix by committing on a work branch and promoting it:",
            "    git switch -c <work-branch>      # keep this commit there",
            "    ddflow promote status            # what is ready to promote",
            "",
            f'Policy is [enforce].environment_commits = "{mode}" in .ddflow/config.toml;',
            (
                'set it to "warn" or "off" if committing here is deliberate (a hotfix on prod).'
                if mode == "block"
                else 'set it to "off" to silence this warning.'
            ),
        ]
    )


def check_commit(repo: Path, cfg: Config | None = None, *, agent: str = "") -> Verdict:
    """(exit_code, message). 0 allows the commit; 1 refuses it.

    Returns a *message*, not a print, so the same logic serves the hook, `doctor`, and
    a CI job without three copies of the wording.
    """
    cfg = cfg or Config.load(repo)
    env_mode = cfg.enforce.environment_commits
    env_msg = environment_branch_commit(repo, cfg) if env_mode != "off" else ""
    if env_msg and env_mode != "warn":
        return Verdict(FAIL, env_msg)
    code, msg = _check_lease(repo, cfg, agent=agent)
    if env_msg:
        msg = "\n\n".join(m for m in (env_msg + "\n\n(warning only)", msg) if m)
    if code == 0:  # a refusal says its own thing; the advisory rides on an allowed commit
        msg = "\n\n".join(m for m in (msg, _complexity_note(repo, cfg)) if m)
    if code == 0:
        from ..enforce_secrets import check_event_secrets  # imports this module: lazily

        scode, smsg = check_event_secrets(repo, cfg)
        code, msg = scode, "\n\n".join(m for m in (msg, smsg) if m)
    return Verdict(code, msg)


def _complexity_note(repo: Path, cfg: Config) -> str:
    """The commit-time warning for staged functions that rose towards the complexity guard."""
    warn_at = cfg.enforce.complexity_warn_at
    paths = staged_paths(repo) if warn_at > 0 else None
    if not paths:
        return ""
    tree = _index_tree(repo)

    def head(path: str) -> bytes | None:
        r = G.run(tree, "show", f"HEAD:{path}", binary=True, timeout=P.TIMEOUTS["git_listing"])
        return r.out_bytes if r.ok else None

    return CB.advice(CB.risen(paths, lambda p: staged_bytes(repo, p, tree=tree), head, warn_at))


def _split_leases(
    repo: Path, state, cfg: Config, me: str, here: Path, now: float, bound: bool = False
) -> tuple[list[str], dict[str, str], bool]:
    """(globs I hold, glob -> "item (holder)" of every other live lease, whether I hold any)."""
    mine: list[str] = []
    others: dict[str, str] = {}
    holds_any = False
    for item_id, lease in leases.unfinished_leases(state, cfg, now).items():  # as for `update`
        if _counts_as_mine(repo, lease, me, here, bound):
            holds_any = True
            mine.extend(lease.globs)
        else:
            for g in lease.globs:
                others[g] = f"{item_id} ({lease.holder})"
    return mine, others, holds_any


def _without_new_files(
    repo: Path, cfg: Config, uncovered: list[str], holds_any: bool, others: dict[str, str]
) -> list[str]:
    """``uncovered`` less the staged NEW files `[lease] new_file_globs` names: a fragment
    named after its own knob is nobody else's (B92e13a521f). An edit stays uncovered, and so
    does a path another agent's live lease names."""
    if not holds_any:
        return uncovered
    globs = new_file_globs(cfg)
    cand = [
        p
        for p in uncovered
        if any(path_in_glob(p, g) for g in globs) and not any(path_in_glob(p, o) for o in others)
    ]
    if not cand:
        return uncovered
    added = CH.changed_paths(_index_tree(repo), include=("staged",), renames=True, diff_filter="A")
    fresh = set(added or ())
    return [p for p in uncovered if p not in cand or p not in fresh]


def _without_branch_changed(
    repo: Path, uncovered: list[str], stolen: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Drop the held-by-another paths this merge-concluding commit's branch itself changed.

    A rival's lease taken after the branch committed a path must not strand the merge of
    main into it (B01ce0f1283): the branch's commits since the merge-base were judged when
    made, and the rival's lease cannot be widened around. Outside a merge, or when git
    cannot say, nothing is dropped."""
    if not stolen:
        return uncovered, stolen
    tree = _index_tree(repo)
    other = _merged_in(tree)
    if not other:
        return uncovered, stolen
    base = W.git(tree, "merge-base", "HEAD", other)
    if not (base.ok and base.out):
        return uncovered, stolen
    own = W.git_paths(tree, "diff", "--no-renames", "--name-only", base.out.split()[0], "HEAD")
    if own is None:
        return uncovered, stolen
    mine = set(own)
    drop = {p for p in stolen if p in mine}
    return [p for p in uncovered if p not in drop], {
        p: o for p, o in stolen.items() if p not in drop
    }


def _uncovered_paths(paths: list[str], mine: list[str], shared: list[str]) -> list[str]:
    """The staged paths neither shared nor inside a glob I hold."""
    return [
        p for p in paths if not is_shared(p, shared) and not any(path_in_glob(p, g) for g in mine)
    ]


def _lapsed_mine(
    repo: Path,
    state,
    uncovered: list[str],
    others: dict[str, str],
    me: str,
    here: Path,
    now: float,
    grace_s: float,
    bound: bool = False,
) -> list:
    """A lease that WOULD have been mine, by the same two tests, but has lapsed and was
    not taken over. Invisible above, so the message said "(no live lease)" and "claim
    the work" to the holder committing in its own tree, and the lapse was misread as
    an identity bug (B3e050cb66a, B5fde61b8a9). Still not a pass: named, not counted.

    Per path, not per lease (DX-lapsed-lease-merge-deadlock): a lease whose globs overlap
    another agent's live lease is withheld only because ACCEPTING it would wave a staged
    path that other lease covers -- the one acceptance that could take a held path. By
    that scope, the old overlap test withheld the lease for the paths too, stranding a
    lapsed item unable to commit its merge of main with no remedy that works (a heartbeat
    would revive the lease over someone's live paths, the race the STOP text warns
    against). Held per path still: a staged path the other live lease covers must take a
    live lease to commit, and `renew`'s revival refusal over a live claim
    (L-revival-judged-on-lease-globs) stays as is."""
    return [
        (item_id, lease)
        for item_id, lease in state.expired_leases(now, grace_s).items()
        if _counts_as_mine(repo, lease, me, here, bound)
        and any(path_in_glob(p, g) for p in uncovered for g in lease.globs)
        and not any(
            any(path_in_glob(p, o) for o in others)
            for p in uncovered
            if any(path_in_glob(p, g) for g in lease.globs)
        )
    ]


def _accepted_lapsed(cfg: Config, lapsed: list, me: str) -> list:
    """The lapsed leases `[enforce].lapsed_lease` lets this commit stand on: nobody took
    them over (`_lapsed_mine` already excludes any overlapping a live lease), so
    refusing the holder, or the agent in the item's own tree, for a lapse only costs a
    heartbeat it may not know to send (bug B8aa03801a0)."""
    policy = cfg.enforce.lapsed_lease
    if policy == "refuse":
        return []
    return [(i, lease) for i, lease in lapsed if policy == "accept" or lease.holder == me]


def _after_accepting(cfg: Config, uncovered: list[str], lapsed: list, me: str, now: float):
    """(paths still uncovered, lapsed leases still unaccepted, the note for accepted ones)."""
    accepted = _accepted_lapsed(cfg, lapsed, me)
    if not accepted:
        return uncovered, lapsed, ""
    left = [p for p in uncovered if not _covered_by(p, accepted)]
    return left, [x for x in lapsed if x not in accepted], _accepted_note(accepted, now, me)


def _covered_by(path: str, leases: list) -> bool:
    return any(path_in_glob(path, g) for _i, lease in leases for g in lease.globs)


def _accepted_note(accepted: list, now: float, me: str) -> str:
    lines = [
        "ddflow: allowed on a LAPSED lease nobody has taken over (policy [enforce].lapsed_lease).",
        "",
    ]
    for item_id, lease in accepted:
        lines += _lapsed_lines(item_id, lease, now, me, allowed=True)
    return "\n".join(lines).rstrip()


def _add_globs_arg(paths: list[str]) -> str:
    """The already-shell-quoted `--add-globs` VALUE for the advice: ONE JSON array,
    each staged path `glob.escape`d, wrapped by `shlex.quote`. Every other notation
    splits on commas (`globspec.parse`), so a staged path holding a comma would be
    printed as a command that ADDS DIFFERENT globs; JSON keeps each path whole,
    `glob.escape` keeps metacharacters literal (a staged `src/a[b].py` must not
    become a class matching `src/ab.py`), and `shlex.quote` keeps the printed
    command copy-runnable (rubber_duck/critic rounds on DX-hook-suggests-add-globs)."""
    return shlex.quote(json.dumps([glob.escape(p) for p in paths]))


def _uncovered_message(
    cfg: Config,
    state,
    uncovered: list[str],
    stolen: dict[str, str],
    lapsed: list,
    mine: list[str],
    me: str,
    now: float,
    mode: str,
    agent: str,
) -> str:
    """The refusal / warning text for staged paths no lease I hold covers."""
    lines = [
        f"ddflow: {len(uncovered)} staged path(s) are not covered by a lease you hold.",
        "",
    ]
    for p in uncovered[:MAX_LISTED_PATHS]:
        lines.append(f"  {p}" + (f"   <-- held by {stolen[p]}" if p in stolen else ""))
    if len(uncovered) > MAX_LISTED_PATHS:
        lines.append(f"  ... and {len(uncovered) - MAX_LISTED_PATHS} more")
    lines.append("")
    widen = _add_globs_arg(uncovered)
    if stolen and all(v.endswith(f"({me})") for v in stolen.values()):
        lines += [
            f"These paths belong to another item YOU hold, bound to a tree of its own: "
            f"commit them there, or widen THIS item's claim "
            f"(`ddflow update <ID> --add-globs {widen}`).",
            "",
        ]
    elif stolen:
        lines += [
            "STOP: paths marked above are leased by ANOTHER agent. Committing them races",
            "that agent's work. Coordinate, or wait for the lease to be released.",
            "",
        ]
        # `_load` writes the derived name back into cfg.agent.id; its source says so.
        derived = not cfg.agent.id or cfg.sources.get("agent.id") == "derived"
        if not agent and derived:
            held_by = {
                item_id: lease
                for item_id, lease in state.active_leases(now, cfg.lease.grace_s).items()
                if any(path_in_glob(p, g) for p in stolen for g in lease.globs)
            }
            lines += _derived_identity_lines(me, held_by)
    for item_id, lease in lapsed:
        lines += _lapsed_lines(item_id, lease, now, me)
    held = ", ".join(mine) if mine else "(no live lease)"
    if lapsed and not mine:
        held = "nothing live (the lease above lapsed)"
    lines += [
        f"You hold: {held}",
        "",
        "Otherwise, claim the work or widen the claim you already hold:"
        if lapsed
        else "Fix by claiming the work, or by widening the claim you already hold:",
        "    ddflow next                     # what may be started",
        "    ddflow claim <ID>                # lease it and get a worktree",
        f"    ddflow update <ID> --add-globs {widen}  # add THESE paths;"
        " --globs would replace the list",
        "",
        f'Policy is [enforce].commit_without_lease = "{mode}" in .ddflow/config.toml.',
    ]
    return "\n".join(lines)


def _with_contested(
    repo: Path, log, cfg, state, paths, uncovered, me, here, now, bound=False
) -> tuple[list[str], dict[str, str]]:
    """``uncovered`` plus the paths a wildcard of mine covers that another live lease holds
    too (`enforce_contested`), and those paths with their holders."""
    twice = EC.contested(
        log,
        cfg,
        state,
        paths,
        lambda lz: _counts_as_mine(repo, lz, me, here, bound),
        lambda lz: here is not None and _made_tree(repo, lz) == here,
        now,
    )
    return uncovered + [p for p in twice if p not in uncovered], twice


def _check_lease(repo: Path, cfg: Config, *, agent: str = "") -> Verdict:
    """The lease half of `check_commit`."""
    policy = getattr(cfg, "enforce", None)
    mode = getattr(policy, "commit_without_lease", "warn") if policy else "warn"
    if mode == "off":
        return Verdict(OK, "")

    staged = staged_paths(repo)
    if staged is None:
        return (
            (0, _UNKNOWN_STAGED + "\n\n(warning only)") if mode == "warn" else (1, _UNKNOWN_STAGED)
        )
    paths = [p for p in staged if not _not_work(p)]
    paths = _commit_scope(_index_tree(repo), paths, cfg, lease=True)
    if not paths:
        return Verdict(OK, "")

    log = EventLog(repo, agent or cfg.agent.id or "", log_cfg=cfg.log)
    state = localize(fold(log.read_all(), strict=False), log, time.time())
    paths = _beyond_upgrade_output(repo, state, paths)
    if not paths:
        return Verdict(OK, "")
    now = time.time()
    me = log.agent_id
    here = _committing_tree(repo)
    bound = _tree_bound(repo, state, cfg, here, now)
    mine, others, holds_any = _split_leases(repo, state, cfg, me, here, now, bound)

    # A shared file (`[lease] shared_globs` / `append_only_globs`) is every live holder's
    # to edit (D-shared-globs): the changelog line each item adds is not a trespass.
    shared = shared_globs(cfg) if holds_any else []
    uncovered = _uncovered_paths(paths, mine, shared)
    uncovered = _without_new_files(repo, cfg, uncovered, holds_any, others)
    uncovered, twice = _with_contested(
        repo, log, cfg, state, paths, uncovered, me, here, now, bound
    )
    if not uncovered:
        return Verdict(OK, "")

    # A path another agent holds is the dangerous case and gets named separately: the
    # remedy is not "claim it", it is "stop".
    stolen = {p: owner for p in uncovered for g, owner in others.items() if path_in_glob(p, g)}
    stolen.update(twice)
    uncovered, stolen = _without_branch_changed(repo, uncovered, stolen)
    lapsed = _lapsed_mine(repo, state, uncovered, others, me, here, now, cfg.lease.grace_s, bound)
    uncovered, lapsed, note = _after_accepting(cfg, uncovered, lapsed, me, now)
    if not uncovered:
        return Verdict(OK, note)
    msg = _uncovered_message(cfg, state, uncovered, stolen, lapsed, mine, me, now, mode, agent)
    msg = f"{note}\n\n{msg}" if note else msg
    if mode == "warn":
        return Verdict(OK, msg + '\n\n(warning only; set the policy to "block" to refuse)')
    return Verdict(FAIL, msg)


def _beyond_upgrade_output(repo: Path, state, paths: list[str]) -> list[str]:
    """``paths`` less the ones whose staged bytes are exactly what an `upgrade --apply`
    recorded as written (`upgrade.applied` ``written``, or `pins regenerate`'s local record): the tool's own output needs no lease,
    but a file edited after the upgrade has another digest and does."""
    wrote: dict[str, set[str]] = {}
    for run in state.upgrade_runs.values():
        for rel, digest in (run.get("written") or {}).items():
            wrote.setdefault(rel, set()).add(digest)
    # A regenerated pin file is the same kind of output (bug B1579b8a1a9).
    for rel, digests in PW.load(repo).items():
        wrote.setdefault(rel, set()).update(digests)
    out = []
    for p in paths:
        data = staged_bytes(repo, p) if p in wrote else None
        if data is None or not ({UW.digest(data), _index_blob(repo, p)} & wrote[p]):
            out.append(p)
    return out
