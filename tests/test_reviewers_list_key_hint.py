"""`reviewers list` names how each reviewer gets its key and who has none; never a value."""

from __future__ import annotations

from ddflow.api import review as API

FAKE = "sk-fake-LISTHINT-5d2e"
BLOCK = '[[reviewer]]\nname = "{n}"\nkind = "openai"\nmodel = "m"\nbase_url = "http://h/v1"\napi_key_env = "DDFLOW_T_LIST_KEY"\n{extra}'


def test_list_shows_key_source_and_no_key_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("DDFLOW_T_LIST_KEY", raising=False)
    kf = tmp_path / "k.key"
    kf.write_text(FAKE + "\n")
    kf.chmod(0o600)
    d = tmp_path / ".ddflow" / "local"
    d.mkdir(parents=True)
    (d / "reviewers.toml").write_text(
        BLOCK.format(n="withfile", extra=f'api_key_file = "{kf}"\n')
        + BLOCK.format(n="nokey", extra="")
    )
    out = API.reviewers_list(tmp_path)
    assert out.data["no_key"] == ["nokey"]
    rows = {r["name"]: r["key"] for r in out.data["reviewers"]}
    assert rows["withfile"] == f"file {kf}" and rows["nokey"].startswith("missing")
    assert FAKE not in str(out.data)
