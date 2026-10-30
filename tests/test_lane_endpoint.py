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


def test_key_by_name_only_and_keyless(tmp_path):
    t = EXAMPLE.read_text().replace(', api_key_env = "LAN_ENDPOINT_KEY"', "")
    ep = E.resolve(_cat(tmp_path, t.replace('s = "unverified"', 's = "verified"')), "lan-vllm")
    assert isinstance(ep, E.Endpoint) and E.launch_env(ep) == {}
    assert "apiKey" not in json.loads(E.render(ep, "kilo"))["provider"][ep.provider]["options"]
    ep2 = E.resolve(_verified(tmp_path), "lan-vllm")
    assert E.launch_env(ep2) == {"LAN_ENDPOINT_KEY": "LAN_ENDPOINT_KEY"}
    assert "LAN_ENDPOINT_KEY" not in repr(ep2) or "key_env" in repr(ep2)
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
    assert E.layout("opencode")["custom_provider"]["s"] == "unverified"


def test_probe_is_unavailable_not_passed(tmp_path):
    ep = E.resolve(_verified(tmp_path), "lan-vllm")
    r = E.probe_plan(ep, "kilo")
    assert r.status == "unavailable" and "operator" in r.detail


def test_template_ships_and_example_has_no_real_address():
    text = EXAMPLE.read_text()
    assert "ENDPOINT-HOST" in text and "192.168" not in text and "sk-" not in text
