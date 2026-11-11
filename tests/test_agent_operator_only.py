"""`[agent].roles` and `[agent].capability_verbs` are operator tables (B-token-role-widened).

The two tables that decide what a paired token may do are read from every config layer,
so a token holder who can land a commit to `.ddflow/config.toml` (say, a role that holds
`merge`) can give its own role `*` there and widen every authority it holds. A cloned
project's committed config must not be able to widen a role (r-tighten-only): which layers
may set these two tables is the operator's decision, and the committed layer is not one of
them.

The fix makes them operator-only knobs: the committed `file` layer is refused on load and
a write target, while the local layer, the environment and `ddflow_configure` keep them.
"""

from __future__ import annotations

import tomllib

import pytest

from ddflow.config import Config
from ddflow.config._errors import InvalidValue


def _committed_cfg(repo, text: str) -> None:
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(text, encoding="utf-8")


def test_a_committed_config_cannot_widen_a_role(repo):
    """A committed layer WIDENING `[agent].roles` is refused, so `bot` stays default.

    Against the unfixed code the committed table IS read, so the bot role (packaged default
    `read, claim, note, start_lane, approve_marked`) becomes `*` -- this test fails there.
    """
    _committed_cfg(repo, '[agent]\nroles = { bot = ["*"] }\n')
    with pytest.raises(InvalidValue, match="agent.roles"):
        Config.load(repo)


def test_a_committed_config_may_tighten_a_role(repo):
    """The committed layer may set neither table at all (fail-closed), even to tighten: a
    tightening belongs in the local layer. Refused, never silently applied."""
    _committed_cfg(repo, '[agent]\nroles = { bot = ["read"] }\n')
    with pytest.raises(InvalidValue, match="agent.roles"):
        Config.load(repo)


def test_a_committed_config_cannot_widen_a_capability_verb_table(repo):
    """`[agent].capability_verbs` is the same operator table; widening it is refused too."""
    _committed_cfg(repo, '[agent]\ncapability_verbs = { read = ["*"] }\n')
    with pytest.raises(InvalidValue, match="agent.capability_verbs"):
        Config.load(repo)


def test_the_local_layer_may_set_the_roles_table(repo):
    """The operator's machine already owns the git-ignored local layer: it may set the table."""
    (repo / ".ddflow" / "local").mkdir(parents=True, exist_ok=True)
    (repo / ".ddflow" / "local" / "config.toml").write_text(
        '[agent]\nroles = { bot = ["read"] }\n', encoding="utf-8"
    )
    cfg = Config.load(repo)
    assert cfg.agent.roles["bot"] == ["read"]
    assert cfg.sources["agent.roles"] == "local"


def test_config_set_writes_the_roles_table_to_the_local_layer(repo):
    """The write of an operator table defaults to local, never the committed file."""
    # Simulate the reclassification these knobs get: the write default for agent.roles is
    # the local layer, like `schedule.parallel`.
    from ddflow.services.configwrite.layers import machine_local_layer

    assert machine_local_layer(set(), "agent.roles") == "local"
    assert machine_local_layer(set(), "agent.capability_verbs") == "local"
