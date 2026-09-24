"""Any LLM as a reviewer: local, remote, SaaS, or an arbitrary command.

The single property every one of these tests protects: **a reviewer that did not review
is UNAVAILABLE, never a pass.** There are a lot of ways not to review — no key, no
binary, server down, non-zero exit, empty stdout, truncated reply — and each has to be
reported with the specific remedy, because "review failed" sends an operator hunting.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from orchard.reviewer import (
    PRESETS,
    REVIEWED,
    UNAVAILABLE,
    Reviewer,
    family_of,
    parse,
    review,
)

DIFF = (
    "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n"
    "@@ -1 +1,2 @@\n+def divide(a, b):\n+    return a / b\n"
)


@pytest.fixture
def fake_cli(tmp_path):
    """A stand-in for any vendor CLI: prompt on stdin, review on stdout."""
    p = tmp_path / "fake-llm"
    p.write_text(
        "#!/bin/sh\n"
        "prompt=$(cat)\n"
        'if echo "$prompt" | grep -q divide; then\n'
        "  printf 'FINDING HIGH calc.py:2\\nDivision by zero when b is 0.\\n\\n"
        "STATUS: FINDINGS 1\\n'\n"
        "else printf 'STATUS: NO FINDINGS\\n'; fi\n"
    )
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def test_an_arbitrary_cli_can_be_a_reviewer(fake_cli):
    """The universal escape hatch: a model with no HTTP API at all is still usable."""
    res = review(
        Reviewer(
            name="cli", kind="command", command=str(fake_cli), model="whatever", family="acme"
        ),
        DIFF,
        intent="add division",
    )
    assert res.status == REVIEWED
    assert len(res.findings) == 1
    assert res.findings[0].severity == "HIGH"
    assert "Division by zero" in res.findings[0].detail


@pytest.mark.parametrize(
    "command,expected_fragment",
    [
        ("definitely-not-installed-xyzzy", "not on PATH"),
        ("sh -c 'echo boom >&2; exit 3'", "exited 3"),
        ("true", "no output on stdout"),
    ],
)
def test_a_command_that_did_not_review_is_unavailable(command, expected_fragment):
    res = review(Reviewer(name="x", kind="command", command=command, model="m"), DIFF, intent="x")
    assert res.status == UNAVAILABLE, "a non-review was reported as a review"
    assert expected_fragment in res.reason, res.reason


def test_a_silent_command_is_not_a_clean_review():
    """`true` exits 0 and prints nothing. Counting that as 'no findings' is the whole
    vacuous-pass failure, arriving through the most innocent-looking path there is."""
    res = review(Reviewer(name="s", kind="command", command="true", model="m"), DIFF, intent="x")
    assert res.status == UNAVAILABLE
    assert res.findings == []


@pytest.mark.parametrize("preset", ["anthropic", "gemini"])
def test_a_saas_backend_without_a_key_names_the_variable(preset, monkeypatch):
    spec = dict(PRESETS[preset])
    spec.pop("launch", None)
    monkeypatch.delenv(spec["api_key_env"], raising=False)
    res = review(Reviewer(name=preset, **spec), DIFF, intent="x")
    assert res.status == UNAVAILABLE
    assert spec["api_key_env"] in res.reason, res.reason


def test_an_unreachable_server_with_no_launch_says_what_to_do():
    res = review(
        Reviewer(name="d", base_url="http://127.0.0.1:59999/v1", model="m", launch={"command": ""}),
        DIFF,
        intent="x",
    )
    assert res.status == UNAVAILABLE
    assert "launch" in res.reason and "not answering" in res.reason


def test_an_unknown_kind_is_reported_not_guessed():
    res = review(
        Reviewer(name="w", kind="telepathy", model="m", base_url="http://x/v1"), DIFF, intent="x"
    )
    assert res.status == UNAVAILABLE
    assert "unknown reviewer kind" in res.reason


def test_a_failed_launch_does_not_leave_a_process_behind():
    """A reviewer stuck 'starting' forever is indistinguishable from one that is down,
    except that it also holds a process."""
    res = review(
        Reviewer(
            name="l",
            base_url="http://127.0.0.1:59998/v1",
            model="m",
            launch={"command": "sh -c 'exit 1'", "ready_timeout_s": 3},
        ),
        DIFF,
        intent="x",
    )
    assert res.status == UNAVAILABLE
    assert "exited immediately" in res.reason or "did not become ready" in res.reason


def test_an_unknown_launch_field_is_an_error_not_a_silent_drop():
    with pytest.raises(ValueError, match="unknown launch field"):
        Reviewer(name="x", launch={"comand": "typo"}).launch_spec()


# -- parsing --------------------------------------------------------------------------


def test_multiple_findings_are_parsed_separately():
    """The location group used to be `.*` under re.S, so it crossed newlines and ate the
    entire reply: a two-finding review parsed as one finding with an empty detail."""
    findings, on_contract = parse(
        "FINDING HIGH calc.py:2\nDivision by zero.\nSecond line.\n\n"
        "FINDING LOW util.py:9\nUnused import.\n\nSTATUS: FINDINGS 2\n"
    )
    assert on_contract
    assert len(findings) == 2
    assert findings[0].location == "calc.py:2"
    assert findings[0].detail == "Division by zero.\nSecond line."
    assert findings[1].location == "util.py:9"
    assert "STATUS" not in findings[1].detail


def test_no_findings_is_on_contract_and_empty():
    findings, on_contract = parse("STATUS: NO FINDINGS")
    assert on_contract and findings == []


def test_a_reply_with_no_status_block_is_off_contract():
    """Length is not a verdict. A reasoning model can emit thousands of tokens of
    deliberation with no conclusion in it."""
    _findings, on_contract = parse("I considered many things at great length. " * 50)
    assert not on_contract


# -- presets and family inference -----------------------------------------------------


def test_every_preset_is_structurally_valid():
    for name, spec in PRESETS.items():
        rev = Reviewer(name=name, **spec)
        assert rev.kind in ("openai", "anthropic", "gemini", "command"), name
        if rev.kind == "command":
            assert rev.command, f"{name}: kind=command with no command"
        else:
            assert rev.base_url, f"{name}: no base_url"
        rev.launch_spec()  # must not raise
        assert rev.resolved_family(), name


def test_no_preset_contains_a_literal_api_key():
    """The config file is committed. Only the NAME of an env var may appear."""
    for name, spec in PRESETS.items():
        for key, val in spec.items():
            assert key != "api_key", f"{name} carries a literal key field"
            if isinstance(val, str):
                assert not val.startswith(("sk-", "ghp_")), f"{name}: {key}"


@pytest.mark.parametrize(
    "model,family",
    [
        ("Qwen/Qwen3.8-Flash-Next-FP8", "alibaba"),
        ("claude-sonnet-5", "anthropic"),
        ("gpt-5", "openai"),
        ("gemini-2.5-pro", "google"),
        ("deepseek-chat", "deepseek"),
    ],
)
def test_family_is_inferred_from_the_model_id(model, family):
    assert family_of(model) == family


def test_two_unknown_models_are_not_the_same_family():
    """Collapsing every unrecognised model into one 'unknown' family would let a pair of
    unknown reviewers satisfy the independence check while possibly sharing a base."""
    assert family_of("acme-1") != family_of("globex-2")


def test_reviewers_add_writes_a_block_without_the_key(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "reviewers", "add", "--preset", "openai", "--model", "gpt-5")
    assert code == 0
    cfg = (repo / ".orchard" / "config.toml").read_text()
    assert 'api_key_env = "OPENAI_API_KEY"' in cfg
    assert "sk-" not in cfg
    assert "OPENAI_API_KEY" in out, "the operator must be told to set the variable"
