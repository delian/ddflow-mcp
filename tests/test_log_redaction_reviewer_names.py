"""Local reviewer service and model names never reach the committed log (B8ef0d325ed).

A reviewer's endpoint, model name and key variable belong in the git-ignored
``.ddflow/local/`` layer. Its service name (and its model string) is none of a secret,
a host, a path or the machine's own name, so the committed-log redactor does not
recognise it and it leaks into the pushed shards as review evidence. Every value here
is synthetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.core.model import State
from ddflow.infra.log import EventLog
from ddflow.infra.log._eventlog import _is_local_endpoint, _reviewer_redact_names
from ddflow.services.gates.reviewers import _unapproved_reviewer

NAME = "local-reviewer-f8f8"  # a service name only the ignored reviewers.toml holds
MODEL = "Local/Reviewer-Flash"  # its model string
#: built at run time, so no tracked file names a private network host (test_repo_is_generic)
_PRIVATE_IP = ".".join(("10", "220", "230", "242"))


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".ddflow").mkdir(parents=True)
    (root / ".ddflow" / "reviewers.toml").write_text(
        "[[reviewer]]\n"
        f'name = "{NAME}"\n'
        'kind = "openai"\n'
        'base_url = "http://127.0.0.1:8000/v1"\n'
        f'model = "{MODEL}"\n'
    )
    return root


def _shard(root: Path) -> str:
    return "".join(p.read_text() for p in (root / ".ddflow" / "events").glob("*.jsonl"))


def test_a_local_reviewer_name_never_reaches_the_shard(tmp_path):
    root = _root(tmp_path)
    log = EventLog(root, "T")
    log.stamp = False
    log.append(
        "gate.failed",
        "T",
        {
            "gate": "rubber_duck",
            "reason": "",
            "evidence": {"reviewer": NAME, "model": MODEL, "family": "local"},
        },
    )
    shard = _shard(root)
    assert NAME not in shard, f"the reviewer service name {NAME!r} reached the committed log"
    assert MODEL not in shard, f"the reviewer model {MODEL!r} reached the committed log"
    assert "[REDACTED:name]" in shard


def test_the_configured_subject_keeps_the_name_for_the_trust_readback(tmp_path):
    # The unapproved-reviewer hint reads the name back from `reviewer.configured.subject`,
    # which is a verbatim event field, not `data`; the data copy is the redacted one.
    root = _root(tmp_path)
    log = EventLog(root, "T")
    log.stamp = False
    log.append(
        "reviewer.configured",
        NAME,
        {"digest": "d" * 16, "kind": "openai", "person": False, "user": ""},
    )
    shard = _shard(root)
    assert f'"subject":"{NAME}"' in shard, "the verbatim subject must keep the name"
    assert '"[REDACTED:name]"' not in shard, "a verbatim subject is not redacted"


def test_the_unapproved_reviewer_hint_reads_the_subject_not_the_redacted_evidence():
    dig = "d" * 16
    state = State()
    state.reviewer_writes[dig] = {"name": NAME}
    got = _unapproved_reviewer(state, {"reviewer": "[REDACTED:name]", "reviewer_digest": dig})
    assert got == NAME


@pytest.mark.parametrize(
    ("base_url", "local"),
    [
        ("http://localhost:8000/v1", True),
        ("http://localdomain:8000/v1", True),
        ("http://ollama:11434/v1", True),  # a single label is never a public host
        (f"http://{_PRIVATE_IP}:8000/v1", True),
        ("http://mybox.local/v1", True),
        ("http://x.internal/v1", True),
        ("http://x.lan/v1", True),
        ("http://box.home.arpa/v1", True),
        ("http://api.example.com/v1", False),
        ("http://8.8.8.8/v1", False),
        ("http://[::1", False),  # unterminated IPv6 literal -> urlparse raises -> not local
    ],
)
def test_is_local_endpoint(base_url, local):
    assert _is_local_endpoint(base_url) is local


def test_the_narrowed_redaction_read_does_not_alarm_about_fields_it_deliberately_ignores(
    tmp_path, capsys
):
    # `_reviewer_redact_names` reads reviewers.toml against a DELIBERATELY narrowed view
    # (`_LocalReviewer`: name/model/base_url only, so the log layer stays off
    # services.review). The fields it leaves out (family, gates, hedge, ...) ARE known to
    # the full services.review.Reviewer, so `tomlcfg._check` may not warn "which this
    # ddflow does not know ... config is newer than this code" for them (bug B506aa72f8e):
    # that reads as a false "all reviewers broken" alarm on every event-log append.
    root = tmp_path / "repo"
    (root / ".ddflow").mkdir(parents=True)
    (root / ".ddflow" / "reviewers.toml").write_text(
        '[[reviewer]]\n'
        f'name = "{NAME}"\n'
        'kind = "openai"\n'
        'family = "local"\n'
        'gates = ["critic", "rubber_duck"]\n'
        'base_url = "http://127.0.0.1:8000/v1"\n'
        f'model = "{MODEL}"\n'
        'hedge = 3\n'
        'max_chunk_chars = 5000\n'
        'temperature = 1.0\n'
    )
    capsys.readouterr()  # clear any prior stderr
    _reviewer_redact_names(root)
    _, err = capsys.readouterr()
    assert "does not know" not in err, (
        f"the narrowed redaction reader must not warn about fields it deliberately "
        f"ignores: {err!r}"
    )
