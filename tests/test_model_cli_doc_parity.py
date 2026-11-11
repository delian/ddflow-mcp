"""DRV-cli doc parity: the model noun ships only `show`, so the docs must not promise more."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# The stale over-promise, in every form a doc could state it. `ddflow model lint`,
# `probe` and `resolve` are CLI verbs the model noun never shipped, so a bare
# "model <verb>" is fine only when it does not read as a CLI command (e.g. the
# internal `model resolve` function in the research doc is legitimate).
_STALE_CLI = (
    "ddflow model show|lint|probe|resolve",
    "ddflow model lint",
    "ddflow model probe",
    "ddflow model resolve",
)


def test_research_doc_does_not_promise_model_cli_verbs_beyond_show():
    text = (_ROOT / "docs/ddflow/research/driver-architecture.md").read_text()
    for stale in _STALE_CLI:
        assert stale not in text, f"research doc still promises {stale!r}"


def test_decision_drv6_notes_the_shipped_model_surface():
    text = (_ROOT / "docs/ddflow/decisions/driver-architecture.md").read_text()
    rows = [ln for ln in text.splitlines() if "D-DRV-6" in ln]
    assert rows, "D-DRV-6 row missing"
    # the decision keeps its decided verbs but states what actually shipped
    assert "`ddflow model show` only" in text, "D-DRV-6 no longer records the shipped surface"
    assert "read-only" in text, "D-DRV-6 no longer records the read-only model catalog"
