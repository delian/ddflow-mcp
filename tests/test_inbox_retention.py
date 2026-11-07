"""B-inbox-message-gone-after-heartbeat: a delivered inbox message is RETAINED and readable
again, not deleted, so a ``ddflow heartbeat`` piped through ``tail``/``cut`` that lost the
text can still be shown whole (``ddflow session inbox``)."""

from __future__ import annotations

import json

from ddflow.services import inbox as IB


def test_a_delivered_message_can_still_be_read_after_take(repo):
    sent, why = IB.send(repo, "lane", "hello steady", sender="coordinator")
    assert sent is not None, why
    got = IB.take(repo, "lane")
    assert [m.text for m in got] == ["hello steady"]

    # Delivered once -- not pending any more -- but not gone: the read verb still shows it.
    assert IB.pending(repo, "lane") == []
    assert [m.text for m in IB.delivered(repo, "lane", retain_s=86400)] == ["hello steady"]


def test_delivered_prunes_a_message_once_retention_has_passed(repo):
    assert IB.send(repo, "lane", "old news", sender="coordinator")[0] is not None
    assert IB.take(repo, "lane")

    # Backdate the retained message past the retention window; reading then prunes it.
    path = sorted(IB._delivered_dir(repo, "lane").glob("*.json"))[0]
    raw = json.loads(path.read_text("utf-8"))
    raw["delivered_at"] = raw["delivered_at"] - 100_000
    path.write_text(json.dumps(raw))

    assert IB.delivered(repo, "lane", retain_s=60) == []
    assert not path.exists()


def test_delivery_is_once_even_when_retention_cannot_write(repo):
    assert IB.send(repo, "lane", "hi", sender="coordinator")[0] is not None
    # A foreign regular file where the delivered/ dir would go: retention cannot write there.
    IB._delivered_dir(repo, "lane").parent.mkdir(parents=True, exist_ok=True)
    IB._delivered_dir(repo, "lane").write_text("foreign")

    got = IB.take(repo, "lane")
    assert [m.text for m in got] == ["hi"]
    assert IB.pending(repo, "lane") == []  # delivered once, never pending twice

