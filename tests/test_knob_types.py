"""A numeric knob is checked for its type and range when the config loads (bug Bc2f54fd0db).

`ttl_s = true`, `max_rounds = 2.7`, a negative number, `inf` and `nan` used to load
unchecked and misbehave later (a bool is an int in Python, so `true` was a 1-second lease).
The refusal names the key, the value and what is allowed.
"""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.config._errors import InvalidValue


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    return tmp_path


def _load(repo, text: str) -> Config:
    (repo / ".ddflow" / "config.toml").write_text(text, encoding="utf-8")
    return Config.load(repo, env={})


@pytest.mark.parametrize(
    ("toml", "key"),
    [
        ("[lease]\nttl_s = true\n", "lease.ttl_s"),
        ("[lease]\nttl_s = false\n", "lease.ttl_s"),
        ("[review]\nmax_rounds = 2.7\n", "review.max_rounds"),
        ("[lease]\nttl_s = -5\n", "lease.ttl_s"),
        ("[lease]\nttl_s = inf\n", "lease.ttl_s"),
        ("[lease]\nttl_s = -inf\n", "lease.ttl_s"),
        ("[lease]\nttl_s = nan\n", "lease.ttl_s"),
        ("[lease]\nheartbeat_s = 0\n", "lease.heartbeat_s"),
        ("[lease]\nttl_s = 1e400\n", "lease.ttl_s"),
    ],
)
def test_a_bad_numeric_value_is_refused_naming_the_key_and_the_allowed_range(repo, toml, key):
    with pytest.raises(InvalidValue) as err:
        _load(repo, toml)
    text = str(err.value)
    assert key in text
    assert "allowed" in text


def test_the_refusal_shows_the_value_the_file_held_and_the_layer_it_came_from(repo):
    with pytest.raises(InvalidValue, match=r"ttl_s = -5") as err:
        _load(repo, "[lease]\nttl_s = -5\n")
    assert "file layer" in str(err.value)


def test_a_machine_local_value_names_the_local_layer(repo):
    (repo / ".ddflow" / "local").mkdir()
    (repo / ".ddflow" / "local" / "config.toml").write_text("[lease]\nttl_s = -5\n")
    with pytest.raises(InvalidValue, match="local layer"):
        Config.load(repo, env={})


@pytest.mark.parametrize(
    ("env", "key"),
    [
        ({"DDFLOW_LEASE_TTL_S": "-5"}, "lease.ttl_s = '-5'"),
        ({"DDFLOW_LEASE_TTL_S": "inf"}, "lease.ttl_s = 'inf'"),
        ({"DDFLOW_REVIEW_MAX_ROUNDS": "2.7"}, "review.max_rounds = '2.7'"),
        ({"DDFLOW_LEASE_HEARTBEAT_S": "0"}, "lease.heartbeat_s = '0'"),
    ],
)
def test_the_environment_is_held_to_the_same_rule(repo, env, key):
    with pytest.raises(InvalidValue) as err:
        Config.load(repo, env=env)
    assert key in str(err.value)  # the key and the value as given
    assert "allowed:" in str(err.value)  # and what is allowed, even for text that is no number
    assert "env layer" in str(err.value)


def test_sensible_values_still_load(repo):
    cfg = _load(repo, "[lease]\nttl_s = 3600\nheartbeat_s = 300\n[review]\nmax_rounds = 4\n")
    assert (cfg.lease.ttl_s, cfg.lease.heartbeat_s, cfg.review.max_rounds) == (3600, 300, 4)


def test_zero_still_means_off_for_a_knob_where_it_does_and_an_integral_float_loads(repo):
    cfg = _load(repo, "[lanes]\nstale_after_s = 0\n")
    assert cfg.lanes.stale_after_s == 0


def test_a_whole_number_float_is_accepted_for_an_integer_knob(repo):
    assert _load(repo, "[lease]\nttl_s = 7200.0\n").lease.ttl_s == 7200


def test_a_float_knob_still_takes_an_integer_literal(repo):
    cfg = _load(repo, "[dedupe]\nshow_floor = 1\n")
    assert cfg.dedupe.show_floor == 1


@pytest.mark.parametrize("text", ["true", "inf", "nan", "-0.5"])
def test_a_float_knob_refuses_a_bool_a_non_finite_and_a_negative_value(repo, text):
    with pytest.raises(InvalidValue, match=r"dedupe\.show_floor") as err:
        _load(repo, f"[dedupe]\nshow_floor = {text}\n")
    assert "allowed:" in str(err.value) and {"true": "True"}.get(text, text) in str(err.value)


def test_the_packaged_defaults_pass_their_own_check():
    # every numeric default is inside its own range, so a fresh install loads
    Config.load(None, env={})


def test_zero_rounds_still_means_unlimited_for_the_review_budget(repo):
    assert _load(repo, "[review]\nmax_rounds = 0\n").review.max_rounds == 0


def test_the_helper_reads_a_typing_spelling_of_an_optional_number():
    from ddflow.config._knob import checked_number

    with pytest.raises(ValueError, match="allowed"):
        checked_number("lease.ttl_s", -5, -5, "typing.Optional[int]")
    assert checked_number("lease.ttl_s", None, None, "typing.Optional[int]") is None


def test_a_knob_whose_doc_gives_zero_or_less_a_meaning_takes_a_negative_value(repo):
    cfg = _load(repo, "[agent]\nask_ttl_s = -1\n[upgrade]\nstart_timeout_s = -1.5\n")
    assert cfg.agent.ask_ttl_s == -1 and cfg.upgrade.start_timeout_s == -1.5


def test_those_knobs_are_still_held_to_a_finite_number(repo):
    with pytest.raises(InvalidValue, match=r"agent\.ask_ttl_s"):
        _load(repo, "[agent]\nask_ttl_s = inf\n")
    with pytest.raises(InvalidValue, match=r"upgrade\.start_timeout_s"):
        _load(repo, "[upgrade]\nstart_timeout_s = true\n")


def test_a_list_or_table_for_a_numeric_knob_is_refused(repo):
    with pytest.raises(InvalidValue, match=r"lease\.ttl_s"):
        _load(repo, "[lease]\nttl_s = [1]\n")


def test_a_negative_cooldown_still_means_probe_every_review(repo):
    assert _load(repo, "[review]\ndegraded_cooldown_s = -1\n").review.degraded_cooldown_s == -1
