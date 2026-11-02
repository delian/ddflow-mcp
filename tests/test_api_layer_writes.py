"""The application layer: the write tools and the projection across exit codes.

Split from tests/test_api_layer.py (rule r-small-files); helpers live in api_layer_support.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_layer_support import (
    FAIL,
    OK,
)
from conftest import run_cli

from ddflow import api
from ddflow.core import outcome as O

# -- the write tools, which the shape comparison cannot reach ---------------------------


def test_recording_a_decision_over_mcp_records_it(repo):
    """`ddflow_decision_add` on the typed path, end to end.

    It is exempt from `MIGRATED_WIRE_SHAPES` because running it twice records two
    decisions with different ids — so the contract is checked by asserting what it DID,
    which is the thing a consumer actually depends on.
    """
    import json as _json

    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_decision_add",
                "arguments": {
                    "title": "Use pluggy",
                    "decision": "every stage is a Step",
                    "globs": "ddflow/steps/*",
                },
            },
        }
    )
    assert reply["result"]["isError"] is False, reply
    text = reply["result"]["content"][0]["text"]
    body = _json.loads(text[text.index("{") :])
    assert set(body) == {"schema", "id"}, (
        f"the wire body was {body}, not {{'schema': ..., 'id': ...}}"
    )

    _code, out, _ = run_cli(repo, "--json", "decision", "list")
    rows = _json.loads(out)
    assert [r["id"] for r in rows] == [body["id"]], "the decision was not recorded"
    assert rows[0]["globs"] == ["ddflow/steps/*"], "globs were dropped on the way through"


def test_a_decision_with_no_globs_warns_on_BOTH_surfaces(repo):
    """The bug class this layer exists for.

    A decision with no globs cannot be surfaced automatically to an agent working the
    code it governs — it will only ever be found by someone already looking. That
    warning was computed inside the human branch of `_decision_add`, so the agent
    driving over MCP, which is always JSON, was never told. It is now a field on the
    Outcome and both surfaces read it.
    """

    run_cli(repo, "init")
    without = api.decision_add(repo, api.decisions.Draft(title="t", decision="d"))
    with_globs = api.decision_add(repo, api.decisions.Draft(title="t", decision="d", globs="x.py"))
    assert without.data["ungoverned"] is True
    assert with_globs.data["ungoverned"] is False


def test_superseding_over_mcp_refuses_without_a_replacement(repo):
    """A decision is never simply deleted. The refusal is the behaviour."""

    run_cli(repo, "init")
    run_cli(repo, "decision", "add", "--id", "D1", "--title", "t", "--decision", "d")
    assert api.decision_supersede(repo, "D1", by="").exit == FAIL
    assert api.decision_supersede(repo, "NOPE", by="D2").exit == FAIL
    assert api.decision_supersede(repo, "D1", by="D2").exit == OK


def test_a_decision_must_say_what_was_DECIDED(repo):
    """The refusal that gives the record its value, probed for the first time.

    `_decision_add` has always refused a record with no `--decision`: notes about what
    was *discussed* are indistinguishable from a decision, and the next agent cannot
    act on them. Nothing tested it — mutating the guard to `if False and ...` left the
    entire decisions suite green, which is how a deliberate refusal quietly becomes
    optional. Found by mutation-testing the B37 migration of this family.
    """

    run_cli(repo, "init")
    refused = api.decision_add(repo, api.decisions.Draft(title="we talked about caching"))
    assert refused.exit == FAIL, refused
    assert "what was DECIDED" in refused.reason

    _code, out, _ = run_cli(repo, "--json", "decision", "list")
    assert json.loads(out) == [], "a refused decision was recorded anyway"

    accepted = api.decision_add(
        repo, api.decisions.Draft(title="caching", decision="cache on the fingerprint")
    )
    assert accepted.exit == OK, accepted


def test_superseded_decisions_are_hidden_but_COUNTED(repo):
    """ "3 superseded, --all to include them" needs the number to be real.

    `decision list` shows only what is in force, which is right — but a reader who is
    not told that anything was hidden cannot know to ask. The count is on the Outcome so
    both surfaces have it; mutating it to a constant `0` also left the suite green.
    """

    run_cli(repo, "init")
    run_cli(repo, "decision", "add", "--id", "D1", "--title", "old", "--decision", "a")
    run_cli(repo, "decision", "add", "--id", "D2", "--title", "new", "--decision", "b")
    run_cli(repo, "decision", "supersede", "D1", "--by", "D2")

    live = api.decision_list(repo)
    assert [r["id"] for r in live.data["rows"]] == ["D2"]
    assert live.data["hidden"] == 1, "the superseded decision was hidden AND uncounted"

    every = api.decision_list(repo, all=True)
    assert {r["id"] for r in every.data["rows"]} == {"D1", "D2"}
    assert every.data["hidden"] == 0, "--all hides nothing, so it must count nothing"


# -- the projection must survive every exit code ----------------------------------------


def test_a_projection_has_the_same_SHAPE_whatever_the_outcome():
    """`Outcome.body(("a", "b"))` on an outcome that only carries `a`.

    This indexed the keys, so an operation with less to say produced a KeyError inside
    the MCP dispatcher and the caller got a crash where the point was to deliver the
    refusal. `workflow drop` on a gate in no pipeline is exactly that outcome.

    A consumer branches on the shape; an explicit `null` is branchable and a missing key
    is not. Probed at the unit level because the two guards for it — this, and every api
    function carrying its projected keys on every path — are redundant by design, so
    neither is observable through the other.
    """
    partial = O.nothing("k", "nothing to do", gate="g")
    assert partial.body(("gate", "removed_from", "applied")) == {
        "gate": "g",
        "removed_from": None,
        "applied": None,
    }
    assert O.failed("k", "broke").body(("a",)) == {"a": None}
    # A single-key payload still indexes: that key IS the body, and inventing a `null`
    # body would turn "the operation has no rows" into "the operation returned nothing".
    assert O.ok("k", rows=[]).body("rows") == []


def test_setting_a_pipeline_names_the_undefined_gate_and_guesses_the_intent(repo):
    """Refused BEFORE the write, with the near-miss named.

    `services/configwrite` also refuses an incoherent pipeline, so this check is
    defence-in-depth — which is why it needs its own probe: disabling it left every test
    green because the second guard caught the write anyway, with a worse message. The
    message is the point. An unknown id in a pipeline blocks every item that enters it
    forever, and "did you mean 'unit_tests'?" is the difference between a typo found now
    and a queue that stops moving tomorrow.
    """

    run_cli(repo, "init")
    out = api.workflow_pipeline(repo, "task", "unit_testz", dry_run=True)
    assert out.exit == FAIL, out
    assert out.data["unknown"] == ["unit_testz"], out.data
    assert "did you mean 'unit_tests'?" in out.reason, out.reason
    assert out.data["applied"] is False


def test_an_incoherent_workflow_is_REPORTED_as_incoherent(repo):
    """`coherent` is the one field a caller uses to decide whether to trust the rest.

    Written straight into `config.toml` rather than through the writer, because the
    writer refuses incoherent edits — which means the only way a project GETS an
    incoherent workflow is by editing the file, and so the only way to test the report
    is the same way.
    """

    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[gates]\ntask_pipeline = ["ghost_gate"]\n')

    out = api.workflow_show(repo)
    assert out.exit == FAIL, "an incoherent workflow reported success"
    assert out.data["coherent"] is False, out.data
    assert out.data["findings"], "no finding explains what is wrong"


def test_render_only_data_never_reaches_the_wire():
    """`_`-prefixed keys are for the local prose renderer.

    The alternative to carrying them is folding the log a second time in the surface,
    which is precisely the duplication this layer removes — `cmd_status` needs the
    completed-task OBJECTS to sort by `completed_at`, not a list of dicts. Carrying them
    without a rule would serialise live dataclasses to every MCP caller as repr strings.
    """
    out = O.ok("status", tasks={"total": 3}, _render={"objects": object()})
    assert out.body() == {"tasks": {"total": 3}}, out.body()
    assert "_render" in out.data, "the renderer still needs it"
