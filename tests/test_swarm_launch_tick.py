"""The lane launcher has a production caller: the swarm scheduler tick (B5c8139e564).

``ddflow/services/lanes/launch.py`` ships a full, tested ``launch()`` that nothing outside
tests called, so a swarm lane could not be started even after its declared needs landed. The
scheduler tick is that caller: it rides on every command through ``api._base._load``, is
gated on ``[swarm] launch_enabled`` (off by default), and starts a lane only through the same
``launch`` a test calls directly.
"""

from __future__ import annotations

from pathlib import Path

from ddflow.config import Config
from ddflow.services.ticks import TickCtx, registered


def _tick(name: str = "swarm.launch"):
    found = [t for t in registered() if t.name == name]
    assert found, f"tick {name!r} is not registered"
    return found[0]


def _ctx(launch_enabled: bool = False) -> TickCtx:
    cfg = Config()
    cfg.swarm.launch_enabled = launch_enabled
    return TickCtx(Path("."), cfg, None)


def test_the_swarm_launch_tick_is_registered_and_gated_on_launch_enabled():
    tick = _tick()
    assert tick.enabled is not None
    assert not tick.enabled(_ctx(False))  # off by default: a person still starts a lane
    assert tick.enabled(_ctx(True))


def test_the_tick_target_is_the_production_caller_of_launch():
    tick = _tick()
    target = tick.resolve()
    assert target.__module__.startswith("ddflow.")
    import ddflow.api.lifecycle.pull as P

    assert P.launch_due is target


def test_launch_due_does_not_launch_while_launch_is_off(monkeypatch):
    import ddflow.api.lifecycle.pull as P

    cfg = Config()
    cfg.swarm.launch_enabled = False
    monkeypatch.setattr(P, "_load", lambda repo, agent="": (None, cfg, None))
    assert P.launch_due(_ctx(False)) is None


def test_launch_due_starts_the_first_idle_approved_lane(monkeypatch):
    import ddflow.api.lifecycle.pull as P
    from ddflow.services.lanes import launch as LN

    cfg = Config()
    cfg.swarm.launch_enabled = True

    class _Log:
        agent_id = ""

        def read_all(self):
            return []

    class _State:
        def active_leases(self, *_a, **_k):
            return {}  # no lane holds an item

    class _Reason:
        reason = ""

    t = LN.T.Template(name="dy", harness="kilo", route="r", family="deepseek", budget_usd=1.0)
    launched = []

    def _fake_launch(req):
        launched.append(req)
        return LN.Launched(t, object(), object(), object(), "ok")

    monkeypatch.setattr(P, "_load", lambda repo, agent="": (_Log(), cfg, _State()))
    monkeypatch.setattr(P, "plan_for", lambda *a, **k: object())
    monkeypatch.setattr(P.FL, "limit_for", lambda *a, **k: _Reason())
    monkeypatch.setattr(P, "caps", lambda *a, **k: object())
    monkeypatch.setattr(P, "_candidates", lambda *a, **k: ["A-1"])
    monkeypatch.setattr(P, "_slots", lambda *a, **k: P.D.Slots(0, 1))
    monkeypatch.setattr(P, "_world_and_items", lambda *a, **k: (object(), []))
    monkeypatch.setattr(P.modelreg, "load", lambda **k: None)
    monkeypatch.setattr(P.T, "load", lambda path: ({"dy": t}, []))
    monkeypatch.setattr(P, "_lane_worktree", lambda repo, name: repo / "wt")
    monkeypatch.setattr(P, "_lane_home", lambda repo, name: repo / "home")
    monkeypatch.setattr(P, "_source", lambda *a, **k: type("Src", (), {"template": t})())
    monkeypatch.setattr(P.D, "gaps", lambda src: ())
    monkeypatch.setattr(P.D, "pull_for", lambda *a, **k: P.D.Answer("offer", "dy", "A-1"))
    monkeypatch.setattr(LN, "launch", _fake_launch)

    note = P.launch_due(_ctx(True))
    assert launched and note == "launched lane 'dy' on A-1"
    assert launched[0].item == "A-1" and launched[0].agent == "dy"


def test_a_refused_lane_does_not_block_the_next_lane(monkeypatch):
    import ddflow.api.lifecycle.pull as P
    from ddflow.services.lanes import launch as LN

    cfg = Config()
    cfg.swarm.launch_enabled = True

    class _Log:
        agent_id = ""

        def read_all(self):
            return []

    class _State:
        def active_leases(self, *_a, **_k):
            return {}

    class _Reason:
        reason = ""

    t = LN.T.Template(name="dy", harness="kilo", route="r", family="deepseek", budget_usd=1.0)
    launched = []

    def _fake_launch(req):
        if req.agent == "a":
            return LN.Refused(("sticky refusal",))
        launched.append(req)
        return LN.Launched(t, object(), object(), object(), "ok")

    monkeypatch.setattr(P, "_load", lambda repo, agent="": (_Log(), cfg, _State()))
    monkeypatch.setattr(P, "plan_for", lambda *a, **k: object())
    monkeypatch.setattr(P.FL, "limit_for", lambda *a, **k: _Reason())
    monkeypatch.setattr(P, "caps", lambda *a, **k: object())
    monkeypatch.setattr(P, "_candidates", lambda *a, **k: ["A-1"])
    monkeypatch.setattr(P, "_slots", lambda *a, **k: P.D.Slots(0, 1))
    monkeypatch.setattr(P, "_world_and_items", lambda *a, **k: (object(), []))
    monkeypatch.setattr(P.modelreg, "load", lambda **k: None)
    monkeypatch.setattr(P.T, "load", lambda path: ({"a": t, "b": t}, []))
    monkeypatch.setattr(P, "_lane_worktree", lambda repo, name: repo / name)
    monkeypatch.setattr(P, "_lane_home", lambda repo, name: repo / "home")
    monkeypatch.setattr(P, "_source", lambda *a, **k: type("Src", (), {"template": t})())
    monkeypatch.setattr(P.D, "gaps", lambda src: ())
    monkeypatch.setattr(P.D, "pull_for", lambda name, *a, **k: P.D.Answer("offer", name, "A-1"))
    monkeypatch.setattr(LN, "launch", _fake_launch)

    note = P.launch_due(_ctx(True))
    assert note == "launched lane 'b' on A-1; also refused: sticky refusal"
    assert launched[0].agent == "b"
