"""One config write pipeline (B-uni-config-writes): `configwrite.apply_edit`.

Every config change -- `config --set`, `--append-toml`, `ddflow_configure`, the workflow
writers, `reviewers add|detect`, `export enable` -- goes through it, with one definition of
the layers and one set of guards, and with values typed rather than guessed from text.
"""

from __future__ import annotations

import tomllib

import pytest
from conftest import run_cli

from ddflow.api import review as AR
from ddflow.api import workflow as AW
from ddflow.infra import tomlcfg as TC
from ddflow.services import configwrite as CW

HUMAN = '[gate.plan_approved]\nhuman = true\nprompt = "p"\n'


def _committed(repo):
    return tomllib.loads((repo / ".ddflow" / "config.toml").read_text("utf-8"))


# -- the layers are defined once ---------------------------------------------------------


def test_layer_paths_are_defined_once(tmp_path):
    assert TC.layer_path(tmp_path) == tmp_path / ".ddflow" / "config.toml"
    assert TC.layer_path(tmp_path, "local", "reviewers.toml") == (
        tmp_path / ".ddflow" / "local" / "reviewers.toml"
    )
    assert TC.config_paths(tmp_path, "gates.toml") == (
        TC.layer_path(tmp_path, "file"),
        TC.layer_path(tmp_path, "file", "gates.toml"),
        TC.layer_path(tmp_path, "local"),
        TC.layer_path(tmp_path, "local", "gates.toml"),
    )
    assert CW.config_file(tmp_path, local=True) == TC.layer_path(tmp_path, "local")
    with pytest.raises(ValueError, match="unknown config layer"):
        TC.layer_path(tmp_path, "global")


# -- typed values (bug B33455cd577) ------------------------------------------------------


def test_a_gate_command_that_looks_like_a_number_stays_a_string(repo):
    """`workflow gate g --command 5 --prompt true` wrote `command = 5`, `prompt = true`:
    the writer re-guessed the type of a pre-serialised string."""
    out = AW.gate(repo, AW.GateEdit(id="probe", command="5", prompt="true", title="1.5"))
    assert out.exit == 0, out.reason
    gate = _committed(repo)["gate"]["probe"]
    assert gate == {"command": "5", "prompt": "true", "title": "1.5"}


def test_a_typed_value_is_written_as_its_type_and_a_spelled_one_as_the_literal_it_spells(repo):
    res = CW.apply_edit(
        repo,
        CW.SetPairs([("lease.ttl_s", 600), ("gates.required", ["a", "5"])]),
        guards=CW.Guards(False),
    )
    assert res.error == "", res.error
    assert _committed(repo)["lease"] == {"ttl_s": 600}
    assert _committed(repo)["gates"]["required"] == ["a", "5"]
    res = CW.apply_edit(repo, CW.SetPairs([("lease.ttl_s", CW.Spelled("700"))]))
    assert res.error == "" and _committed(repo)["lease"] == {"ttl_s": 700}
    res = CW.apply_edit(repo, CW.SetPairs([("flow.tag_prefix", "700")]))
    assert res.error == "" and _committed(repo)["flow"]["tag_prefix"] == "700"


# -- the same guards for every kind of edit ----------------------------------------------


@pytest.mark.parametrize(
    "edit",
    [
        CW.SetPairs([("gate.x.human", False)]),
        CW.AppendText("[gate.x]\nhuman = false\n"),
        CW.Block("[gate.x]\nhuman = false\n"),
    ],
    ids=["set", "append", "block"],
)
@pytest.mark.parametrize("layer", ["file", "local"])
def test_no_edit_clears_a_human_gate_flag(repo, edit, layer):
    res = CW.apply_edit(repo, edit, layer=layer)
    assert "human" in res.error, res.error
    assert not TC.layer_path(repo, layer).exists()


@pytest.mark.parametrize("kind", [CW.AppendText, CW.Block])
def test_a_text_edit_cannot_drop_a_human_gate_from_a_pipeline(repo, kind):
    run_cli(repo, "config", "--append-toml", HUMAN)
    run_cli(repo, "workflow", "pipeline", "task", "research,plan_approved,implement")
    # The local layer is read last: its pipeline would win on this machine, with nothing
    # in any committed diff to show it.
    res = CW.apply_edit(
        repo, kind('[gates]\ntask_pipeline = ["research", "implement"]\n'), layer="local"
    )
    assert "human-approval" in res.error, res.error
    assert not TC.layer_path(repo, "local").exists()


def test_an_appended_unknown_section_is_refused_like_a_set(repo):
    for edit in (CW.AppendText("[nosuch]\nx = 1\n"), CW.Block("[nosuch]\nx = 1\n")):
        res = CW.apply_edit(repo, edit)
        assert "nosuch" in res.error, res.error
    assert not (repo / ".ddflow" / "config.toml").exists()


def test_appending_a_pipeline_naming_an_undefined_gate_is_refused(repo):
    """The workflow judgement was `--set`'s alone; an append reached the same state."""
    res = CW.apply_edit(repo, CW.AppendText('[gates]\ntask_pipeline = ["implement", "ghost"]\n'))
    assert "ghost" in res.error, res.error
    ok = CW.apply_edit(repo, CW.AppendText('[gates]\ntask_pipeline = ["implement"]\n'))
    assert ok.error == "", ok.error


@pytest.mark.parametrize("kind", ["set", "append", "block"])
def test_a_dry_run_writes_nothing_for_any_edit(repo, kind):
    edit = {
        "set": CW.SetPairs([("lease.ttl_s", 5)]),
        "append": CW.AppendText("[lease]\nttl_s = 5\n"),
        "block": CW.Block("[lease]\nttl_s = 5\n"),
    }[kind]
    res = CW.apply_edit(repo, edit, dry_run=True)
    assert res.error == "" and "ttl_s = 5" in res.text
    assert not (repo / ".ddflow" / "config.toml").exists()


# -- one context for every writer --------------------------------------------------------


def test_a_workflow_write_syncs_gitattributes_like_configure(repo):
    """The workflow writers never synced `.gitattributes`; `ddflow_configure` did."""
    run_cli(repo, "config", "--append-toml", '[lease]\nappend_only_globs = ["LOG.md"]\n')
    (repo / ".gitattributes").write_text("")
    out = AW.gate(repo, AW.GateEdit(id="lint", command="ruff check"))
    assert out.exit == 0, out.reason
    assert "LOG.md merge=union" in (repo / ".gitattributes").read_text()


def test_a_local_edit_does_not_touch_gitattributes(repo):
    run_cli(repo, "config", "--append-toml", '[lease]\nappend_only_globs = ["LOG.md"]\n')
    (repo / ".gitattributes").unlink(missing_ok=True)
    res = CW.apply_edit(repo, CW.SetPairs([("lease.ttl_s", 9)]), layer="local")
    assert res.error == "" and res.attributes_added == []
    assert not (repo / ".gitattributes").exists()


# -- one reviewer builder on both paths --------------------------------------------------


def test_reviewers_add_writes_the_local_layer_unless_shared(repo):
    out = AR.reviewers_add(repo, name="r1", model="m/x", base_url="http://127.0.0.1:1/v1")
    assert out.exit == 0, out.reason
    local = repo / ".ddflow" / "local" / "reviewers.toml"
    assert tomllib.loads(local.read_text())["reviewer"][0] == {
        "name": "r1",
        "model": "m/x",
        "base_url": "http://127.0.0.1:1/v1",
    }
    out = AR.reviewers_add(repo, name="r2", model='q"uote', shared=True)
    assert out.exit == 0, out.reason
    assert _committed(repo)["reviewer"][0]["model"] == 'q"uote'


def test_reviewers_add_refuses_an_unknown_preset_and_an_agent_command_reviewer(repo):
    out = AR.reviewers_add(repo, preset="nosuch")
    assert out.exit != 0 and "unknown preset" in out.reason
    out = AR.reviewers_add(repo, name="c", preset="claude-cli", person=False, agent="a1")
    assert out.exit == 3 and out.data["reviewer_refused"], out.reason  # a command reviewer
    out = AR.reviewers_add(repo, name="c", preset="claude-cli", person=True)
    assert out.exit == 0, out.reason


def test_a_name_given_wins_over_the_preset_and_a_newer_format_is_a_refusal(repo):
    out = AR.reviewers_add(repo, preset="claude-cli", name="mine", person=True)
    assert out.exit == 0, out.reason
    names = [r["name"] for r in tomllib.loads(open(out.data["path"]).read())["reviewer"]]
    assert names == ["mine"]
    (repo / ".ddflow" / "config.toml").write_text("format = 99\n")
    out = AR.reviewers_add(repo, name="n", model="m", shared=True)
    assert out.exit == 3 and "format" in out.reason, out.reason


def test_a_failing_detect_write_reports_the_reason_not_a_missing_payload(repo, monkeypatch):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("format = 99\n")
    monkeypatch.setattr(
        "ddflow.services.review.detect",
        lambda: [("http://127.0.0.1:1/v1", "local", ["m/x"])],
    )
    out = AR.reviewers_detect(repo, write=True, shared=True)
    assert out.exit == 3 and "format" in out.reason and out.data["text"] == "", out.reason
