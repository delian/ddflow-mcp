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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import Config
from .model import REVIEW, Item, State

TRUNK, GITFLOW = "trunk", "gitflow"
MODELS = (TRUNK, GITFLOW)
INTEGRATIONS = ("merge", "pr")
PR_MERGE = ("on_approval", "auto", "human")
ON_CHANGES = ("reopen", "block")
FORGES = ("auto", "github", "gitlab")

FEATURE, BUGFIX, HOTFIX = "feature", "bugfix", "hotfix"
FORWARD_MERGE, CHERRY_PICK = "forward-merge", "cherry-pick"
PORT_STRATEGIES = (FORWARD_MERGE, CHERRY_PICK)


@dataclass(frozen=True)
class Choice:
    """A workflow decision that belongs to the operator or the agent, not to ddflow.

    ddflow supports several ways of working and must not pick one silently. So each is a
    CHOICE: it can be made in config (the operator's file, which wins), or recorded with
    `flow choose` by an agent or operator, and when nobody makes it the default is applied
    at the first moment it matters AND recorded -- so the project keeps following it,
    even if a later ddflow ships a different default. `flow show` lists every one, with
    who decided and when.
    """

    knob: str
    options: tuple[str, ...]
    question: str
    #: Asked about only when this is true: `port_strategy` means nothing without lines,
    #: and a brief that lists every conceivable decision gets skimmed.
    relevant: Callable[[Config], bool]


CHOICES: dict[str, Choice] = {
    c.knob: c
    for c in (
        Choice(
            "model",
            MODELS,
            "Branching model: one trunk, or gitflow (develop + production + release branches)?",
            lambda cfg: True,
        ),
        Choice(
            "integration",
            INTEGRATIONS,
            "Land work by merging locally, or through pull/merge requests a person approves?",
            lambda cfg: True,
        ),
        Choice(
            "pr_merge",
            PR_MERGE,
            "Who presses merge on an approved request: ddflow (on_approval), the forge "
            "(auto), or only a person (human)?",
            lambda cfg: cfg.flow.integration == "pr",
        ),
        Choice(
            "on_changes_requested",
            ON_CHANGES,
            "When a reviewer requests changes: return the item to the queue for an agent "
            "(reopen), or park it for a person (block)?",
            lambda cfg: cfg.flow.integration == "pr",
        ),
        Choice(
            "stack",
            ("true", "false"),
            "May work start on top of a dependency that is still in review (stacked "
            "requests), or wait for its merge?",
            lambda cfg: cfg.flow.integration == "pr",
        ),
        Choice(
            "port_strategy",
            PORT_STRATEGIES,
            "How does a fix reach several release lines: written on the oldest and "
            "merged forward (forward-merge), or written on the newest and cherry-picked "
            "back (cherry-pick)?",
            lambda cfg: bool(cfg.flow.lines),
        ),
    )
}


def choice_value(knob: str, raw: str) -> Any:
    """A choice as the config holds it: `stack` is a bool, the rest are strings."""
    if knob == "stack":
        return str(raw).strip().lower() in ("true", "1", "yes", "on")
    return str(raw)


def version_file_problem(path: str, pattern: str) -> str:
    """Why ``pattern`` cannot be a `[flow.version_files]` pattern ("" when it can): a regex
    with exactly ONE capture group, the version text."""
    try:
        rx = re.compile(pattern, re.MULTILINE)
    except re.error as exc:
        return f"[flow.version_files] {path!r}: {pattern!r} is not a regex ({exc})"
    if rx.groups != 1:
        return (
            f"[flow.version_files] {path!r}: the pattern needs exactly one capture group "
            f"(the version text); {pattern!r} has {rx.groups}"
        )
    return ""


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
        ("claims", fc.claims, ("local", "remote")),
    ):
        if value not in allowed:
            out.append(f"[flow].{knob} = {value!r} is not one of {', '.join(allowed)}")
    if fc.port_strategy not in PORT_STRATEGIES:
        out.append(
            f"[flow].port_strategy = {fc.port_strategy!r} is not one of {', '.join(PORT_STRATEGIES)}"
        )
    if fc.current_line in fc.lines:
        out.append(
            f"[flow].current_line {fc.current_line!r} is also a maintenance line in "
            f"[flow.lines]; the current line is the newest and follows `model`"
        )
    for path, pattern in fc.version_files.items():
        bad = version_file_problem(path, str(pattern))
        if bad:
            out.append(bad)
    envs = list(fc.environments)
    dup = sorted({e for e in envs if envs.count(e) > 1})
    if dup:
        out.append(f"[flow].environments lists {', '.join(dup)} twice; a chain has one order")
    clash = sorted(set(envs) & ({*fc.lines.values(), fc.develop_branch}))
    if clash:
        out.append(
            f"[flow].environments names {', '.join(clash)}, which is also a release line or "
            f"develop: an environment branch receives work only by promotion"
        )
    stray = sorted(set(fc.auto_promote) - set(envs))
    if stray:
        out.append(f"[flow].auto_promote names {', '.join(stray)}, not in [flow].environments")
    empty = [n for n, b in fc.lines.items() if not str(b).strip()]
    if empty:
        out.append(f"[flow.lines] {', '.join(empty)} name no branch")
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
    if cfg.flow.model != GITFLOW or it.promote_to:
        return f"{cfg.worktree.branch_prefix}{name}"
    prefix = {
        FEATURE: cfg.flow.feature_prefix,
        BUGFIX: cfg.flow.bugfix_prefix,
        HOTFIX: cfg.flow.hotfix_prefix,
    }[branch_kind(it, cfg)]
    return f"{prefix}{name}"


def production(cfg: Config, default_branch: str) -> str:
    return cfg.flow.production_branch or default_branch


def line_order(cfg: Config) -> list[str]:
    """Every line, oldest first; the current line is always last."""
    return [*cfg.flow.lines, cfg.flow.current_line]


def effective_line(state: State, it: Item) -> str:
    """The line ``it`` lands on: its own, else its nearest ancestor's, else the current.

    Inherited, so a phase filed for the 2.x line puts every task in it on 2.x without
    each one saying so -- and a task that says otherwise wins.
    """
    if it.line:
        return it.line
    for anc in state.ancestors(it.id):
        if anc.line:
            return anc.line
    return ""


def line_key(state: State, it: Item, cfg: Config) -> str:
    """The line ``it`` lands on, NAMED -- "" resolved to the current line.

    What conflict checks compare. Comparing `effective_line` strings made "" (no line)
    and "3" (the current line, by name) look like two lines while both land on the same
    branch, so two agents were allowed onto one file -- and every generated port to the
    current line carries the name.
    """
    return effective_line(state, it) or cfg.flow.current_line


def unknown_line(state: State, it: Item, cfg: Config) -> str:
    """``it``'s line when it names one the config no longer has; else "".

    A line removed from [flow.lines] must not quietly become "the current line": the
    item's work would land on the newest major, the one thing its line said it must not.
    """
    ln = effective_line(state, it)
    return ln if ln and ln not in line_order(cfg) else ""


def is_maintenance(cfg: Config, line: str) -> bool:
    return bool(line) and line != cfg.flow.current_line and line in cfg.flow.lines


def target_branch(it: Item, cfg: Config, default_branch: str, line: str = "") -> str:
    """Where ``it``'s work finally lands (ignoring stacking, which is temporary).

    A maintenance line lands directly on its branch; the current line follows `model`.
    ``line`` is the EFFECTIVE line (see `effective_line`), passed in because resolving
    inheritance needs the whole state.
    """
    if it.promote_to:
        return it.promote_to
    if is_maintenance(cfg, line):
        return cfg.flow.lines[line]
    if cfg.flow.model != GITFLOW:
        return cfg.worktree.base_ref or default_branch
    if branch_kind(it, cfg) == HOTFIX:
        return production(cfg, default_branch)
    return cfg.flow.develop_branch


def back_merge_targets(it: Item, cfg: Config, default_branch: str, line: str = "") -> list[str]:
    """Branches that must ALSO receive ``it`` after it lands. A gitflow hotfix only."""
    if is_maintenance(cfg, line) or it.promote_to:
        return []
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
    if not cfg.flow.stack or it.port_from:
        # A port applies what its source LANDED; stacked on an unlanded source there is
        # nothing to apply yet.
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


# -- ports across release lines ---------------------------------------------------------


@dataclass
class PortPlan:
    author: str  # the line the fix is written on
    ports: list[tuple[str, int]]  # (line, index into ports of what it ports FROM; -1 = the fix)
    strategy: str
    note: str = ""


def plan_ports(cfg: Config, requested: list[str], strategy: str) -> PortPlan:
    """Where a fix that must reach ``requested`` lines is written, and how it travels.

    forward-merge: written on the OLDEST requested line and merged forward through EVERY
    line up to the newest requested one. A merge cannot skip a line: 2.x merged into 3.x
    after 1.x was merged straight into 3.x would bring the fix's absence on 2.x along
    with everything else, so intermediate lines are included and the plan says so.

    cherry-pick: written on the NEWEST requested line, then applied to each older one
    independently -- so the ports run in parallel.
    """
    order = line_order(cfg)
    wanted = sorted({r or cfg.flow.current_line for r in requested}, key=order.index)
    if strategy == CHERRY_PICK:
        return PortPlan(wanted[-1], [(ln, -1) for ln in reversed(wanted[:-1])], strategy)
    chain = order[order.index(wanted[0]) : order.index(wanted[-1]) + 1]
    extra = [ln for ln in chain if ln not in wanted]
    return PortPlan(
        chain[0],
        [(ln, i - 1) for i, ln in enumerate(chain[1:])],
        strategy,
        note=(
            f"forward-merge passes through {', '.join(extra)} as well: a merge cannot skip a line"
            if extra
            else ""
        ),
    )


# -- environment branches (GitLab flow) ---------------------------------------------------


def env_chain(cfg: Config, default_branch: str) -> list[str]:
    """The promotion chain: the current line's released branch, then each environment.

    Trunk: the base branch. Gitflow: production -- what reaches an environment is what
    was released, not develop's work in progress.
    """
    head = (
        production(cfg, default_branch)
        if cfg.flow.model == GITFLOW
        else (cfg.worktree.base_ref or default_branch)
    )
    return [head, *cfg.flow.environments]


def promotion_step(cfg: Config, default_branch: str, env: str) -> tuple[str, str]:
    """``(from, env)`` for promoting to ``env``, or raises ValueError naming why not.

    Always ONE step, from the branch immediately upstream: "upstream first" is the rule
    that keeps production from receiving anything pre-production has not had.
    """
    chain = env_chain(cfg, default_branch)
    if env not in cfg.flow.environments:
        known = ", ".join(cfg.flow.environments) or "none are configured"
        raise ValueError(f"{env!r} is not an environment ([flow].environments: {known})")
    if env == chain[0]:
        raise ValueError(f"{env!r} is where the chain STARTS, not an environment to promote to")
    i = len(chain) - 1 - chain[::-1].index(env)
    return chain[i - 1], env
