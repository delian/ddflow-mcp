"""DRV-cli: harness add | lint | test | probe and model show."""

from __future__ import annotations

from ddflow.api import harness as H
from ddflow.api import harness_drv as D
from ddflow.api import model_cmds as M
from ddflow.core import outcome as O


def test_add_writes_a_skeleton_with_every_fact_left_out(tmp_path):
    out = H.harness_view(tmp_path, what="add", id="acme")
    assert out.ok
    text = (tmp_path / ".ddflow/local/harnesses/acme.toml").read_text()
    assert 'id = "acme"' in text and '"hooks"' in text
    assert "[mcp]" not in text.replace("# [mcp]", "")  # nothing guessed
    again = H.harness_view(tmp_path, what="add", id="acme")
    assert again.exit == O.FAIL  # never overwritten


def test_add_refuses_a_shipped_id_and_a_bad_id(tmp_path):
    assert H.harness_view(tmp_path, what="add", id="claude").exit == O.FAIL
    assert H.harness_view(tmp_path, what="add", id="Bad Id").exit == O.FAIL


def test_lint_of_the_skeleton_names_what_is_missing(tmp_path):
    H.harness_view(tmp_path, what="add", id="acme")
    out = H.harness_view(tmp_path, what="lint", id="acme")
    assert out.exit == O.FAIL
    assert any("[mcp]" in p for p in out.data["problems"])
    assert any("placeholder" in w for w in out.data["warnings"])


def test_lint_names_unknown_keys_and_contradictions(tmp_path):
    f = tmp_path / "x.toml"
    f.write_text(
        'id = "x"\nrank = 1\nfamily = "f"\ndelta = "x.md"\nbogus = 1\n'
        '[mcp]\npath = ""\nshape = "none"\nwhy = 2\n'
        "[plugin]\nnone = true\n"
        '[verified]\ndate = "2026-10-31"\nsources = ["u"]\nunverified = ["plugin"]\n'
    )
    out = H.harness_view(tmp_path, what="lint", id=str(f))
    msgs = " | ".join(out.data["problems"])
    assert "'bogus'" in msgs and "[mcp] unknown key 'why'" in msgs
    assert "[plugin] says none = true" in msgs


def test_lint_of_a_shipped_descriptor_is_clean(tmp_path):
    assert H.harness_view(tmp_path, what="lint", id="claude").ok


def test_lint_unknown_id_fails(tmp_path):
    assert H.harness_view(tmp_path, what="lint", id="nope").exit == O.FAIL


def test_test_is_unavailable_until_conformance_lands(tmp_path, monkeypatch):
    def gone(name):
        raise ImportError(name)

    monkeypatch.setattr(D.importlib, "import_module", gone)
    out = H.harness_view(tmp_path, what="test", id="claude")
    assert out.exit == O.NOTHING and out.data["status"] == "unavailable"


def test_probe_runs_nothing_without_consent(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("launched")

    monkeypatch.setattr(D.EP, "run", boom)
    out = H.harness_view(tmp_path, what="probe", id="claude")
    assert out.exit == O.NOTHING
    assert out.data["status"] == "unavailable"
    assert all(f["status"] == "unavailable" for f in out.data.get("facts", []))


def test_probe_with_consent_runs_version_only(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        D.EP,
        "run",
        lambda argvs, **k: (
            seen.extend(argvs)
            or [
                {"argv": a, "status": "verified", "exit_code": 0, "output": "x 10.0.0.5"}
                for a in argvs
            ]
        ),
    )
    out = H.harness_view(tmp_path, what="probe", id="grok", run=True)  # grok names a binary
    assert out.ok and seen
    assert all(a[1:] == ["--version"] for a in seen)
    assert "output" not in str(out.data["facts"])


def test_model_show_known_unknown_and_empty(tmp_path):
    out = M.model_show(tmp_path, "deepseek-flash")
    assert out.ok and out.data["facts"]
    assert all(f["status"] and f["source"] for f in out.data["facts"])
    unk = M.model_show(tmp_path, "zzz-no-such-model")
    assert unk.data["family"] == "unknown" and "never to a neighbour" in unk.data["message"]
    assert M.model_show(tmp_path, " ").exit == O.FAIL


def test_model_show_never_prints_a_route_endpoint(tmp_path):
    loc = tmp_path / ".ddflow/local"
    loc.mkdir(parents=True)
    (loc / "models.toml").write_text(
        '[route.mine]\nendpoint = "http://10.1.2.3:8000/v1"\n'
        '[model.mine-1]\nfamily = "deepseek"\nnote = "http://192.168.0.9/x"\n'
    )
    for name in ("mine-1", "mine"):
        out = M.model_show(tmp_path, name)
        assert "10.1.2.3" not in str(out.data) and "192.168" not in str(out.data)


def test_model_show_redacts_the_wire_body_too(tmp_path):
    loc = tmp_path / ".ddflow/local"
    loc.mkdir(parents=True)
    (loc / "models.toml").write_text(
        '[sources]\nsrv = { url = "http://10.9.8.7/models" }\n'
        '[model.m2]\nfamily = "deepseek"\n'
        'window = { v = 5, s = "documented", src = "srv", d = "2026-10-31" }\n'
    )
    out = M.model_show(tmp_path, "m2")
    assert out.data["facts"] and "10.9.8.7" not in str(out.data)


def test_add_escapes_free_text_and_lint_survives_scalar_tables(tmp_path):
    assert H.harness_view(tmp_path, what="add", id="q1", title='a"b\\c', family="x\ny").ok
    out = H.harness_view(tmp_path, what="lint", id="q1")
    assert not any("TOML" in p or "Invalid" in p for p in out.data["problems"])
    f = tmp_path / "s.toml"
    f.write_text('id = "s"\nverified = "yes"\n')
    assert H.harness_view(tmp_path, what="lint", id=str(f)).exit == O.FAIL
