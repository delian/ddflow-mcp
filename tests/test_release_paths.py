"""Every release of a claim drops its remote claim ref (Bd45d1ad60e).

`resolve` (the losers of a contest) and identity re-homing appended `lease.released`
directly, skipping `leases.release`'s `_remote_drop`: with `[flow].claims = "remote"` the
claim ref on the remote was never dropped, and a re-homed lease's new holder could not
renew a ref that still named the old one.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_identity_upgrade import _claimed_before_the_upgrade, _derived, _lease
from test_lease_contest import _REPRO, _acq, _ev

from ddflow import api
from ddflow.infra import claimref as CR
from ddflow.infra.log import EventLog


class _Remote:
    """A claim-ref remote that answers every call and records it."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple[str, str, str]] = []
        for verb in ("take", "renew", "drop"):
            monkeypatch.setattr(CR, verb, self._answer(verb))

    def _answer(self, verb):
        def answer(root, remote, item, holder, *rest):
            self.calls.append((verb, item, holder))
            return CR.Result("ok", holder, time.time() + 600)

        return answer


def _remote_claims(repo: Path) -> None:
    cfg = repo / ".ddflow" / "config.toml"
    cfg.parent.mkdir(exist_ok=True)
    text = cfg.read_text() if cfg.exists() else ""
    assert "[flow]" not in text, "fixture: a [flow] table would need the key set inside it"
    cfg.write_text(text + '\n[flow]\nclaims = "remote"\n')


def test_resolve_drops_each_losing_claims_remote_ref(repo: Path, monkeypatch):
    remote = _Remote(monkeypatch)
    _remote_claims(repo)
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    evs = [_ev("task.added", "T", 1, title="t")]
    evs += [_acq(2 + i, who, *_REPRO[who]) for i, who in enumerate(_REPRO)]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in evs))
    out = api.items.resolve(repo, "T", keep="bob", agent="op")
    assert out.exit == 0, out.reason
    assert ("drop", "T", "alice") in remote.calls, remote.calls
    assert ("take", "T", "bob") in remote.calls, remote.calls


def test_re_homing_moves_the_remote_claim_to_the_new_id(repo: Path, monkeypatch):
    remote = _Remote(monkeypatch)
    _remote_claims(repo)
    bare = _claimed_before_the_upgrade(repo, monkeypatch)
    me = _derived(repo)
    remote.calls.clear()
    assert api.heartbeat(repo, "T1").exit == 0
    assert _lease(repo, "T1").holder == me
    assert ("drop", "T1", bare) in remote.calls, remote.calls
    assert ("take", "T1", me) in remote.calls, remote.calls
