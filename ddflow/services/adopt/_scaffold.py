"""The `.ddflow/` scaffolding `init` and `adopt` write: the starter config, the gitignore,
and the repository lines that make the event log safe to commit."""

from __future__ import annotations

from pathlib import Path

from ...infra.fsio import Managed, NewerContent, RegionError, replace_text
from .. import shared_files as SF


def starter_config() -> str:
    """The single configuration file.

    One file, not two. An earlier version also wrote `.ddflow/gates.toml` carrying a
    placeholder `unit_tests.command`, and because gates.toml wins over config.toml that
    placeholder silently overrode anything `ddflow configure` wrote -- so the documented
    way to set the test command could not set the test command. Splitting gates into
    their own file is still supported for operators who want it; it is just not the
    default, because a default that creates two sources of truth will produce two
    sources of truth.
    """
    return """# ddflow configuration — everything in one file.
# `ddflow config --explain` documents every knob. Only what you change needs to be
# here; everything else keeps its default.

# ---------------------------------------------------------------------------------
# THE ONE THING YOU MUST SET: how this project runs its tests.
# ---------------------------------------------------------------------------------
# [gate.unit_tests]
# command = "pytest -q -n auto"   # or "npm test" · "cargo test" · "go test ./..." · "make check"
#
# Set it with:   ddflow config --set gate.unit_tests.command "pytest -q -n auto"
# Run it in PARALLEL: `-n auto` needs pytest-xdist (`uv add --dev pytest-xdist`); without
# it, drop the flag. A serial run of a large suite is the slowest step of every item.
# Until it is set, the unit_tests gate reports UNAVAILABLE — which is honest, and
# blocks completion, rather than passing vacuously.
#
# Left COMMENTED on purpose: an empty table here would collide with the block that
# `ddflow config --append-toml` writes, since TOML forbids a duplicate table, and the
# documented way to configure the project would fail on a fresh install.

# ---------------------------------------------------------------------------------
# What is committed here vs what is local: this file is GENERIC project policy (the
# test command, pipelines, gates) that every clone must agree on. Your own endpoints,
# hosts, API-key variable names and machine sizing are git-ignored and go in
# .ddflow/local/ (`ddflow config --local --set ...`).
#
# A cross-family reviewer makes the `critic` gate real rather than self-reported.
# `ddflow reviewers detect --write` finds a local model server and writes it to
# .ddflow/local/reviewers.toml (`reviewers add` does the same; --shared commits it here
# instead). The block below is only the shape such an entry takes.
# ---------------------------------------------------------------------------------
# [[reviewer]]
# name     = "local"
# base_url = "http://127.0.0.1:11434/v1"
# model    = "qwen3:8b"
# family   = "alibaba"            # must differ from the authoring model's family
# gates    = ["critic"]

# api_key_env = "MY_API_KEY"      # the NAME of an env var, never the key itself

[lease]
ttl_s = 1800            # how long a claim survives without a heartbeat
heartbeat_s = 300

[worktree]
enabled = true
# max_parallel = 0      # 0 (default) follows the schedule limit; a number caps worktrees

[schedule]
# parallel = "auto"     # auto (default): the limit adapts in [max_parallel_min, max_parallel_max]
# max_parallel_tasks = 4   # the start value in auto, the ceiling in fixed

[enforce]
# "block" makes the pre-commit hook REFUSE a commit touching paths no lease of yours
# covers — the only layer of this workflow that does not rely on the agent agreeing.
# Starts at "warn" so adopting ddflow never breaks an existing repo on day one.
commit_without_lease = "warn"

[session]
brief_max_tokens = 1200
"""


#: The `.ddflow/.gitignore` ddflow owns outright. A constant, not an append: every line
#: in it is a claim about what ddflow itself writes into `.ddflow/`, so a stale copy
#: is wrong and the current one is the only right content.
DDFLOW_GITIGNORE = (
    "# The index and local state are DERIVED from events/ and are rebuildable.\n"
    "# They are machine-local on purpose: a committed index resurrects dead agents'\n"
    "# leases on every clone, and a committed cache is a merge conflict with no\n"
    "# meaningful resolution.\n"
    "index.db\nindex.db-*\nindex.rebuilding*\nevents.lock\n"
    "# What belongs to THIS machine -- your reviewer endpoints, API-key variable names,\n"
    "# test-worker counts -- goes in local/config.toml, local/gates.toml or\n"
    "# local/reviewers.toml, read last so it wins. reviewers.toml beside config.toml is\n"
    "# ignored as well: a LAN endpoint committed here reaches every clone.\n"
    "local/\n/reviewers.toml\n"
    "# The lock `config --set` / `workflow gate` take for a read-modify-write of a\n"
    "# config file. Without this line `git add .ddflow`, as `init` instructs, committed it.\n"
    ".*.lock\n"
    "# Every agent's task worktrees (decision D-worktree-home) and per-run logs: git\n"
    "# worktrees and scratch, never committed.\n"
    "/worktrees/\n/runs/\n"
)

#: The line that makes two clones' event-log shards concatenate on merge instead of
#: conflicting. Without it the first parallel merge of the log is a hand-resolved conflict.
UNION_MERGE_LINE = ".ddflow/events/*.jsonl merge=union\n"


#: `.ddflow/.gitignore`'s ddflow part, a stamped region (fsio.Managed) so a line a person
#: adds outside it survives and a copy of an older release is told from a hand edit. The
#: region is rewritten only when its BODY differs: a release alone changes no byte of it.
GITIGNORE_REGION = Managed("gitignore", open="#", close="")


def write_ddflow_gitignore(gi: Path) -> bool:
    """Bring ``.ddflow/.gitignore`` to this ddflow's text; True when it was written.

    A file with the region keeps everything outside it; a file with none (the whole-file
    constant an earlier ddflow wrote, or a hand-made one) is replaced outright, as before."""
    have = gi.read_text("utf-8", errors="replace") if gi.is_file() else None
    if have is not None:
        try:
            body = GITIGNORE_REGION._region().body(have)
        except RegionError:
            # Broken markers: drop the stray marker lines and write a fresh region after what
            # is left, so a line a person added outside it survives.
            kept = [
                ln for ln in have.splitlines(keepends=True) if "ddflow:begin gitignore" not in ln
            ]
            kept = [ln for ln in kept if "ddflow:end gitignore" not in ln]
            text = "".join(kept)
            replace_text(
                gi,
                text
                + ("" if text.endswith("\n") or not text else "\n")
                + GITIGNORE_REGION.render(DDFLOW_GITIGNORE),
            )
            return True
        if body == DDFLOW_GITIGNORE:
            return False
        if body is not None:
            try:
                replace_text(gi, GITIGNORE_REGION.splice(have, DDFLOW_GITIGNORE))
            except NewerContent:
                return False  # a newer ddflow's region: never downgraded
            return True
    replace_text(gi, GITIGNORE_REGION.render(DDFLOW_GITIGNORE))
    return True


def _append_once(path: Path, present: frozenset[str], text: str) -> bool:
    """Append `text` to a file the PROJECT owns unless one of its lines is in `present`.

    Appended, never rewritten: `.gitignore` and `.gitattributes` usually carry the
    project's own lines, and the newline guard keeps a file without a trailing newline
    from gluing our first line onto its last one.

    Whole LINES are compared, not substrings (bug B63d0028716): `.ddflow/events/*.jsonl
    -diff` contains "ddflow/events" and is not the union merge, and `.ddflow-worktrees-old/`
    contains ".ddflow-worktrees" and ignores nothing of ours. A substring test let either
    one stand in for the rule and the rule was never written.
    """
    prev = path.read_text("utf-8") if path.exists() else ""
    if any(" ".join(line.split()) in present for line in prev.splitlines()):
        return False
    replace_text(path, prev + ("" if prev.endswith("\n") or not prev else "\n") + text)
    return True


#: The root `.gitignore` lines that already ignore an in-repo worktree root.
_WORKTREES_IGNORED = frozenset(
    {".ddflow-worktrees", ".ddflow-worktrees/", "/.ddflow-worktrees", "/.ddflow-worktrees/"}
)


def init_files(repo: Path) -> list[str]:
    """Create `.ddflow/` and the repository files that make its log safe to commit.

    ONE implementation for every surface. These writes used to live in the CLI's
    `cmd_init`, which `ddflow adopt` called and `ddflow_setup` over MCP never did (bug
    B185ec008b4): a project onboarded over MCP had a committable index, worktrees showing
    as untracked noise, and an event log that conflicted on the first parallel merge.
    `adopt` calls this, so both surfaces converge; `ddflow init` calls it alone.

    Idempotent. The starter config is written only when absent -- it is the project's
    once written -- and the root `.gitignore` / `.gitattributes` are appended to, never
    replaced. `.ddflow/.gitignore` is ddflow's own and is brought to the current text.
    """
    repo = Path(repo)
    actions: list[str] = []
    d = repo / ".ddflow"
    (d / "events").mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if write_ddflow_gitignore(gi):
        actions.append("wrote .ddflow/.gitignore")
    cfgp = d / "config.toml"
    if not cfgp.exists():
        replace_text(cfgp, starter_config())
        actions.append("wrote .ddflow/config.toml (starter)")
    # An in-repo worktree root (the default inside a container, where a sibling path
    # would land on the ephemeral layer) must be ignored, or every worktree shows up as
    # hundreds of untracked files and the enforcement hook trips over them.
    if _append_once(
        repo / ".gitignore",
        _WORKTREES_IGNORED,
        "\n# ddflow task worktrees (git worktrees; never commit them)\n.ddflow-worktrees/\n",
    ):
        actions.append("added .ddflow-worktrees/ to .gitignore")
    if _append_once(
        repo / ".gitattributes", frozenset({UNION_MERGE_LINE.strip()}), UNION_MERGE_LINE
    ):
        actions.append("added merge=union for .ddflow/events/*.jsonl to .gitattributes")
    # `[lease] append_only_globs` get their union line too (D-shared-globs): re-synced
    # here, so a config edited by hand is caught up by `init` / `adopt`.

    actions += [f"added '{ln}' to .gitattributes" for ln in SF.sync_attributes(repo)]
    return actions
