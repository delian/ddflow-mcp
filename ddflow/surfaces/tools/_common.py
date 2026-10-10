"""What the tool table's argv/api builders share: the lazy `api` import, option and list
helpers, the duplicate-check answer, and the few tools whose builder is a function.

Lives beside the table, not in the engine (`surfaces/mcp.py`): every name here is read by a
`TOOLS` entry. `surfaces/mcp.py` re-exports them, so `ddflow.surfaces.mcp._opt` and friends
keep working.

The imports left inside functions are deliberate: `ddflow.api` reaches jinja2 and this
module is imported while the tool table is built (`scripts/bump.sh` imports it under an
interpreter that has none), and `TOOLS` is a real cycle with the package assembling it.
"""

from __future__ import annotations

from typing import Any

from ...config import csv_list
from ...core import outcome as O
from ...core.clock import WAIT_MAX_S
from ...services.adopt import AGENT_TARGETS
from ..vocabulary import sources


def _AGENT_KEYS() -> list[str]:
    """Every supported harness, from the one registry that defines them.

    The import is inside the function only to keep it out of this module's header; it is
    NOT deferred in any meaningful sense, because `TOOLS` calls this while it is being
    built, so `services.adopt` is imported when this module is. An earlier version of this
    docstring claimed the opposite — roborev checked `sys.modules` and disproved it. The
    accurate statement is that the list has ONE source, and a hand-kept copy in the tool
    description has drifted twice.
    """

    return list(AGENT_TARGETS)


#: `ddflow_wait` over MCP: shorter than the CLI's default, because the client -- not
#: ddflow -- decides when a tool call has hung, and a timed-out call is a lost answer.
#: Capped for the same reason, at the cap the CLI's wait has too; an agent that wants longer
#: calls again.
MCP_WAIT_DEFAULT_S = 300
MCP_WAIT_MAX_S = WAIT_MAX_S  # one cap for both surfaces: core.clock


def _wait_timeout(a: dict[str, Any]) -> float:
    """`timeout` as given (0 included -- it means "ask, do not sleep"), else the MCP
    default; never above the cap."""
    t = a.get("timeout")
    t = MCP_WAIT_DEFAULT_S if t is None else t
    try:
        return min(float(t), float(MCP_WAIT_MAX_S))
    except OverflowError:  # a JSON integer past a double's range is far past the cap
        return float(MCP_WAIT_MAX_S)


def _opt(flag: str, args: dict[str, Any], key: str | None = None) -> list[str]:
    """Build `--flag value`, or nothing when the caller did not supply one.

    There used to be a ``clearable`` parameter here, distinguishing "not supplied" from
    "supplied as empty" — a distinction argv erases and this had to rebuild, because
    over MCP `ddflow_update(id="X", needs="")` silently did nothing while
    `ddflow update X --needs ""` cleared the field. `ddflow_update` was its only caller,
    and that tool now goes through the typed `api` path, where `None` and `[]` are
    simply different values and no flag is needed.

    So it went, rather than staying as a parameter nothing passes: a branch no test can
    execute cannot be caught drifting, which is the reason its last caller was removed
    in the first place.
    """
    k = key or flag.lstrip("-").replace("-", "_")
    v = args.get(k)
    return [flag, str(v)] if v not in (None, "", []) else []


def _answer(a: dict[str, Any]):
    """The duplicate-check answer an add call carries: ``relation`` and ``check_only``."""
    from ...api._dedupe import Answer

    given = Answer.parse(str(a.get("relation", "") or ""))
    return Answer(given.relation, given.target, bool(a.get("check_only")))


def _rule_answer(a: dict[str, Any], relations: tuple[str, ...]) -> Any:
    """The duplicate-check answer a rule tool call carries, or None. More than one is
    refused (the CLI's flags are mutually exclusive; silently keeping one dropped the
    other, rubber-duck)."""
    given = [r for r in relations if (a.get(r) if r != "new" else bool(a.get("new")))]
    if len(given) > 1:
        raise ValueError(f"answer one of {', '.join(given)}, not several")
    if not given:
        return None
    rel = given[0]
    return _api().RuleDedupAnswer(rel, "" if rel == "new" else a[rel])


def _configure_reported(repo, a, agent):
    """`ddflow_configure`, telling the operator when it changed the review budget."""
    from ...api.setup import report_budget_change

    edit = _api().ConfigEdit(
        set=a.get("set", "") or "",
        value=a.get("value", "") or "",
        append_toml=a.get("toml", "") or "",
        filter=a.get("filter", "") or "",
        explain=True,
        local=bool(a.get("local")),
    )
    return report_budget_change(repo, edit, _api().configure(repo, edit, agent=agent), agent=agent)


def _bisect(repo, a):
    """`ddflow_bisect`. `glob`, `repeat` and `max_runs` are CLI-only: the tools/list byte budget."""
    from ...api import bisect as B

    return B.bisect(
        repo,
        a.get("victim", "") or "",
        a.get("cmd", "") or "",
        candidates=a.get("candidates", "") or "",
        glob=a.get("glob", "") or "",
        timeout_s=float(a.get("timeout") or 600),
        repeat=a.get("repeat", 1),
        max_runs=a.get("max_runs", 200),
    )


def _reopening(a: dict[str, Any]) -> bool:
    """`ddflow_bug_invalid`'s mode, for its `api` and its `payload` alike. Strict: the
    two modes do opposite things, so a `"false"` string must not pick one."""
    mode = a.get("reopen", False)
    if not isinstance(mode, bool):
        raise ValueError("reopen must be true or false")
    return mode


def _bug_reopen(repo, a: dict[str, Any], *, agent: str):
    """`bug reopen` (B7bdcc6b212), served by `ddflow_bug_invalid` with `reopen`. Evidence
    a reopen cannot record is refused (exit 3), not dropped; an empty one carries nothing."""
    from ...api.bug_reopen import bug_reopen

    if a.get("evidence"):
        return O.refused(
            "bug.reopened",
            "evidence is for closing a bug as invalid; a reopen records only its reason.",
            id=a["id"],
        )
    return bug_reopen(repo, a["id"], reason=a.get("reason", "") or "", agent=agent)


#: Whether this process has handed the tool table to `ddflow_upgrade` yet.
_upgrade_vocabulary = {"provided": False}


def _api():
    """Imported lazily: `surfaces` may reach `api`, and doing it at call time keeps the
    module import graph flat for anything that only wants the tool table."""
    from ... import api

    if not _upgrade_vocabulary["provided"]:
        # The MCP server's side of what the CLI registers at import: the tool table, which
        # `ddflow_upgrade` judges names by. Here, not at import, to keep the table api-free.
        api.refs.provide_upgrade_vocabulary(None, sources()[1])
        _upgrade_vocabulary["provided"] = True  # only once it has happened
    return api


def _regression_tests(args: dict[str, Any]) -> str | list[str]:
    """`regression_test` (a string -- or a list, from a client that sent one anyway) and
    `regression_tests` as one value for `api.bug_fixed`, which does the splitting.

    Not `_list_or_none`: its plain comma split cuts a parametrize id like `t[1,2]`. A
    lone string stays a string, so it is recorded verbatim as before (B227585c781).
    """
    single, many = args.get("regression_test") or "", args.get("regression_tests") or []
    if isinstance(single, str) and not many:
        return single
    as_list = ([single] if single else []) if isinstance(single, str) else list(single)
    return [*as_list, *([many] if isinstance(many, str) else many)]


def _list_or_none(args: dict[str, Any], key: str) -> list[str] | None:
    """`None` when absent, a list when supplied — INCLUDING the empty one.

    The whole point of the typed path. `""` supplied deliberately means "clear this
    field", and over argv that was indistinguishable from not supplying it at all: over
    MCP a dependency could be added and never removed, which is exactly the operation
    the loop detector tells you to perform.
    """
    if key not in args or args[key] is None:
        return None

    return csv_list(args[key]) if isinstance(args[key], str) else list(args[key])


def _all_tools() -> dict[str, dict[str, Any]]:
    """The whole registry, for the one tool that hands it on (`ddflow_help`). Read at call
    time: a slice module cannot import the package that is still assembling it."""
    from . import TOOLS

    return TOOLS
