"""The `[hooks]` section: what the PreToolUse guard refuses (DX-forbidden-commands-hook).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("hooks")
@dataclass
class HooksConfig:
    """The PreToolUse guard `ddflow hooks install --claude` adds: commands an agent is
    told never to run, refused before they run."""

    forbidden_commands: list[str] = knob(
        factory=lambda: [
            "pkill",
            "killall",
            "git stash",
            "git push",
            "git reset --hard @primary",
            "rm -rf .ddflow",
            "--force",
            "--force-with-lease",
        ],
        doc="Commands the PreToolUse hook (`ddflow hooks install --claude`) refuses in a Bash tool call, naming the safe alternative. Each entry is a word sequence matched, in order, against every command of a pipeline or `;`/`&&` list: `git stash` matches `git -C x stash push`, `--force` matches that flag anywhere, `rm -rf .ddflow` matches `rm -fr ./.ddflow`. A trailing `@primary` makes the entry apply only in the primary checkout, not in an item's worktree. Anything in allowed_commands is exempt. An instruction in a prompt is not enforcement: three subagents ran pkill against the brief that said never. The operator lifts an entry for ONE session with the environment variable DDFLOW_HOOKS_ALLOW (comma-separated entries, or `all`), and for the project by editing this list; [] turns the guard off. Every refusal is recorded as a session note (source forbidden-command) that `ddflow report` lists.",
    )
    allowed_commands: list[str] = knob(
        factory=lambda: ["ddflow review --force --reason"],
        doc='Commands exempt from forbidden_commands, in the same word-sequence form: the one `--force` ddflow itself documents, `ddflow review --force --reason "..."` for an extra review round. A command matching an entry here is allowed even when it also matches a forbidden one.',
    )
    fixture_max_age_months: int = knob(
        default=6,
        doc="A recorded payload fixture or a walked tier-2 checklist older than this many whole months is flagged for re-recording: by `ddflow harness record <id>`, and in the fixture gap report. A flag, never a failure.",
    )
    fixture_max_reply_bytes: int = knob(
        default=65536,
        doc="`ddflow harness record` keeps an MCP reply in session/mcp.json up to this many bytes, after redaction. A longer reply is replaced by `_truncated` with its size and this cap, so a fixture never reads as complete when it is not.",
    )
