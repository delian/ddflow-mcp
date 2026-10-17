"""The `chore` commands, declared once. CLI-ONLY (decision D-chore-class): `commit` is the
whole claim/commit/merge/complete ceremony for files in `[chore].globs`, and `approve` is a
person's act that no agent identity may do. Neither has a tool: tools/list has almost no
headroom, and an approval has no business on a surface an agent drives."""

from __future__ import annotations

from ..registry import CliPolicy, Command, Param
from ..tools._common import _api

_SHOWN = CliPolicy(shown=None)


def _text(out, a) -> str:
    return out.data.get("text") or out.reason


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("chore", "commit"),
        summary="commit, merge and complete files in [chore].globs in one step, with no reviewer",
        tool="",
        reason=(
            "CLI-only by design (D-chore-class): tools/list has no headroom, and the chore "
            "class rests on a person's approval an agent surface must not carry."
        ),
        call=lambda repo, a, agent: _api().chore_commit(
            repo,
            list(a.get("files") or []),
            message=a.get("message") or "",
            model=a.get("model") or "",
            agent=agent,
        ),
        payload=("item", "sha", "files", "skipped_gates", "approved_by", "why", "stage"),
        render=_text,
        cli=_SHOWN,
        params=(
            Param(
                "files",
                positional=True,
                nargs="*",
                cli_only=True,
                cli_help="the files (or directories) of this checkout to land; each must match [chore].globs",
            ),
            Param(
                "message",
                short="-m",
                required=True,
                cli_help="the commit message, and the chore's title",
            ),
            Param(
                "model",
                default="",
                cli_help="the author's model family, recorded on the completion",
            ),
        ),
    ),
    Command(
        path=("chore", "approve"),
        summary="a person approves the current [chore].globs (refused under an agent identity)",
        tool="",
        reason="a person's act: refused for every agent identity, so no tool carries it",
        call=lambda repo, a, agent: _api().chore_approve(repo, agent=agent, note=a.get("note") or ""),
        payload=("digest", "globs", "approved_by"),
        render=_text,
        cli=_SHOWN,
        params=(Param("note", default="", cli_help="why, recorded with the approval"),),
    ),
)
