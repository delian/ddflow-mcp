"""B-rc-participation: a declared record kind is searchable, recallable and an MCP resource
with no code of its own (RC1, RC4, RC7). The kind below exists only in this file.
"""

from __future__ import annotations

import json

import pytest

from ddflow.api import records as R
from ddflow.core import outcome as O
from ddflow.core import participation as PA
from ddflow.core import provenance as PV
from ddflow.surfaces.cli import main
from ddflow.surfaces.mcp import Server

WORDS = "quarterly gizmo calibration needs a torque wrench before every shipment"


def _toy(**over) -> R.RecordKind:
    return R.RecordKind(
        name="toy",
        def_kind="claim",
        summary="a toy record for tests",
        fields=(
            R.FieldSpec("title", required=True),
            R.FieldSpec("body", default=""),
            R.FieldSpec("tags", "array", default=()),
        ),
        text=("body", "tags"),
        columns=("title",),
        recall_hint="check the toy before you ship",
        **over,
    )


@pytest.fixture
def toy():
    kind = R.declare(_toy())
    yield kind
    R.forget("toy")


@pytest.fixture
def run_cli(capsys):
    """The CLI in this process, where the throwaway kind is declared (a subprocess has none)."""

    def run(repo, *argv):
        try:
            code = main(["--repo", str(repo), *argv])
        except SystemExit as exc:  # argparse
            code = int(exc.code or 0)
        got = capsys.readouterr()
        return code, got.out, got.err

    return run


@pytest.fixture
def proj(repo, toy, monkeypatch, run_cli):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "off")
    assert run_cli(repo, "init")[0] == 0
    return repo


def _add(repo, rid, title, body="", tags=()):
    out = R.record_add(
        repo, R.KINDS["toy"], rid, {"title": title, "body": body, "tags": list(tags)}
    )
    assert out.exit == O.OK, out.reason


# -- declaration -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "word"),
    [
        ({"applies": ("nope",)}, "applies"),
        ({"export": R.ExportSpec(text=("nope",))}, "export"),
        ({"export": R.ExportSpec(text=("body",), meta=("tags",), title="{missing}")}, "missing"),
        ({"export": R.ExportSpec(title="{title")}, "template"),
        ({"feed": R.FeedSpec(summary="{ghost} changed")}, "ghost"),
        ({"recall_cap": 0}, "recall_cap"),
    ],
)
def test_a_declaration_naming_a_field_the_kind_lacks_is_refused(over, word):
    with pytest.raises(ValueError, match=word):
        _toy(**over)


def test_good_declarations_are_accepted_and_inert_by_default():
    kind = _toy(
        applies=("tags",),
        export=R.ExportSpec(text=("body",), meta=("tags",), title="{title} [{status}] {id}"),
        feed=R.FeedSpec(binding=True, summary="{id}: {title}"),
    )
    assert kind.applies == ("tags",) and kind.feed.binding
    plain = _toy()
    assert plain.applies == () and plain.export is None and plain.feed is None
    assert plain.search and plain.recall and plain.resource


def test_declaring_registers_the_participation_and_forgetting_removes_it(toy):
    assert PA.DECLARED["toy"].label == "TOY" and PV.TABLE_KIND["toy"] == "toy"
    assert ("toy", "TOY", "check the toy before you ship") in PA.recall_entries()
    R.forget("toy")
    assert "toy" not in PA.DECLARED and "toy" not in PV.TABLE_KIND


def test_rule_keeps_its_own_search_and_is_not_recalled_or_served_yet():
    d = PA.DECLARED["rule"]
    assert not d.searched and not d.recall
    assert "rule" not in {t for t, _, _ in PA.recall_entries()}
    assert "rule" not in {r["name"].split()[0].lower() for r in R.record_resource_listing()}


# -- search ----------------------------------------------------------------------------


def test_a_declared_kind_is_found_by_search_kind_with_no_other_edit(proj, run_cli):
    _add(proj, "t1", "Gizmo calibration", WORDS, ["shipping"])
    _add(proj, "t2", "Unrelated gardening", "tomatoes need sun")
    code, out, err = run_cli(proj, "--json", "search", "--kind", "toy", "torque wrench")
    assert code == 0, (out, err)
    rows = json.loads(out)["rows"]
    assert [r["id"] for r in rows][:1] == ["t1"] and {r["kind"] for r in rows} == {"toy"}
    assert "torque" in rows[0]["snippet"] or "calibration" in rows[0]["snippet"]


def test_search_narrows_a_declared_kind_by_state_owner_and_since(proj, run_cli):
    _add(proj, "t1", "Gizmo calibration", WORDS)
    _add(proj, "t2", "Gizmo shipping", WORDS + " twice")
    R.record_remove(proj, R.KINDS["toy"], "t2", reason="obsolete")
    got = lambda *a: [  # noqa: E731
        r["id"]
        for r in json.loads(run_cli(proj, "--json", "search", "--kind", "toy", *a)[1])["rows"]
    ]
    assert sorted(got("torque")) == ["t1", "t2"]
    assert got("--state", "retired", "torque") == ["t2"]
    assert got("--state", "active", "torque") == ["t1"]
    assert got("--since", "2999-01-01", "torque") == []
    me = json.loads(run_cli(proj, "--json", "search", "--kind", "toy", "torque")[1])["rows"][0][
        "owner"
    ]
    assert got("--owner", me, "torque") != [] and got("--owner", "nobody", "torque") == []


def test_an_unknown_kind_is_refused_listing_the_declared_ones(proj, run_cli):
    code, out, err = run_cli(proj, "search", "--kind", "gizmo", "x")
    assert code != 0 and "toy" in (out + err)


def test_the_declared_kinds_log_events_are_not_listed_twice(proj, run_cli):
    _add(proj, "t1", "Gizmo calibration", WORDS)
    rows = json.loads(run_cli(proj, "--json", "search", "--kind", "toy,log", "torque wrench")[1])[
        "rows"
    ]
    assert [r["kind"] for r in rows] == ["toy"]


def test_a_kind_that_opts_out_of_search_is_not_found(proj, run_cli):
    R.forget("toy")
    R.declare(_toy(search=False))
    _add(proj, "t1", "Gizmo calibration", WORDS)
    code, _out, _err = run_cli(proj, "search", "--kind", "toy", "torque")
    assert code != 0


def _tables(body):
    return [k for k in body if k != "schema"]


# -- recall ----------------------------------------------------------------------------


def test_a_declared_kind_is_recalled_under_its_label_fenced_as_data(proj, run_cli):
    _add(proj, "t1", "Gizmo calibration", WORDS, ["shipping"])
    code, out, err = run_cli(proj, "recall", "gizmo torque wrench")
    assert code == 0, (out, err)
    assert "## TOY  — check the toy before you ship" in out
    assert "Gizmo calibration" in out and "<ddflow-record" in out and 'kind="toy"' in out
    data = json.loads(run_cli(proj, "--json", "recall", "gizmo torque wrench")[1])
    assert data["toy"][0]["id"] == "t1" and data["toy"][0]["kind"] == "TOY"
    for word in ("toy", "TOY"):
        only = json.loads(run_cli(proj, "--json", "recall", "--sources", word, "torque")[1])
        assert _tables(only) == ["toy"]


def test_a_retired_record_is_not_recalled(proj, run_cli):
    _add(proj, "t1", "Gizmo calibration", WORDS)
    R.record_remove(proj, R.KINDS["toy"], "t1", reason="obsolete")
    out = run_cli(proj, "--json", "recall", "--sources", "toy", "torque wrench")
    assert out[0] == 2 or "toy" not in _tables(json.loads(out[1]))


def test_a_large_kind_is_capped_per_kind_and_does_not_crowd_the_others(proj, run_cli):
    for i in range(200):
        _add(proj, f"t{i:03d}", f"Gizmo calibration {i}", WORDS + f" lot {i}")
    code, out, _ = run_cli(proj, "--json", "recall", "--limit", "50", "gizmo torque wrench")
    assert code == 0
    assert len(json.loads(out)["toy"]) <= PA.DEFAULT_RECALL_CAP
    R.forget("toy")
    R.declare(_toy(recall_cap=7))
    out = json.loads(
        run_cli(
            proj,
            "--json",
            "recall",
            "--limit",
            "50",
            "--max-chars",
            "100000",
            "gizmo torque wrench",
        )[1]
    )
    assert len(out["toy"]) == 7


def test_the_fixed_sources_are_unchanged_and_come_first(proj, run_cli):
    from ddflow.infra.store import RECALL_SOURCES, recall_sources

    assert recall_sources()[: len(RECALL_SOURCES)] == RECALL_SOURCES
    assert [t for t, _, _ in recall_sources()[len(RECALL_SOURCES) :]] == ["toy"]
    R.forget("toy")
    assert recall_sources() == RECALL_SOURCES


# -- resources -------------------------------------------------------------------------


def _call(repo, method, **params):
    return Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def test_a_declared_kind_is_listed_and_read_as_an_mcp_resource(proj, run_cli):
    _add(proj, "t1", "Gizmo calibration", WORDS)
    _add(proj, "t2", "Second", "other")
    listed = _call(proj, "resources/list")["result"]["resources"]
    mine = [r for r in listed if r["uri"] == "ddflow://records/toy"]
    assert len(mine) == 1 and set(mine[0]) == {"uri", "name", "description", "mimeType"}
    assert "ddflow://records/toy/{id}" in mine[0]["description"]
    text = _call(proj, "resources/read", uri="ddflow://records/toy")["result"]["contents"][0][
        "text"
    ]
    assert "2 in all" in text and "t1" in text and "t2" in text
    one = _call(proj, "resources/read", uri="ddflow://records/toy/t1")["result"]["contents"][0][
        "text"
    ]
    assert "<ddflow-record" in one and "status: active" in one and "history: 1 entries" in one
    assert "Gizmo calibration" in one


def test_a_resource_shows_the_successor_chain_and_unknowns_are_invalid_params(proj, run_cli):
    _add(proj, "t1", "Gizmo calibration", WORDS)
    _add(proj, "t2", "Replacement", "different words altogether")
    from ddflow.api import record_lifecycle as LC

    LC.record_supersede(proj, R.KINDS["toy"], "t1", by="t2", reason="better")
    one = _call(proj, "resources/read", uri="ddflow://records/toy/t1")["result"]["contents"][0][
        "text"
    ]
    assert "superseded by: t2" in one
    for uri in ("ddflow://records/toy/zzz", "ddflow://records/nope", "ddflow://records/"):
        reply = _call(proj, "resources/read", uri=uri)
        assert reply["error"]["code"] == -32602, uri


def test_the_resource_list_is_bounded_newest_first_and_redacted(proj, run_cli):
    secret = "sk-" + "abcdefghijklmnop1234567890"
    for i in range(R.RESOURCE_ROWS + 5):
        _add(
            proj,
            f"t{i:03d}",
            f"Record {i} {secret if i == R.RESOURCE_ROWS + 4 else ''}",
            f"body {i}",
        )
    text = _call(proj, "resources/read", uri="ddflow://records/toy")["result"]["contents"][0][
        "text"
    ]
    lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert len(lines) == R.RESOURCE_ROWS and f"{R.RESOURCE_ROWS + 5} in all" in text
    assert lines[0].startswith("- t0" + str(R.RESOURCE_ROWS + 4)) and secret not in text
    whole = _call(proj, "resources/read", uri=f"ddflow://records/toy/t{R.RESOURCE_ROWS + 4:03d}")[
        "result"
    ]["contents"][0]["text"]
    assert secret not in whole


def test_a_kind_that_opts_out_of_resources_is_not_listed(proj, run_cli):
    R.forget("toy")
    R.declare(_toy(resource=False))
    uris = [r["uri"] for r in _call(proj, "resources/list")["result"]["resources"]]
    assert "ddflow://records/toy" not in uris
    assert "error" in _call(proj, "resources/read", uri="ddflow://records/toy")


def test_a_mixed_kind_request_lists_a_declared_record_once_and_a_legacy_kind_as_before(
    proj, run_cli
):
    _add(proj, "t1", "Gizmo calibration", WORDS)
    code, out, err = run_cli(
        proj,
        "rule",
        "add",
        "--id",
        "r1",
        "--title",
        "Torque wrench rule",
        "--content",
        WORDS,
        "--new",
    )
    assert code == 0, (out, err)
    for kinds in ("toy,rule", "toy,skill", "toy,session", "toy,log"):
        rows = json.loads(run_cli(proj, "--json", "search", "--kind", kinds, "torque wrench")[1])[
            "rows"
        ]
        assert [r["id"] for r in rows if r["kind"] == "toy"] == ["t1"], kinds
        assert not [r for r in rows if r["kind"] == "log" and "toy" in r["id"]], kinds
    rows = json.loads(run_cli(proj, "--json", "search", "--kind", "rule", "torque wrench")[1])[
        "rows"
    ]
    assert [r["id"] for r in rows if r["kind"] == "rule"].count("r1") == 1
