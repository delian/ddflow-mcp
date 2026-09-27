"""Branching model and versioning — which branch, which target, which version. PURE.

Every question here is answerable from the fold and the config alone, which is why it
lives in `core`: the git and forge calls that ACT on the answers are in `infra`, and the
rules can be tested without a repository (RESEARCH R16).

Two models, deliberately no more:

* **trunk** — every task forks from and lands on one base branch. ddflow's behaviour
  before this module existed, unchanged: same branch names, same target.
* **gitflow** — the Driessen model as teams actually run it. Features and bugfixes fork
  from ``develop`` and land there; a hotfix forks from production and lands on
  production AND ``develop`` (a fix that reaches only production is re-broken by the
  next release); a release branch is cut from ``develop``, lands on production, is
  tagged there, and is merged back.

GitHub flow and GitLab flow are trunk with pull requests, which is ``model = "trunk"``
plus ``integration = "pr"`` — the two axes are independent, and making them one knob
would make the combination nobody listed impossible to express.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import Config
from .model import REVIEW, Item, State

TRUNK, GITFLOW = "trunk", "gitflow"
MODELS = (TRUNK, GITFLOW)
INTEGRATIONS = ("merge", "pr")
PR_MERGE = ("on_approval", "auto", "human")
ON_CHANGES = ("reopen", "block")
FORGES = ("auto", "github", "gitlab")

FEATURE, BUGFIX, HOTFIX = "feature", "bugfix", "hotfix"


def problems(cfg: Config) -> list[str]:
    """Enumerated `[flow]` knobs holding a value nothing understands.

    Checked where the value is USED rather than at load, because `Config._apply` is
    generic and knows only types. Without it `model = "git-flow"` would read as "not
    gitflow" and quietly run trunk -- the silently-dropped-knob class.
    """
    fc = cfg.flow
    out = []
    for knob, value, allowed in (
        ("model", fc.model, MODELS),
        ("integration", fc.integration, INTEGRATIONS),
        ("pr_merge", fc.pr_merge, PR_MERGE),
        ("on_changes_requested", fc.on_changes_requested, ON_CHANGES),
        ("forge", fc.forge, FORGES),
    ):
        if value not in allowed:
            out.append(f"[flow].{knob} = {value!r} is not one of {', '.join(allowed)}")
    if parse_version(cfg.flow.initial_version) is None:
        out.append(
            f"[flow].initial_version = {cfg.flow.initial_version!r} is not MAJOR.MINOR.PATCH"
        )
    return out


def _tagged(it: Item, tags: list[str]) -> bool:
    want = {t.lower() for t in tags}
    return any(t.lower() in want for t in it.tags)


def branch_kind(it: Item, cfg: Config) -> str:
    """feature | bugfix | hotfix, from the item's tags. Hotfix wins over bugfix."""
    if _tagged(it, cfg.flow.hotfix_tags):
        return HOTFIX
    if _tagged(it, cfg.flow.bugfix_tags):
        return BUGFIX
    return FEATURE


def safe_name(item_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", item_id).strip("-") or "item"


def branch_name(it: Item, cfg: Config) -> str:
    """The branch ddflow creates for ``it``.

    Under trunk this is exactly the pre-existing ``worktree.branch_prefix + id``, so a
    project that never sets ``[flow]`` sees no rename of anything already in flight.
    """
    name = safe_name(it.id)
    if cfg.flow.model != GITFLOW:
        return f"{cfg.worktree.branch_prefix}{name}"
    prefix = {
        FEATURE: cfg.flow.feature_prefix,
        BUGFIX: cfg.flow.bugfix_prefix,
        HOTFIX: cfg.flow.hotfix_prefix,
    }[branch_kind(it, cfg)]
    return f"{prefix}{name}"


def production(cfg: Config, default_branch: str) -> str:
    return cfg.flow.production_branch or default_branch


def target_branch(it: Item, cfg: Config, default_branch: str) -> str:
    """Where ``it``'s work finally lands (ignoring stacking, which is temporary)."""
    if cfg.flow.model != GITFLOW:
        return cfg.worktree.base_ref or default_branch
    if branch_kind(it, cfg) == HOTFIX:
        return production(cfg, default_branch)
    return cfg.flow.develop_branch


def back_merge_targets(it: Item, cfg: Config, default_branch: str) -> list[str]:
    """Branches that must ALSO receive ``it`` after it lands. A gitflow hotfix only."""
    if cfg.flow.model == GITFLOW and branch_kind(it, cfg) == HOTFIX:
        dev = cfg.flow.develop_branch
        if dev != target_branch(it, cfg, default_branch):
            return [dev]
    return []


@dataclass
class Stack:
    """What ``it`` would fork from if it started now, and why."""

    base: str = ""  # "" = no stacking: fork from the target branch
    on: str = ""  # the dependency it stacks on
    error: str = ""


def stack_base(state: State, it: Item, cfg: Config, deps: list[str]) -> Stack:
    """If a dependency is in REVIEW, fork from ITS branch -- a stacked pull request.

    ``deps`` is the item's inherited dependency ids (the caller has them already, and
    this module must not import the scheduler that imports it). Only dependencies in
    REVIEW matter: a DONE one is on the target branch already.

    Two unmerged dependencies on DIFFERENT branches cannot both be under one branch.
    That is reported as an error rather than resolved by picking one, because picking
    one silently omits the other's code from the dependent's tree -- and its tests would
    then pass against a world that will not exist once both land.
    """
    if not cfg.flow.stack:
        return Stack()
    pending: dict[str, str] = {}
    for dep in deps:
        d = state.items.get(dep)
        if d is not None and d.state == REVIEW and d.branch:
            pending.setdefault(d.branch, dep)
    if not pending:
        return Stack()
    if len(pending) > 1:
        names = ", ".join(f"{dep} ({br})" for br, dep in sorted(pending.items()))
        return Stack(
            error=f"depends on {len(pending)} unmerged branches ({names}); a branch can "
            f"stack on only one. It starts when all but one have merged."
        )
    branch, dep = next(iter(pending.items()))
    return Stack(base=branch, on=dep)


# -- versions -----------------------------------------------------------------------

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

MAJOR, MINOR, PATCH = "major", "minor", "patch"
BUMPS = (MAJOR, MINOR, PATCH)


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _SEMVER.match(text.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def bump(version: tuple[int, int, int], kind: str) -> tuple[int, int, int]:
    """SemVer 2.0 increments. Below 1.0.0 a breaking change bumps MINOR, not MAJOR.

    That is the SemVer rule for 0.y.z ("anything MAY change at any time") as
    conventional-changelog tools apply it by default: a pre-1.0 project that auto-bumped
    to 1.0.0 on its first `feat!` would have declared a stable API by accident.
    """
    major, minor, patch = version
    if kind == MAJOR:
        return (major, minor + 1, 0) if major == 0 else (major + 1, 0, 0)
    if kind == MINOR:
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def fmt(version: tuple[int, int, int]) -> str:
    return ".".join(str(x) for x in version)


#: Conventional Commits 1.0: `type(scope)!: subject`, or a BREAKING CHANGE footer.
_CC = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\([^)]*\))?(?P<bang>!)?:\s")


def commit_bump(message: str) -> str:
    """The bump one commit message asks for: major | minor | patch | ""."""
    first = message.strip().splitlines()[0] if message.strip() else ""
    if re.search(r"^BREAKING[ -]CHANGE:", message, re.MULTILINE):
        return MAJOR
    m = _CC.match(first)
    if not m:
        return ""
    if m.group("bang"):
        return MAJOR
    kind = m.group("type").lower()
    if kind == "feat":
        return MINOR
    if kind in ("fix", "perf", "revert"):
        return PATCH
    return ""


def item_bump(it: Item, cfg: Config) -> str:
    """The bump a finished ITEM implies, from its tags.

    Items are the unit ddflow knows about; commits are the unit the team's other tools
    know about. Both are read, and the larger bump wins, so a project that writes
    Conventional Commits and one that only tags its tasks both get a version.
    """
    tags = {t.lower() for t in it.tags}
    if tags & {"breaking", "major"}:
        return MAJOR
    if _tagged(it, cfg.flow.bugfix_tags) or _tagged(it, cfg.flow.hotfix_tags):
        return PATCH
    if tags & {"feature", "feat", "minor"}:
        return MINOR
    return ""


def strongest(bumps: list[str]) -> str:
    for kind in BUMPS:
        if kind in bumps:
            return kind
    return ""
