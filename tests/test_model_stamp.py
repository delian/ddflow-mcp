"""B-drift stamp: authorship provenance fields on agent-authored records (bug Bb54029aee9).

Reviewed and gated records written by agents (`review.triaged` probe text, `gate.passed`
evidence notes, and the other agent-authored kinds) used to carry only the agent id, so a
leaked script word could not be attributed to a served model or a context fill. The
`ddflow.services.model_stamp` service supplies the three fields the design asks for
(docs/ddflow/research/hallucination-drift.md, HAL-model-facts):

* `served_model` -- the served model id the harness reports under a model env name;
* `harness`      -- the claiming harness id, detected from its session variables;
* `ctx_tokens`   -- the current context fill in tokens the harness reports.

Every test here feeds hand-built environments; nothing guesses, so what is absent reads
as the literal `"unknown"` (bug Bb54029aee9: absent = unknown, never guessed).
"""

from __future__ import annotations

import pytest

from ddflow.services import model_stamp as MS


def reported(pairs: dict[str, str]) -> dict[str, str]:
    """Environment with every reporting variable the test does not set removed, so a
    variable leaking from the runner's own environment cannot satisfy an assertion."""
    from ddflow.services.harnessreg import session_env_vars

    env: dict[str, str] = {}
    for var in (*session_env_vars(), MS.DDFLOW_SERVED_MODEL, MS.DDFLOW_CTX_TOKENS, *MS.MODEL_ENV):
        env.pop(var, None)
    env.update(pairs)
    return env


def test_fields_all_unknown_with_no_reporting_env() -> None:
    """No reporting variable is set: every field reads the literal 'unknown'."""
    stamp = MS.stamp({}, reported({}))
    assert stamp["served_model"] == MS.UNKNOWN
    assert stamp["harness"] == MS.UNKNOWN
    assert stamp["ctx_tokens"] == MS.UNKNOWN


def test_served_model_reported_under_documented_env() -> None:
    """COPILOT_MODEL (the verified copilot env, agent-token-features-pass2.md) is read."""
    env = reported({"COPILOT_MODEL": "gpt-5.4"})
    assert MS.served_model(env) == "gpt-5.4"


def test_ddflow_owned_channel_wins_over_harness_env() -> None:
    """DDFLOW_SERVED_MODEL (ddflow's own reporting channel) beats any harness env."""
    env = reported({"DDFLOW_SERVED_MODEL": "deepseek-v4", "COPILOT_MODEL": "gpt-5.4"})
    assert MS.served_model(env) == "deepseek-v4"


def test_unlisted_model_env_is_not_guessed() -> None:
    """A variable that is not a declared reporting channel is never read."""
    env = reported({"SOME_HARNESS_MODEL": "something"})
    assert MS.served_model(env) == MS.UNKNOWN


def test_ctx_tokens_read_and_validated() -> None:
    """DDFLOW_CTX_TOKENS is read; a value that is not a token count is 'unknown'."""
    assert MS.ctx_tokens(reported({"DDFLOW_CTX_TOKENS": "120000"})) == "120000"
    assert MS.ctx_tokens(reported({"DDFLOW_CTX_TOKENS": "high"})) == MS.UNKNOWN
    assert MS.ctx_tokens(reported({})) == MS.UNKNOWN


def test_harness_detected_from_session_variables() -> None:
    """The claiming harness id comes from its session variables; none means 'unknown'."""
    assert MS.harness(reported({"CLAUDE_CODE_SESSION_ID": "sess-1"})) == "claude"
    assert MS.harness(reported({})) == MS.UNKNOWN


def test_stamp_merges_fields_and_keeps_existing() -> None:
    """stamp() adds the three fields to event data; a caller's own value is kept."""
    env = reported({"COPILOT_MODEL": "gpt-5.4", "DDFLOW_CTX_TOKENS": "80000"})
    out = MS.stamp({"note": "note"}, env)
    assert out == {
        "note": "note",
        "served_model": "gpt-5.4",
        "harness": MS.UNKNOWN,
        "ctx_tokens": "80000",
    }
    kept = MS.stamp({"served_model": "given"}, env)
    assert kept["served_model"] == "given"
