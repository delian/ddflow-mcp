"""DRV-cli doc parity: the model noun ships only `show`, so the docs must not promise more."""

from __future__ import annotations

from pathlib import Path

_DOCS = (
    "docs/ddflow/decisions/driver-architecture.md",
    "docs/ddflow/research/driver-architecture.md",
)


def test_driver_docs_do_not_over_promise_model_cli_verbs():
    root = Path(__file__).resolve().parents[1]
    for rel in _DOCS:
        text = (root / rel).read_text().replace("\\", "")
        assert "model show|lint|probe|resolve" not in text, (
            f"{rel} still promises model lint|probe|resolve; the model noun ships only `show`"
        )
