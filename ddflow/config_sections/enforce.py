"""The `[enforce]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._docs import declare, knob

#: The values of each `[enforce]` policy knob.
BLOCK_WARN_OFF = ("block", "warn", "off")


def _waivers_problem(v: Any) -> str:
    """`[enforce].trailer_waivers`: key -> a non-empty list of non-empty words. An empty
    list would refuse every use of its key while reading like a waiver."""
    shape = 'must be a table of trailer key -> list of words, e.g. { "Phase-ships" = ["none"] }'
    if not isinstance(v, dict):
        return shape
    for key, words in v.items():
        if not key or ":" in key or any(c.isspace() for c in key):
            # `"Phase-ships "` or `"Phase ships"` could never match a trailer git
            # parses (its token holds no whitespace): an inert waiver.
            return f"{key!r}: a trailer key must be non-empty, with no whitespace and no ':'"
        if not isinstance(words, list):
            return f"{key!r}: {shape}"
        if not words:
            return f"{key!r} has no words, which would refuse every use of the key; list them"
        if not all(isinstance(w, str) and w.strip() == w and w for w in words):
            return f"{key!r}: every word must be a non-empty string with no surrounding spaces"
    return ""


@declare("enforce")
@dataclass
class EnforceConfig:
    """Mechanical enforcement — the layer that does not rely on the agent agreeing."""

    #: block | warn | off
    commit_without_lease: str = knob(
        "warn",
        doc="What the pre-commit hook does when a commit touches paths no live lease of yours covers. 'block' refuses (the only real enforcement ddflow has), 'warn' prints and allows, 'off' disables. Default 'warn' so adoption never breaks an existing repo on day one; switch to 'block' once the queue is populated.",
        choices=BLOCK_WARN_OFF,
        strictest=("block", "the hook refuses"),
    )
    install_hooks_on_setup: bool = knob(
        True,
        doc="Install the pre-commit hook during `ddflow adopt`. The hook is what makes the workflow enforced rather than merely described; disable only if your project manages hooks centrally.",
    )
    require_item_trailer: bool = knob(
        False,
        doc="Require every commit to carry an `Item: <id>` git trailer (or another key from item_trailer_keys) whose value is the id of an item in the queue -- a phase or task in any state but removed -- or a key from trailer_waivers carrying one of its words; checked by the commit-msg hook `ddflow hooks install` adds. A mistyped id is refused, naming it and the nearest real ids; a queue the hook cannot read is exit 2 (could not run), never a pass. Makes commits reconcilable against the queue by `git log --format='%(trailers:key=Item)'` instead of by parsing prose. Off by default because it is noisy on a repo with non-agent contributors.",
    )
    item_trailer_keys: list[str] = knob(
        factory=lambda: ["Item"],
        doc='Trailer keys that satisfy require_item_trailer; any one of them will do, and each must name an item in the queue unless trailer_waivers gives its key a vocabulary. A project that has written `Phase: <id>` (or `Phase-ships: none` for a commit that ships no item) in every commit for months keeps its convention: set ["Phase", "Phase-ships"] and declare Phase-ships in trailer_waivers. Matched case-insensitively, as git\'s `%(trailers:key=...)` does; every accepted trailer on a commit must be valid, not just one. Checked by the commit-msg hook on the message being committed; merge commits are exempt.',
    )
    forbidden_trailers: list[str] = knob(
        factory=list,
        doc='Trailer keys the commit-msg hook REFUSES, e.g. ["Co-'
        + "Authored-By\"] for a project that never credits a tool in its history. Case-insensitive; any line starting with `<key>:` counts, not only git's final-paragraph trailers, and merge commits are NOT exempt. Enforced by git's commit-msg hook, so it holds for every agent and every route that runs git hooks (`git commit -F`, the editor, merges), which a harness-side hook reading only the command text cannot see; `--no-verify` and plumbing skip it, as they skip every hook. Empty by default.",
    )
    trailer_waivers: dict[str, list[str]] = knob(
        factory=dict,
        doc='Trailer keys that mark a commit shipping NO item, each with the only words its value may take: `{ "Phase-ships" = ["none", "filing", "recon", "evidence", "followup"] }`. A trailer whose key is here AND in item_trailer_keys passes only with one of its words (`Phase-ships: bogus` is refused, listing them); every other item_trailer_keys trailer must carry an item id. A key here satisfies require_item_trailer whether or not item_trailer_keys also lists it. Empty by default: every accepted key names an item. Set it with the TOML inline table, `ddflow config --set enforce.trailer_waivers \'{ "Phase-ships" = ["none"] }\'`; JSON is the environment\'s form only: DDFLOW_ENFORCE_TRAILER_WAIVERS=\'{"Phase-ships": ["none"]}\'.',
        check=_waivers_problem,
    )
    #: block | warn | off
    generated_views: str = knob(
        "block",
        doc="What the pre-commit hook does when a STAGED view generated by `ddflow render` differs from what the log regenerates now -- hand-edited, or stale because the queue moved after rendering. 'block' refuses, 'warn' prints and allows, 'off' disables. Default 'block': it fires only on a commit that includes a view, and the remedy is one command (`ddflow render`, then re-stage).",
        choices=BLOCK_WARN_OFF,
        strictest=("block", "the hook refuses"),
    )
    #: block | warn | off
    stale_docs: str = knob(
        "warn",
        doc="What the pre-commit hook does when the commit removes or renames an identifier (snake_case, camelCase, --flag), a file, or a `name = value` default, and a doc file still names it on a line the commit does not touch. 'block' refuses, 'warn' prints and allows, 'off' disables. Default 'warn': the check is a heuristic over identifier-SHAPED tokens, and a false refusal on day one teaches --no-verify; switch to 'block' once its reports have been trustworthy here.",
        choices=BLOCK_WARN_OFF,
        strictest=("block", "the hook refuses"),
    )
    #: block | warn | off
    environment_commits: str = knob(
        "block",
        doc="What the pre-commit hook does with a commit made directly on a branch listed in [flow].environments (GitLab flow's upstream-first): 'block' (default) refuses it and names the remedy (work on a branch, then `ddflow promote status`), 'warn' prints and allows, 'off' disables. Merge and squash commits are never refused: that is how a promotion lands. Commits that stage only ddflow's own files are exempt.",
        choices=BLOCK_WARN_OFF,
        strictest=("block", "the hook refuses"),
    )
    doc_globs: list[str] = knob(
        factory=lambda: ["**/*.md", "**/*.rst", "**/*.adoc"],
        doc="Which tracked files are documentation for stale_docs, in git's glob pathspec syntax (`*` stops at `/`, `**/` is any depth). A matching file is searched for stale mentions, and its own removed lines are never taken as code removals. `*.txt` is deliberately NOT a default: CMakeLists.txt and requirements.txt are code, and calling them docs hid every name they removed; add a project's own text docs by path.",
    )
    #: fmt: skip
    doc_exclude: list[str] = knob(
        factory=lambda: [
            "**/CHANGELOG*",
            "**/HISTORY*",
            "**/NEWS*",
            ".ddflow/**",
            "docs/ddflow/**",
        ],
        doc="Documentation that legitimately names removed things and is never reported by stale_docs: changelogs and history, ddflow's own generated views and log. Add a project's backlog or decision records here -- a page whose job is to remember the old name.",
    )
    #: block | warn | off
    stale_rules: str = knob(
        "block",
        doc="What the pre-commit hook does when the branch the work merges into (the item's recorded base, else the default branch) changed a rulebook since this branch forked and the change is not merged in: AGENTS.md, CLAUDE.md, CLAUDE.local.md, .ddflow/config.toml, the driver docs under docs/ddflow/drivers/, or any agent's native rules file. A session loads its rules once, so the edit is silently ignored here until merged. 'block' refuses and says `git merge <base>` then re-read the named files, 'warn' prints and allows, 'off' disables. Default 'block': it is precise, and the commit concluding that merge is never refused. The SessionStart hook reports the same drift but only informs.",
        choices=BLOCK_WARN_OFF,
        strictest=("block", "the hook refuses"),
    )
    #: block | warn | off
    readme_with_code: str = knob(
        "warn",
        doc="What `complete`, `gate status` and `brief` do about a TASK whose diff changes a path in readme_code_globs but none of readme_files, with no 'docs' outcome recorded for it (`gate skip <id> docs --reason ...`, or `gate record <id> docs --outcome passed --evidence ...` naming the section changed). 'warn' reports it (a `complete` warning, a line in `gate status` and in the item's `brief`), 'block' makes `complete` refuse, 'off' disables. Test files (a tests/, test/, spec/, specs/ or __tests__/ directory; test_*.*, *_test.*, *_spec.*, *.test.*, *.spec.*, conftest.py) and documentation (a docs/ or doc/ directory; .md, .rst, .adoc, .txt files) never count as code, and ddflow's own event-log commits (`.ddflow/**`) are not in the default readme_code_globs. When git cannot say what the task changed, `complete` says the check could not run (a warning, never a blocker). Default 'warn': a README is the user's, and the report names the one-line remedy.",
        choices=BLOCK_WARN_OFF,
        strictest=("block", "complete refuses"),
    )
    readme_code_globs: list[str] = knob(
        factory=lambda: ["ddflow/**"],
        doc="Paths whose change is user-visible and so should reach the README (readme_with_code), in git's glob pathspec syntax. Default `ddflow/**`, ddflow's own package; set a project's own source directories. Docs and `.ddflow/**` are not listed and so are exempt; a test or documentation file inside a listed path is exempt too.",
    )
    readme_files: list[str] = knob(
        factory=lambda: ["README.md"],
        doc="The files that count as updating the README for readme_with_code, as paths from the repository root (`docs/README.md` is not `README.md`). Default README.md.",
    )
    #: block | warn | off
    behind: str = knob(
        "warn",
        doc="What the pre-commit hook does when the branch is more than max_behind commits behind the branch its work merges into. 'block' refuses, 'warn' prints and allows, 'off' disables. Default 'warn': being behind is not by itself wrong, but drift compounds (a branch 98 commits behind had to be hand-ported). When git cannot tell (unborn HEAD, unresolvable base) either policy warns with the reason and never blocks.",
        choices=BLOCK_WARN_OFF,
        strictest=("block", "the hook refuses"),
    )
    max_behind: int = knob(
        50,
        doc="The commit count past which `behind` fires; at or below it the check is silent. Must be >= 1 -- 0 or a negative number is refused as invalid rather than read as 'off', which is the `behind` knob's job.",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1
            else 'must be an integer >= 1; to disable the check set [enforce].behind = "off"'
        ),
    )
