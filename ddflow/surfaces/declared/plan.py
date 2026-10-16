"""The `plan check` command, declared once. CLI-ONLY, on purpose.

`tool=""` with a `reason` is the parity exemption as a field: this is a pre-filing
convenience the coordinator runs on a document, and its output is a list to read, not
something an agent calls mid-task. Not being an MCP tool also means no tools/list bytes,
no tool schema, and no tier entry for it.
"""

from __future__ import annotations

from ..registry import CliPolicy, Command, Param
from ..tools._common import _api


def _text(out, a) -> str:
    """The findings (what to fix), then the waves (what could run together).

    A REFUSAL (a document that is not a plan) carries no findings, so its reason is the
    whole answer -- and it is printed, because the policy shows every exit.
    """
    d = out.data
    if "clean" not in d:
        return out.reason
    if d["clean"]:
        lines = [f"{d['source']}: {d['items']} item(s), no findings"]
    else:
        lines = [f"{d['source']}: {len(d['findings'])} finding(s)"]
        lines += [f"  {f['kind']}  {' '.join(f['ids'])}: {f['detail']}" for f in d["findings"]]
    lines += [f"  warning  {w['id']}: {w['detail']}" for w in d.get("warnings", [])]
    if d["waves"]:
        lines.append("waves:")
        lines += [f"  wave {w}: {iid}" for iid, w in d["waves"].items()]
    return "\n".join(lines)


#: The findings are the OUTPUT, so they are printed on EVERY exit -- the default prints
#: only a failure's reason on stderr, which would hide what the check exists to say.
_SHOWN = CliPolicy(shown=None)


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("plan", "check"),
        summary="check a proposed plan before filing it (globs, cycles, collisions)",
        tool="",
        reason=(
            "CLI-only by design: a pre-filing convenience on a document, whose output is a "
            "list to read -- not a tool an agent calls mid-task."
        ),
        call=lambda repo, a, agent: _api().plan_check(repo, a["source"]),
        payload=("source", "clean", "findings", "waves", "items", "warnings"),
        render=_text,
        cli=_SHOWN,
        params=(
            Param(
                "source",
                help="The plan file, or - for stdin.",
                cli_help="the plan file, or - for stdin",
                positional=True,
            ),
        ),
    ),
)
