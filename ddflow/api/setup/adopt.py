"""Adopt ddflow into a project: drivers, the agent rules sections, the hook.

The files are written into the tree ``called_from`` stands in: a linked worktree's own
checkout, committed through its branch, never the shared primary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...config import csv_list
from ...core import outcome as O
from ...core.events import SkewRefused
from ...infra import worktree as W
from ...services import help as H
from ...services import pins_merge as PM
from ...services.adopt import (
    AGENT_TARGETS,
    Refused,
    adopt,
    adopted_agents,
    init_files,
    refresh_docs,
)
from ...services.prompts import TemplateError
from ...views import human
from ...views import human as _human
from .._base import _load
from .companions import _scan


def help_topic(
    repo: Path, *, topic: str = "", tools: dict | None = None, agent: str = ""
) -> O.Outcome:
    """What this is, what it can do, and what the workflow is.

    `tools` is handed IN by the surface: `services/` sits below `surfaces/`, so the help
    renderer may not reach up for the tool registry, and the generated capability inventory
    must come from the LIVE table rather than a hand-kept second copy.
    """

    try:
        text = H.render_topic(topic, repo) if topic else H.render_index(repo, tools=tools or {})
    except TemplateError as exc:
        return O.failed("help", str(exc), topic=topic or "index", text="", topics=list(H.TOPICS))
    return O.ok("help", topic=topic or "index", text=text, topics=list(H.TOPICS))


def init_project(repo: Path, *, agent: str = "", called_from: Path | None = None) -> O.Outcome:
    """`ddflow init`: create `.ddflow/` and the repository files that keep its log safe.

    The same `services.adopt.init_files` that `setup` runs as its first step, so `init`,
    `adopt` and `ddflow_setup` cannot disagree about what an initialised project is.
    """

    del agent  # identity is not needed to create files; accepted for surface symmetry
    tree = files_tree(repo, called_from)  # committed files: the caller's tree, as `setup`
    actions = init_files(tree)
    d = tree / ".ddflow"
    return O.ok("init", actions=actions, root=str(d), config=str(d / "config.toml"), tree=str(tree))


@dataclass
class Adoption:
    """What `setup` writes, and for whom."""

    agents: str = ""
    docs: str = "docs/ddflow"
    launch: str = ""
    image: str = ""
    #: Rewrite only the driver docs and the rules blocks (`services.adopt.refresh_docs`).
    refresh_docs: bool = False
    #: Preview what `refresh_docs` would write, without writing anything (a diff mode).
    plan: bool = False


def files_tree(repo: Path, called_from: Path | None) -> Path:
    """Where `adopt` writes the files meant to be COMMITTED: the caller's own tree.

    `repo` is the primary checkout (`repo_root`), which is right for the shared event log
    and the hooks, and wrong for files that reach the project through the caller's
    branch: from a linked worktree they dirtied the primary and left that branch with
    nothing (bug Bfeb62112d9). A `called_from` outside this repository is not a tree of it.
    Not `W.current`: that answers None for a DETACHED linked worktree (nothing to adopt
    for a claim), which is still the caller's checkout here, and it does not ask whether
    the tree belongs to `repo` at all.
    """

    if called_from is None:
        return Path(repo)
    top = W.git(called_from, "rev-parse", "--show-toplevel")
    if not top.ok or not top.out:
        return Path(repo)
    try:
        same = W.repo_root(called_from).resolve() == Path(repo).resolve()
    except W.GitError:
        same = False
    return Path(top.out).resolve() if same else Path(repo)


def setup(
    repo: Path,
    plan: Adoption | None = None,
    *,
    agent: str = "",
    called_from: Path | None = None,
) -> O.Outcome:
    """Adopt ddflow into a project: drivers, the agent rules sections, the hook.

    The files are written into the tree `called_from` stands in (`files_tree`): a linked
    worktree's own checkout, committed through its branch, never the shared primary.
    Without `called_from` they go to `repo`.

    Names the companion gap at adoption time. A project that adopts ddflow and stops has a
    `standards` gate with nothing behind it and a `rules` gate reading no memory — and
    because an agent gate passes on an assertion, that gap is invisible in exactly the way
    this design exists to prevent. Detection only; nothing is installed, because fetching
    and running code on someone's machine is not a thing a work-queue tool gets to do.
    """

    plan = plan or Adoption()
    log, cfg, _st = _load(repo, agent)
    try:
        # An older ddflow must not rewrite what a newer one wrote (the managed blocks and
        # driver docs are tracked files, so the log's skew guard has to cover them too).
        log.check_skew()
    except SkewRefused as exc:
        return O.refused("setup", str(exc), actions=[], agents=[], text="")
    agents = csv_list(plan.agents) or list(AGENT_TARGETS)
    tree = files_tree(repo, called_from)
    if plan.refresh_docs:
        # Documents only: no init files, hooks, MCP launches or command files, and no
        # companion scan. Into the caller's tree like every committed file.
        try:
            actions = refresh_docs(tree, docs_dir=plan.docs, plan=plan.plan)
        except ValueError as exc:
            return O.failed("setup", str(exc), actions=[], agents=[], text="")

        data = {
            "actions": actions,
            "agents": adopted_agents(tree, docs_dir=plan.docs),
            "tree": str(tree),
            "refresh_docs": True,
            "companions_ready": [],
            "companions_absent": [],
            "text": "",
        }
        refused = [a for a in actions if isinstance(a, Refused)]
        # A file a NEWER ddflow wrote is refused (exit 3, "upgrade ddflow to >= X"); the
        # rest was still refreshed, so the actions are reported in full.
        out = O.refused("setup", "; ".join(refused), **data) if refused else O.ok("setup", **data)

        out.data["text"] = _human.render(out)
        return out
    try:
        actions = adopt(
            tree,
            agents,
            docs_dir=plan.docs,
            install_hooks=cfg.enforce.install_hooks_on_setup,
            launch=plan.launch,
            image=plan.image,
        )
    except ValueError as exc:
        return O.failed("setup", str(exc), actions=[], agents=agents, text="")
    # A project that lists pin recipes gets the merge driver and post-merge hook that
    # regenerate its pin files (DX-pins-regenerate); one without recipes is untouched.
    try:
        actions += PM.install(tree, cfg)
    except (OSError, RuntimeError) as exc:
        actions.append(
            f"pin merge driver NOT installed ({exc}); `ddflow pins regenerate --install`"
        )

    statuses = _scan(repo)
    if isinstance(statuses, O.Outcome):
        return statuses
    ready, absent = [], []
    for st in statuses:
        if not st.is_gap:
            continue
        (ready if st.state == "installed" else absent).append(st.companion.id)

    data = {
        "actions": actions,
        "agents": agents,
        "tree": str(tree),
        "companions_ready": ready,
        "companions_absent": absent,
        "text": "",
    }
    refused = [a for a in actions if isinstance(a, Refused)]
    if refused:
        # Everything else was still written, so the actions are reported in full; but a
        # step the operator must finish by hand is not "adopted", and the exit code is
        # what a script or an agent reads.
        out = O.failed("setup", "; ".join(refused), **data)
    else:
        out = O.ok("setup", **data)
    out.data["text"] = human.render(out)
    return out
