"""DRV-cli doc parity: the model noun ships only `show`, so the docs must not promise more."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _norm(s: str) -> str:
    # a promise table row escapes its pipes; cover both `show|lint` and `show\|lint`
    return s.replace("\\", "")


# The stale over-promise, in every CLI form a doc could state it. `ddflow model lint`,
# `probe` and `resolve` are verbs the model noun never shipped; a bare `model resolve`
# in prose (the internal resolve function) is legitimate.
_STALE_CLI = (
    "ddflow model show|lint|probe|resolve",
    "ddflow model lint",
    "ddflow model probe",
    "ddflow model resolve",
)


def test_research_doc_does_not_promise_model_cli_verbs_beyond_show():
    text = _norm((_ROOT / "docs/ddflow/research/driver-architecture.md").read_text())
    for stale in _STALE_CLI:
        assert stale not in text, f"research doc still promises {stale!r}"


def test_decision_drv6_overrides_the_model_verb_promise():
    text = (_ROOT / "docs/ddflow/decisions/driver-architecture.md").read_text()
    rows = [ln for ln in text.splitlines() if "D-DRV-6" in ln]
    assert rows, "D-DRV-6 row missing"
    row = _norm(rows[0])
    assert "model show|lint|probe|resolve" not in row, (
        "D-DRV-6 keeps the unimplemented verbs as the decided model surface"
    )
    assert "`ddflow model show`" in row and "overrid" in row.lower(), (
        "D-DRV-6 no longer states what the model noun actually shipped"
    )
