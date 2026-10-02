"""The model-tier hint (B-model-tier-hint): a `tier:` tag, shown, advisory, validated."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli

from ddflow.core.tier import tier_of, unknown_tiers

ROOT = Path(__file__).resolve().parents[1]


def _add(repo, tid, tags=""):
    argv = ["task", "add", tid, "--title", f"title of {tid}", "--globs", f"{tid}.py"]
    if tags:
        argv += ["--tags", tags]
    code, out, err = run_cli(repo, *argv)
    assert code == 0, out + err


def test_tier_of_and_unknown():
    assert tier_of(["x", "tier:fast"]) == "fast"
    assert tier_of(["Tier:Deep"]) == "deep"
    assert tier_of(["tier:foo", "tier:balanced"]) == "balanced"
    assert tier_of(["tier:foo"]) == "" and tier_of(None) == ""
    assert unknown_tiers(["tier:foo", "tier:fast", "bug"]) == ["tier:foo"]


def test_next_and_brief_show_the_tier(repo):
    _add(repo, "T1", "tier:fast")
    _add(repo, "T2")
    code, out, _ = run_cli(repo, "next")
    assert code == 0
    t1 = next(line for line in out.splitlines() if "T1" in line)
    t2 = next(line for line in out.splitlines() if "T2" in line)
    assert "[tier:fast]" in t1 and "tier" not in t2
    code, out, _ = run_cli(repo, "--json", "next")
    rows = {r["id"]: r for r in json.loads(out)["ready"]}
    assert rows["T1"]["tier"] == "fast" and "tier" not in rows["T2"]
    code, out, _ = run_cli(repo, "brief", "--item", "T1")
    assert "tier `fast` (advisory" in out and "[tier:fast]" in out
    code, out, _ = run_cli(repo, "brief", "--item", "T2")
    assert "- tier `" not in out
    assert "[tier:" not in next(line for line in out.splitlines() if "**T2**" in line)


def test_mcp_next_body_keeps_the_tier(repo):
    from test_mcp_payload_bound import _call

    _add(repo, "T1", "tier:deep")
    r = _call(repo, "ddflow_next")
    body = json.loads(r["content"][0]["text"])
    assert body["ready"][0]["tier"] == "deep"


def test_doctor_notes_an_unknown_tier_not_an_error(repo):
    _add(repo, "T1", "tier:foo")
    code, out, err = run_cli(repo, "doctor")
    text = out + err
    assert "T1" in text and "unknown tier tag tier:foo" in text
    assert code == 0, text


def test_driver_and_readme_say_advisory():
    for p in (
        "docs/ddflow/drivers/implement-phase.md",
        "ddflow/templates/drivers/implement-phase.md",
    ):
        t = (ROOT / p).read_text()
        assert "Model-tier hint (advisory)" in t and "advice only" in t
    assert (ROOT / "docs/ddflow/drivers/implement-phase.md").read_text() == (
        ROOT / "ddflow/templates/drivers/implement-phase.md"
    ).read_text()
    assert "### Model-tier hint (advisory)" in (ROOT / "README.md").read_text()
