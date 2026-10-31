"""SW-local-endpoint: a route as data rendered into a lane-private provider block."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from ddflow.infra import paths
from ddflow.services import modelreg
from ddflow.services.lane_driver import Result
from ddflow.services.lanes import endpoint as E

EXAMPLE = paths.package_dir() / "models" / "routes" / "local.example.toml"


def _cat(tmp_path: Path, text: str | None = None) -> modelreg.Catalog:
    (tmp_path / "models.toml").write_text(text if text is not None else EXAMPLE.read_text())
    return modelreg.load(local_dir=tmp_path)


def test_shipped_data_is_well_formed(tmp_path):
    assert E.data_problems() == []
    assert modelreg.validate(_cat(tmp_path)) == []


def test_resolve_example_route(tmp_path):
    ep = E.resolve(_cat(tmp_path), "lan-vllm")
    assert isinstance(ep, Result) and not ep.ok  # tool_call is only unverified: refused
    assert "tool calls are not verified" in ep.detail
    ep = E.resolve(_cat(tmp_path), "lan-vllm", require_tools=False)
    assert isinstance(ep, E.Endpoint)
    assert (ep.model, ep.window, ep.concurrency, ep.free) == ("SERVED-MODEL-ID", None, None, True)


def _verified(tmp_path):
    t = EXAMPLE.read_text().replace('s = "unverified"', 's = "verified"')
    return _cat(tmp_path, t)


def test_verified_route_renders_for_both_harnesses(tmp_path):
    ep = E.resolve(_verified(tmp_path), "lan-vllm")
    assert isinstance(ep, E.Endpoint) and ep.tool_call and ep.window == 32768
    assert ep.concurrency == 2 and ep.streaming is True
    for harness in ("kilo", "opencode"):
        home = tmp_path / harness
        p = E.write_private(ep, harness, home)
        assert isinstance(p, Path) and p.is_relative_to(home)
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        doc = json.loads(p.read_text())
        prov = doc["provider"][ep.provider]
        assert prov["options"]["baseURL"] == ep.base_url
        assert prov["options"]["apiKey"] == "{env:LAN_ENDPOINT_KEY}"
        m = prov["models"]["SERVED-MODEL-ID"]
        assert m["tool_call"] is True and m["limit"] == {"context": 32768, "output": 4096}
        assert doc["model"] == E.model_arg(ep)


def test_key_by_name_only_and_keyless(tmp_path, monkeypatch):
    t = EXAMPLE.read_text().replace(', api_key_env = "LAN_ENDPOINT_KEY"', "")
    ep = E.resolve(_cat(tmp_path, t.replace('s = "unverified"', 's = "verified"')), "lan-vllm")
    assert isinstance(ep, E.Endpoint) and E.launch_env(ep) == {}
    assert "apiKey" not in json.loads(E.render(ep, "kilo"))["provider"][ep.provider]["options"]
    ep2 = E.resolve(_verified(tmp_path), "lan-vllm")
    assert E.launch_env(ep2) == {"LAN_ENDPOINT_KEY": "LAN_ENDPOINT_KEY"}
    assert "key_env='LAN_ENDPOINT_KEY'" in repr(ep2)
    monkeypatch.setenv("LAN_ENDPOINT_KEY", "sk-sentinel-value")
    pr = E.prepare(_verified(tmp_path), "lan-vllm", "kilo", tmp_path / "hh")
    assert "sk-sentinel-value" not in repr(pr) + pr.config.read_text() + repr(pr.env)
    assert "ENDPOINT-HOST" not in repr(ep2)


@pytest.mark.parametrize(
    "old,new,msg",
    [
        ('api_key_env = "LAN_ENDPOINT_KEY"', 'api_key_env = "sk-abc def"', "NAME"),
        ("http://ENDPOINT-HOST", "http://user:pw@ENDPOINT-HOST", "credentials"),
        ('kind = "openai"', 'kind = "anthropic"', "OpenAI-compatible"),
        (', model = "SERVED-MODEL-ID"', "", "no served model"),
    ],
)
def test_bad_routes_are_unavailable(tmp_path, old, new, msg):
    r = E.resolve(_cat(tmp_path, EXAMPLE.read_text().replace(old, new)), "lan-vllm")
    assert isinstance(r, Result) and msg in r.detail


def test_unknown_route_and_harness(tmp_path):
    cat = _verified(tmp_path)
    assert "no route" in E.resolve(cat, "nope").detail
    ep = E.resolve(cat, "lan-vllm")
    assert isinstance(E.render(ep, "claude"), Result)
    assert E.layout("kilo")["custom_provider"]["s"] == "verified"
    assert E.layout("opencode")["custom_provider"]["s"] == "verified"


def test_probe_is_unavailable_not_passed(tmp_path):
    ep = E.resolve(_verified(tmp_path), "lan-vllm")
    r = E.probe_plan(ep, "kilo")
    assert r.status == "unavailable" and "operator" in r.detail


def test_template_ships_and_example_has_no_real_address():
    text = EXAMPLE.read_text()
    assert "ENDPOINT-HOST" in text and "192.168" not in text and "sk-" not in text


def test_prepare_writes_private_block_and_reports_probe(tmp_path):
    pr = E.prepare(_verified(tmp_path), "lan-vllm", "kilo", tmp_path / "home")
    assert isinstance(pr, E.Prepared) and pr.config.is_file()
    assert pr.env == {"LAN_ENDPOINT_KEY": "LAN_ENDPOINT_KEY"} and pr.model.endswith(
        "/SERVED-MODEL-ID"
    )
    assert pr.probe.status == "unavailable"
    assert E.prepare(_cat(tmp_path), "lan-vllm", "kilo", tmp_path / "h2").status == "unavailable"
    assert not (tmp_path / "h2").exists()  # a refused route writes nothing


def test_limit_needs_both_numbers(tmp_path):
    t = EXAMPLE.read_text().replace('s = "unverified"', 's = "verified"')
    t = "\n".join(ln for ln in t.splitlines() if not ln.startswith("max_output"))
    ep = E.resolve(_cat(tmp_path, t), "lan-vllm")
    assert ep.window == 32768 and ep.max_output is None
    m = json.loads(E.render(ep, "kilo"))["provider"][ep.provider]["models"]["SERVED-MODEL-ID"]
    assert "limit" not in m  # kilo skips a model with context but no output


def test_malformed_route_row_is_unavailable_and_old_file_mode_is_tightened(tmp_path):
    bad = '[route.x]\nclass = "local"\nendpoint = "http://h/v1"\n'
    r = E.resolve(_cat(tmp_path, bad), "x")
    assert isinstance(r, Result) and "not an OpenAI" in r.detail
    ep = E.resolve(_verified(tmp_path), "lan-vllm")
    old = tmp_path / "h" / ".config" / "kilo" / "kilo.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    old.chmod(0o644)
    E.write_private(ep, "kilo", tmp_path / "h")
    assert stat.S_IMODE(old.stat().st_mode) == 0o600


def _url_cat(tmp_path, url):
    t = EXAMPLE.read_text().replace("http://ENDPOINT-HOST:8000/v1", url)
    return _cat(tmp_path, t)


@pytest.mark.parametrize("url", ["https://h/v1?api_key=sk-1", "https://h/v1#x"])
def test_url_with_query_or_fragment_is_refused(tmp_path, url):
    r = E.resolve(_url_cat(tmp_path, url), "lan-vllm", require_tools=False)
    assert isinstance(r, Result) and "query" in r.detail


@pytest.mark.parametrize("url", ["https://h", "http://h:8000/v1/", "https://h/compatible-mode/v1"])
def test_plain_and_multi_segment_urls_are_accepted(tmp_path, url):
    r = E.resolve(_url_cat(tmp_path, url), "lan-vllm", require_tools=False)
    assert isinstance(r, E.Endpoint) and r.base_url == url


def test_env_name_with_newline_is_refused(tmp_path):
    t = EXAMPLE.read_text().replace('"LAN_ENDPOINT_KEY"', '"LAN_ENDPOINT_KEY\\n"')
    r = E.resolve(_cat(tmp_path, t), "lan-vllm", require_tools=False)
    assert isinstance(r, Result) and "NAME" in r.detail


def test_unverified_harness_is_refused_unless_allowed(tmp_path, monkeypatch):
    real = E.layout

    def unread(h):
        lay = dict(real(h))
        lay["custom_provider"] = {"v": True, "s": "unverified"}
        return lay

    monkeypatch.setattr(E, "layout", unread)
    cat = _verified(tmp_path)
    r = E.prepare(cat, "lan-vllm", "opencode", tmp_path / "o")
    assert isinstance(r, Result) and "unverified" in r.detail and not (tmp_path / "o").exists()
    assert isinstance(
        E.prepare(cat, "lan-vllm", "opencode", tmp_path / "o", allow_unverified_harness=True),
        E.Prepared,
    )


def test_non_string_endpoint_values_are_refused(tmp_path):
    t = EXAMPLE.read_text().replace('api_key_env = "LAN_ENDPOINT_KEY"', "api_key_env = true")
    r = E.resolve(_cat(tmp_path, t), "lan-vllm", require_tools=False)
    assert isinstance(r, Result) and "strings" in r.detail


def test_harness_recorded_as_not_accepting_is_refused(tmp_path, monkeypatch):
    real = E.layout("kilo")
    bad = {**real, "custom_provider": {**real["custom_provider"], "v": False}}
    monkeypatch.setattr(E, "layout", lambda h: bad)
    r = E.prepare(_verified(tmp_path), "lan-vllm", "kilo", tmp_path / "x")
    assert isinstance(r, Result) and "NOT accepting" in r.detail
