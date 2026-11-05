"""``session end()`` redacts the summary before it reaches the committed log (B3ae931789e).

``end`` writes the session summary the same redacted way ``prompt`` and ``note`` do,
instead of trusting the log-level writer alone. The regression is the value ``end``
hands to the log: on the fixed code it is already clean.
"""

from __future__ import annotations

from ddflow.infra.log import EventLog
from ddflow.services import sessions as S

#: Synthetic, assembled at run time so the repository holds no credential-shaped literal.
_KEY = "sk-" + "FAKE1234" * 4


def test_end_redacts_the_summary_before_it_is_written(tmp_path, monkeypatch):
    log = EventLog(tmp_path)
    handed_to_log: dict = {}
    real_append = EventLog.append

    def spy(self, kind, subject, data=None, **kw):
        if kind == "session.ended":
            handed_to_log["summary"] = (data or {}).get("summary")
        return real_append(self, kind, subject, data, **kw)

    monkeypatch.setattr(EventLog, "append", spy)
    S.end(log, "s1", summary=f"all good {_KEY}")

    clean = handed_to_log.get("summary")
    assert clean is not None
    assert _KEY not in clean
    assert "all good" in clean
