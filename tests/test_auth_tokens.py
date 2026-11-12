"""Paired bearer tokens (P-remote.auth): a one-time code at the host mints a named token with a
role; only its hash is kept, outside the project; expiring, rotatable, revocable; its name is the
caller's identity and one role check holds the operator-only verbs.

Every secret here is minted at run time, so nothing token-shaped is committed (r-secrets).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.adapters.env import SystemEnv
from ddflow.adapters.fakes.env import FakeEnv
from ddflow.config import Config
from ddflow.core.events import PROVENANCE_KINDS
from ddflow.core.handlers.tokens import tokens_of
from ddflow.core.model import fold
from ddflow.core.redact import mask_secrets
from ddflow.infra.log import EventLog
from ddflow.services import actors as AC
from ddflow.services import archifaces as IF
from ddflow.services import auth as AU
from ddflow.services import identity as ID


def _unproven() -> None:
    ID._CREDENTIAL.set(None)
    ID._PROOF_REQUIRED.set(False)


@pytest.fixture(autouse=True)
def _a_person(monkeypatch, tmp_path):
    for var in ("DDFLOW_AGENT", AU.TOKEN_ENV, *ID.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    _unproven()
    yield
    _unproven()


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "t.py")
    return repo


def _pair(repo, name="openclaw", role="bot", *extra):
    code, out, err = run_cli(repo, "--json", "pair", name, "--role", role, *extra)
    assert code == 0, out + err
    return json.loads(out)["code"]


def _redeem(repo, _monkeypatch, code):
    """The client's side: the code goes in on stdin, never argv."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    argv = [sys.executable, "-m", "ddflow", "--repo", str(repo), "--json", "tokens", "redeem"]
    p = subprocess.run(
        argv, input=code + "\n", capture_output=True, text=True, env=env, timeout=120
    )
    return p.returncode, p.stdout, p.stderr


def _token(repo, monkeypatch, name="openclaw", role="bot", *extra):
    got, out, err = _redeem(repo, monkeypatch, _pair(repo, name, role, *extra))
    assert got == 0, out + err
    return json.loads(out)["token"]


def _as(repo, monkeypatch, token, *argv):
    """One CLI call presenting ``token``, as a client's environment would."""
    monkeypatch.setenv(AU.TOKEN_ENV, token)
    return run_cli(repo, *argv)


def _events(repo):
    return EventLog(repo, "probe").read_all()


def _state(repo):
    return fold(_events(repo), strict=False)


# -- minting ---------------------------------------------------------------------------------


def test_the_code_mints_a_token_the_store_keeps_only_as_a_hash(proj, monkeypatch):
    token = _token(proj, monkeypatch)
    store = AU.store_path(proj)
    assert token not in store.read_text("utf-8")
    assert token.rsplit("_", 1)[1] not in store.read_text("utf-8")
    assert AU.verify(proj, Config(), token).name == "openclaw"


def test_the_store_is_private_and_outside_the_project(proj, monkeypatch):
    _token(proj, monkeypatch)
    store = AU.store_path(proj)
    assert proj.resolve() not in store.resolve().parents
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700


def test_a_user_dir_inside_the_project_is_refused(proj, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(proj / "cfg"))
    code, out, err = run_cli(proj, "pair", "x", "--role", "bot")
    assert code == 3 and "inside the project" in out + err
    assert not (proj / "cfg").exists()


def test_a_store_others_can_write_is_refused(proj, monkeypatch):
    token = _token(proj, monkeypatch)
    os.chmod(AU.store_path(proj), 0o666)
    with pytest.raises(AU.AuthRefused, match="permissions"):
        AU.verify(proj, Config(), token)


def test_a_corrupt_store_verifies_nothing(proj, monkeypatch):
    token = _token(proj, monkeypatch)
    AU.store_path(proj).write_text("{not json", "utf-8")
    with pytest.raises(AU.AuthRefused, match="unreadable"):
        AU.verify(proj, Config(), token)


def test_a_wrong_typed_store_field_is_refused_not_a_crash(proj, monkeypatch):
    """`_read` checks only `format`: a store whose tables are the wrong type used to pass it
    and crash later (on `.items()`); it refuses with a clear message instead."""
    _token(proj, monkeypatch)
    store = AU.store_path(proj)
    store.write_text(json.dumps({"format": 1, "tokens": [], "pairings": []}), "utf-8")
    store.chmod(0o600)
    with pytest.raises(AU.AuthRefused, match="not a table"):
        AU._read(store)
    with pytest.raises(AU.AuthRefused, match="not a table"):
        AU.verify(proj, Config(), "ddft_" + "a1b2c3d4e5f6" + "_" + "f" * 64)


def test_a_symlinked_store_is_refused(proj, monkeypatch, tmp_path):
    token = _token(proj, monkeypatch)
    store = AU.store_path(proj)
    planted = tmp_path / "planted.json"
    planted.write_text(store.read_text("utf-8"), "utf-8")
    planted.chmod(0o600)
    store.unlink()
    store.symlink_to(planted)
    with pytest.raises(AU.AuthRefused, match="permissions"):
        AU.verify(proj, Config(), token)


def test_every_worktree_of_a_project_shares_one_store(proj):
    subprocess.run(["git", "-C", str(proj), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(proj), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-qm", "base", "--allow-empty"], check=True, capture_output=True,
    )  # fmt: skip
    tree = proj.parent / "linked"
    subprocess.run(
        ["git", "-C", str(proj), "worktree", "add", "-q", str(tree)],
        check=True, capture_output=True,
    )  # fmt: skip
    assert AU.store_path(tree) == AU.store_path(proj)


def test_a_token_for_one_project_proves_nothing_in_another(proj, monkeypatch, tmp_path):
    token = _token(proj, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    run_cli(other, "init")
    assert _token(other, monkeypatch, "openclaw", "bot")
    assert AU.store_path(other) != AU.store_path(proj)
    with pytest.raises(AU.AuthRefused):
        AU.verify(other, Config(), token)


def test_two_clients_racing_for_one_code_get_one_token(proj):
    code = AU.start_pairing(_log(proj), Config(), name="n", role="bot").code
    won, lost = [], []

    def spend() -> None:
        try:
            won.append(AU.redeem(_log(proj), Config(), code))
        except AU.AuthRefused as exc:
            lost.append(exc)

    threads = [threading.Thread(target=spend) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(won) == 1 and len(lost) == 5


def test_the_store_path_is_no_knob():
    """A cloned project's config must not be able to point verification at its own file."""
    knobs = [k for k in vars(Config().agent) if "token" in k or "pair" in k]
    assert knobs and not [k for k in knobs if "dir" in k or "path" in k or "store" in k]


def test_nothing_secret_reaches_the_project_or_the_log(proj, monkeypatch):
    code = _pair(proj)
    _got, out, _e = _redeem(proj, monkeypatch, code)
    token = json.loads(out)["token"]
    secret = token.rsplit("_", 1)[1]
    stored = json.loads(AU.store_path(proj).read_text("utf-8"))
    hashes = [row["hash"] for row in stored["tokens"].values()]
    for path in proj.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            body = path.read_bytes()
            for needle in (token, secret, code, *hashes):
                assert needle.encode() not in body, path


def test_a_code_is_spent_once(proj, monkeypatch):
    code = _pair(proj)
    assert _redeem(proj, monkeypatch, code)[0] == 0
    assert _redeem(proj, monkeypatch, code)[0] == 3


def test_an_expired_code_is_refused(proj):
    code = AU.start_pairing(_log(proj), Config(), name="n", role="bot", now=1000.0).code
    with pytest.raises(AU.AuthRefused):
        AU.redeem(_log(proj), Config(), code, now=1000.0 + Config().agent.pair_ttl_s + 1)


def test_wrong_guesses_burn_the_pairing(proj, monkeypatch):
    cfg = Config()
    code = _pair(proj)
    pid, secret = code.split("-", 1)
    for _ in range(cfg.agent.pair_max_attempts):
        assert _redeem(proj, monkeypatch, f"{pid}-{'0' * len(secret)}")[0] == 3
    assert _redeem(proj, monkeypatch, code)[0] == 3


def test_an_unknown_role_a_bad_name_or_a_long_ttl_is_refused(proj):
    assert run_cli(proj, "pair", "x", "--role", "root")[0] == 1
    assert run_cli(proj, "pair", "no spaces", "--role", "bot")[0] == 1
    over = str(Config().agent.token_max_ttl_h + 1)
    assert run_cli(proj, "pair", "x", "--role", "bot", "--ttl-h", over)[0] == 1
    assert run_cli(proj, "pair", "x", "--role", "bot", "--ttl-h", "0")[0] == 1


def test_a_live_name_is_not_paired_twice(proj, monkeypatch):
    _pair(proj)
    code, out, err = run_cli(proj, "pair", "openclaw", "--role", "bot")
    assert code == 3 and "openclaw" in out + err


def test_pairing_is_a_persons_act(proj, monkeypatch):
    code, out, err = run_cli(proj, "--agent", "bot", "pair", "x", "--role", "operator")
    assert code == 3 and "person" in out + err
    monkeypatch.setenv("CLAUDECODE", "1")
    assert run_cli(proj, "pair", "x", "--role", "operator")[0] == 3
    assert not AU.store_path(proj).exists()


# -- verification ----------------------------------------------------------------------------


def test_a_tampered_token_is_refused(proj, monkeypatch):
    token = _token(proj, monkeypatch)
    forged = token[:-1] + ("0" if token[-1] != "0" else "1")
    for bad in (forged, "", "nonsense", token + "x"):
        with pytest.raises(AU.AuthRefused):
            AU.verify(proj, Config(), bad)


def test_a_token_expires(proj, monkeypatch):
    token = _token(proj, monkeypatch, "n", "bot", "--ttl-h", "1")
    import time

    assert AU.verify(proj, Config(), token, now=time.time() + 3000).name == "n"
    with pytest.raises(AU.AuthRefused):
        AU.verify(proj, Config(), token, now=time.time() + 3700)


def test_a_bad_token_fails_closed_on_the_cli(proj, monkeypatch):
    code, out, err = _as(proj, monkeypatch, "ddft_000000000000_" + "0" * 64, "status")
    assert code == 3 and "token" in (out + err).lower()


def test_a_presented_token_leaves_the_environment(proj, monkeypatch):
    """A lane or gate this process starts must not inherit the caller's credential."""
    monkeypatch.setenv(AU.TOKEN_ENV, _token(proj, monkeypatch))
    assert AU.bind_env(proj, Config(), ("status",)).name == "openclaw"
    assert AU.TOKEN_ENV not in os.environ


def test_taking_a_variable_returns_it_and_removes_it(monkeypatch):
    monkeypatch.setenv("DDFLOW_PROBE_TAKE", "value")
    assert SystemEnv().take("DDFLOW_PROBE_TAKE") == "value"
    assert "DDFLOW_PROBE_TAKE" not in os.environ
    assert SystemEnv().take("DDFLOW_PROBE_TAKE") == ""
    fake = FakeEnv({"X": "1"})
    assert fake.take("X") == "1" and fake.get("X") is None and fake.take("X") == ""


def test_token_support_adds_no_environment_read_to_the_census():
    """Measured on the real tree: the port stays at the port's sites (its `get` and `setdefault`
    reads), and nothing reads the token outside it."""
    counts = IF.counts(IF.scan(Path(__file__).resolve().parents[1]))
    assert counts[("env", "<dynamic>", "ddflow/adapters/env.py")] == 2  # get + setdefault
    assert counts[("env", "<all>", "ddflow/adapters/env.py")] == 1
    assert ("env", "<dynamic>", "ddflow/surfaces/context.py") not in counts
    assert not [k for k in counts if k[1] == AU.TOKEN_ENV or k[2] == "ddflow/services/auth.py"]


def test_the_mcp_server_does_not_take_a_token_from_its_environment(proj, monkeypatch):
    """A stdio server speaks for whichever agent connected: the operator's token in its
    environment would make every agent the operator."""
    token = _token(proj, monkeypatch, "me", "operator")
    monkeypatch.setenv(AU.TOKEN_ENV, token)
    assert AU.bind_env(proj, Config(), ("mcp",)) is None
    assert ID.credential() is None
    assert AU.TOKEN_ENV not in os.environ  # nor does what the server starts inherit it


def test_a_blank_token_is_a_bad_token_not_no_token(proj, monkeypatch):
    monkeypatch.setenv(AU.TOKEN_ENV, "   ")
    with pytest.raises(AU.AuthRefused):
        AU.bind_env(proj, Config(), ("status",))


def test_a_client_redeems_with_its_old_token_still_set(proj, monkeypatch):
    old = _token(proj, monkeypatch, "gw", "bot")
    _code, out, _err = run_cli(proj, "--json", "tokens", "rotate", "gw")
    monkeypatch.setenv(AU.TOKEN_ENV, old)
    assert _redeem(proj, monkeypatch, json.loads(out)["code"])[0] == 0


def test_a_token_lists_with_a_group_verb(proj, monkeypatch):
    token = _token(proj, monkeypatch, "eye", "observer")
    assert _as(proj, monkeypatch, token, "list", "task")[0] == 0


# -- identity --------------------------------------------------------------------------------


def test_the_token_name_is_the_identity_and_agent_cannot_override_it(proj, monkeypatch):
    token = _token(proj, monkeypatch, "openclaw", "bot")
    code, out, err = _as(proj, monkeypatch, token, "--agent", "spoof", "session", "note",
                         "--text", "hello from the gateway")  # fmt: skip
    assert code == 0, out + err
    notes = [e for e in _events(proj) if e.kind == "session.note"]
    assert notes and notes[-1].agent == "openclaw"


def test_a_context_built_inside_a_servers_request_keeps_its_proof(proj):
    from ddflow.surfaces.cli import build_parser
    from ddflow.surfaces.context import Ctx

    proof = ID.Credential(name="gw", role="bot", token_id="t")
    with ID.with_credential(proof):
        ctx = Ctx(build_parser().parse_args(["--repo", str(proj), "status"]))
        assert ID.credential() == proof and ctx.log.agent_id == "gw"


def test_resolve_prefers_the_proof_over_every_declaration(proj):
    proof = ID.Credential(name="gw", role="bot", token_id="t")
    with ID.with_credential(proof):
        assert ID.resolve(proj, Config(), "spoof") == ID.AgentId("gw", "token")
    assert ID.resolve(proj, Config(), "spoof").id == "spoof"


# -- one role check --------------------------------------------------------------------------


def test_a_lesser_role_is_not_a_person_even_at_a_bare_terminal():
    with ID.with_credential(ID.Credential(name="gw", role="bot", token_id="t")):
        assert "gw" in ID.agent_marker()
        assert ID.is_agent()
        actor = AC.resolve_actor(Config(), "whoever")
    assert (actor.id, actor.role, actor.source) == ("gw", "bot", "credential")
    assert not actor.can("approve")


def test_an_operator_token_is_a_person_on_any_surface():
    proof = ID.Credential(name="me", role="operator", token_id="t", operator=True)
    with ID.with_credential(proof):
        assert ID.agent_marker("spoof") == ""
        assert ID.is_agent("", via_mcp=True) == ""
        assert AC.resolve_actor(Config(), "x", via_mcp=True).can("approve")


def test_a_proof_outranks_a_claimed_credential_role():
    with ID.with_credential(ID.Credential(name="gw", role="bot", token_id="t")):
        actor = AC.resolve_actor(Config(), "gw", credential_role="operator")
    assert actor.role == "bot"


def test_a_bot_token_cannot_run_an_operator_verb_from_a_bare_terminal(proj, monkeypatch):
    token = _token(proj, monkeypatch, "gw", "bot")
    for argv in (
        ("grant", "add", "--item", "T1", "--gate", "critic", "--reason", "r"),
        ("approvals", "approve", "Q1"),
        ("tokens", "revoke", "gw", "--reason", "r"),
        ("pair", "x", "--role", "operator"),
    ):
        code, out, err = _as(proj, monkeypatch, token, *argv)
        assert code == 3, argv
        assert "does not" in out + err or "person" in out + err


def test_an_observer_token_reads_and_writes_nothing(proj, monkeypatch):
    token = _token(proj, monkeypatch, "eye", "observer")
    assert _as(proj, monkeypatch, token, "status")[0] == 0
    before = len(_events(proj))
    assert _as(proj, monkeypatch, token, "session", "note", "--text", "x")[0] == 3
    assert _as(proj, monkeypatch, token, "claim", "T1")[0] == 3
    assert len(_events(proj)) == before


def test_an_operator_token_runs_an_operator_verb(proj, monkeypatch):
    token = _token(proj, monkeypatch, "me", "operator")
    code, out, err = _as(proj, monkeypatch, token, "--agent", "x", "grant", "add", "--item", "T1",
                         "--gate", "critic", "--reason", "r")  # fmt: skip
    assert code == 0, out + err


def test_verbs_follow_the_capability_table_and_deny_by_default():
    cfg = Config()

    def actor(role):
        return AC.resolve_actor(cfg, "c", credential_role=role)

    assert AU.authorize(cfg, actor("operator"), "approvals approve") == ""
    assert AU.authorize(cfg, actor("observer"), "status") == ""
    assert AU.authorize(cfg, actor("agent"), "claim") == ""
    assert AU.authorize(cfg, actor("observer"), "claim")
    assert AU.authorize(cfg, actor("bot"), "approvals approve")
    assert AU.authorize(cfg, actor("bot"), "no such verb")
    assert AU.authorize(cfg, actor("nobody"), "status")  # an unknown role holds nothing
    cfg.agent.capability_verbs = {**cfg.agent.capability_verbs, "read": ["gate *"]}
    assert AU.authorize(cfg, actor("observer"), "gate status") == ""
    assert AU.authorize(cfg, actor("observer"), "status")


def test_the_default_table_gives_no_lesser_role_an_operator_verb():
    cfg = Config()
    verbs = ("approve", "approvals approve", "approvals deny", "approvals revoke", "grant add",
             "chore approve", "reviewers approve", "config", "pair", "tokens revoke",
             "tokens rotate", "tokens list", "cleanup", "hooks install")  # fmt: skip
    for role in ("bot", "agent", "observer"):
        actor = AC.resolve_actor(cfg, "c", credential_role=role)
        for verb in verbs:
            assert AU.authorize(cfg, actor, verb), (role, verb)


def test_required_mode_a_marker_free_shell_is_not_a_person(proj):
    ID.require_proof()
    assert "operator token" in ID.agent_marker()
    assert AC.resolve_actor(Config(), "me").source == "default"


def test_required_mode_bootstrap_never_opens_for_the_mcp_surface():
    ID.require_proof()
    assert ID.lacks_only_proof()
    with ID.via_mcp():
        assert not ID.lacks_only_proof()


def test_required_mode_is_off_by_default_and_changes_nothing(proj):
    assert Config().agent.require_operator_token is False
    assert ID.agent_marker() == ""


def _require(repo):
    with (repo / ".ddflow" / "config.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n[agent]\nrequire_operator_token = true\n")


def test_required_mode_refuses_an_operator_verb_without_a_token(proj):
    _require(proj)
    code, out, err = run_cli(proj, "grant", "add", "--item", "T1", "--gate", "critic",
                             "--reason", "r")  # fmt: skip
    assert code == 3 and "operator token" in out + err


def test_required_mode_bootstraps_the_first_operator_token_then_needs_it(proj, monkeypatch):
    _require(proj)
    token = _token(proj, monkeypatch, "me", "operator")
    assert run_cli(proj, "pair", "x", "--role", "bot")[0] == 3
    assert _as(proj, monkeypatch, token, "pair", "x", "--role", "bot")[0] == 0


def test_required_mode_bootstrap_is_for_the_operator_role_alone(proj):
    _require(proj)
    assert run_cli(proj, "pair", "x", "--role", "bot")[0] == 3
    assert not AU.store_path(proj).exists()


def test_a_rotation_that_cannot_be_recorded_still_ends_the_old_token(proj, monkeypatch):
    old = _token(proj, monkeypatch, "gw", "bot")
    _code, out, _err = run_cli(proj, "--json", "tokens", "rotate", "gw")
    log = _log(proj)

    def down(*_a, **_k):
        raise OSError("log unavailable")

    monkeypatch.setattr(log, "append", down)
    with pytest.raises(OSError):
        AU.redeem(log, Config(), json.loads(out)["code"])
    with pytest.raises(AU.AuthRefused):
        AU.verify(proj, Config(), old)


def test_tokens_list_names_a_store_and_log_disagreement(proj, monkeypatch):
    """`redeem` writes the store before the log: a crash between them leaves a live token the
    list (log) never sees, and a list entry the store has already ended. `tokens list` says so
    instead of staying silent."""
    old = _token(proj, monkeypatch, "gw", "bot")
    _code, out, _err = run_cli(proj, "--json", "tokens", "rotate", "gw")
    log = _log(proj)

    def down(*_a, **_k):
        raise OSError("log unavailable")

    monkeypatch.setattr(log, "append", down)
    with pytest.raises(OSError):
        AU.redeem(log, Config(), json.loads(out)["code"])
    code, out, err = run_cli(proj, "--json", "tokens", "list")
    assert code == 0, out + err
    assert "unrecorded" in out
    assert "will not verify" in out


def test_required_mode_bootstrap_still_refuses_an_agent(proj, monkeypatch):
    _require(proj)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert run_cli(proj, "pair", "x", "--role", "operator")[0] == 3


# -- list, revoke, rotate --------------------------------------------------------------------


def test_list_shows_no_secret_and_derives_the_state(proj, monkeypatch):
    _token(proj, monkeypatch, "gw", "bot")
    code, out, err = run_cli(proj, "--json", "tokens", "list")
    assert code == 0, out + err
    rows = json.loads(out)["tokens"]
    assert [(r["name"], r["role"], r["state"]) for r in rows] == [("gw", "bot", "active")]
    assert "hash" not in json.dumps(rows)


def test_revoke_kills_the_token_and_is_recorded(proj, monkeypatch):
    token = _token(proj, monkeypatch, "gw", "bot")
    code, out, err = run_cli(proj, "tokens", "revoke", "gw", "--reason", "left the team")
    assert code == 0, out + err
    with pytest.raises(AU.AuthRefused):
        AU.verify(proj, Config(), token)
    assert token.split("_")[1] not in AU.store_path(proj).read_text("utf-8")
    row = next(iter(tokens_of(_state(proj)).values()))
    assert row["revoked_at"] and row["reason"] == "left the team"
    assert run_cli(proj, "tokens", "revoke", "gw", "--reason", "again")[0] == 3
    assert run_cli(proj, "--json", "tokens", "list")[1].count("gw") == 0
    assert "revoked" in run_cli(proj, "--json", "tokens", "list", "--all")[1]


def test_revoke_needs_a_reason(proj, monkeypatch):
    _token(proj, monkeypatch, "gw", "bot")
    assert run_cli(proj, "tokens", "revoke", "gw", "--reason", " ")[0] == 1


def test_revoke_by_an_agent_is_refused(proj, monkeypatch):
    token = _token(proj, monkeypatch, "gw", "bot")
    assert run_cli(proj, "--agent", "x", "tokens", "revoke", "gw", "--reason", "r")[0] == 3
    assert AU.verify(proj, Config(), token).name == "gw"


def test_rotation_keeps_the_old_token_until_the_new_one_is_redeemed(proj, monkeypatch):
    old = _token(proj, monkeypatch, "gw", "bot")
    code, out, err = run_cli(proj, "--json", "tokens", "rotate", "gw")
    assert code == 0, out + err
    assert AU.verify(proj, Config(), old).name == "gw"
    got, body, _e = _redeem(proj, monkeypatch, json.loads(out)["code"])
    assert got == 0
    new = json.loads(body)["token"]
    assert AU.verify(proj, Config(), new).role == "bot"
    with pytest.raises(AU.AuthRefused):
        AU.verify(proj, Config(), old)
    rows = tokens_of(_state(proj))
    assert sorted(r["reason"] for r in rows.values()) == ["", "rotated"]
    assert [r["rotated_from"] for r in rows.values() if r["rotated_from"]] == [old.split("_")[1]]


def test_a_name_cannot_take_the_shape_of_a_token_id(proj, monkeypatch):
    """`tokens revoke` takes a name or an id, so the two must never collide."""
    first = _token(proj, monkeypatch, "alice", "bot")
    token_id = first.split("_")[1]
    assert run_cli(proj, "pair", token_id, "--role", "bot")[0] == 1
    assert run_cli(proj, "tokens", "revoke", token_id, "--reason", "r")[0] == 0
    with pytest.raises(AU.AuthRefused):
        AU.verify(proj, Config(), first)


def test_a_transport_hands_the_code_in_its_arguments(proj):
    """A server's stdin is not its client's: the declared call takes the code from `args`."""
    from ddflow.surfaces.declared import serve as declared

    code = AU.start_pairing(_log(proj), Config(), name="n", role="bot").code
    redeem = next(c for c in declared.COMMANDS if c.path == ("tokens", "redeem"))
    assert redeem.call(proj, {"code": code}, "").data["name"] == "n"


def test_rotating_an_unknown_name_is_refused(proj):
    assert run_cli(proj, "tokens", "rotate", "ghost")[0] == 3


# -- the record ------------------------------------------------------------------------------


def test_the_events_carry_who_and_when_and_no_secret(proj, monkeypatch):
    token = _token(proj, monkeypatch, "gw", "bot")
    run_cli(proj, "tokens", "revoke", "gw", "--reason", "r")
    issued = [e for e in _events(proj) if e.kind == "token.issued"]
    revoked = [e for e in _events(proj) if e.kind == "token.revoked"]
    assert len(issued) == len(revoked) == 1
    assert set(issued[0].data) == {"id", "name", "role", "expires_at", "by", "rotated_from"}
    assert set(revoked[0].data) == {"id", "name", "by", "reason"}
    assert token.rsplit("_", 1)[1] not in json.dumps([e.data for e in issued + revoked])


def test_the_token_events_survive_compaction():
    assert {"token.issued", "token.revoked"} <= PROVENANCE_KINDS


def test_a_pasted_token_is_redacted_from_any_text():
    token = "ddft_" + "ab12" * 3 + "_" + "cd34" * 16
    text, hits = mask_secrets(f"the gateway uses {token} now", Config().session.redact_patterns)
    assert hits == 1 and token[-16:] not in text


def test_the_token_shape_and_the_redact_default_stay_pinned():
    """The token shape lives in two files (`_TOKEN` and [session].redact_patterns' default); the
    sample is built in `_TOKEN`'s own shape, so changing one side and not the other fails."""
    sample = AU._TOKEN.fullmatch("ddft_" + "a1b2c3d4e5f6" + "_" + "f" * 64)
    assert sample is not None
    text, hits = mask_secrets(f"gateway {sample.group(0)} now", Config().session.redact_patterns)
    assert hits == 1 and sample.group(0)[-16:] not in text


def test_the_commands_have_no_mcp_tool():
    from ddflow.surfaces.declared import serve as declared

    assert declared.COMMANDS and all(not c.tool and c.reason for c in declared.COMMANDS)


def _log(repo):
    return EventLog(repo, "probe")
