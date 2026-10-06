"""The `[worktree]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._docs import _doc


@dataclass
class WorktreeConfig:
    """Git worktree isolation for parallel agents."""

    enabled: bool = True
    root: str = ".ddflow/worktrees"
    branch_prefix: str = "ddflow/"
    base_ref: str = ""  # "" = the repo's default branch, auto-detected
    merge_strategy: str = "no-ff"  # no-ff | ff-only | squash
    remove_on_merge: bool = True
    max_parallel: int = 0  # 0 = follow the schedule limit (not unlimited)
    sync_before_start: bool = True
    adopt_existing: bool = True
    local_files: list[str] = field(default_factory=list)


_doc(
    "worktree",
    "enabled",
    "Whether tasks run in isolated git worktrees. Turn off only for a single-agent, single-task project; parallel agents in one tree destroy each other's work.",
)
_doc(
    "worktree",
    "root",
    "Where worktrees are created, relative to the repo root. Default `.ddflow/worktrees`: inside the project for every agent and harness, so the trees are found, kept and shared in one place (decision D-worktree-home), and git-ignored (`.ddflow/.gitignore`, and a `.gitignore` the root writes into itself), a dot-directory that the default settings of pytest, ruff and ripgrep skip; a tool configured to walk dot-directories or ignore `.gitignore` needs `.ddflow/worktrees` excluded. The former default, `../.ddflow-worktrees`, still works when set; trees created there keep their recorded paths.",
)
_doc(
    "worktree",
    "branch_prefix",
    "Prefix for auto-created task branches, so `git branch --list 'ddflow/*'` enumerates exactly the machine-managed ones.",
)
_doc(
    "worktree",
    "base_ref",
    "Branch new worktrees fork from. Empty means auto-detect the default branch (origin/HEAD, else main, else master) — hardcoding 'main' breaks every 'master' repo.",
)
_doc(
    "worktree",
    "merge_strategy",
    "'no-ff' keeps a merge commit per task (best audit trail), 'ff-only' keeps history linear, 'squash' collapses a task to one commit.",
)
_doc(
    "worktree",
    "remove_on_merge",
    "Remove the worktree after a successful merge. Disable while debugging the pipeline so post-mortem inspection is possible.",
)
_doc(
    "worktree",
    "max_parallel",
    "Ceiling on simultaneously active task worktrees. Guards disk and CPU; the scheduler queues beyond it rather than refusing. 0 (the default) means FOLLOW the schedule limit -- the adaptive or fixed number of items in flight (`schedule.parallel`) -- and is not unlimited: there is no unlimited setting, the schedule ceiling always bounds it. A nonzero value stays an independent hard cap on worktrees alone. Projects adopted before adaptive parallelism carry an explicit `max_parallel = 4`, which caps auto at 4 worktrees; `ddflow doctor` names it and the remedy `ddflow config --set worktree.max_parallel 0`.",
)
_doc(
    "worktree",
    "adopt_existing",
    "When the caller is already inside a linked git worktree, bind the item to THAT tree and branch instead of creating another. Agent harnesses (Claude Code, Cursor) often isolate the agent themselves; without this, claim builds a rival tree and tells the agent to leave the one holding its uncommitted work. Turn off to always create ddflow's own.",
)
_doc(
    "worktree",
    "sync_before_start",
    "Merge the base branch into the task branch before work starts. Prevents the 98-commits-behind-and-unmergeable failure that motivated this knob.",
)
_doc(
    "worktree",
    "local_files",
    "Git-IGNORED, machine-local files (repo-relative paths) copied from the primary checkout into every worktree a claim binds -- one it creates, the harness tree it adopts, or the item's own tree on a re-claim -- a tool config that must not be committed but is read from each checkout, like .roborev.toml. Never overwrites a file already in the worktree, and never copies a path git tracks (that one arrives with the checkout). A copy that fails is skipped, not raised.",
)
