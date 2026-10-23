"""SU-client-table: which MCP clients honour `notifications/tools/list_changed`.

The table is keyed by `clientInfo.name` (R-su-client-key); a version is trusted only for the
clients whose version is real. Unknown means hard (D-soft-upgrade-capability). The module is
stdlib-only: the shim consults it.
"""

from __future__ import annotations

import ast
import sys
from datetime import date
from pathlib import Path

import pytest

from ddflow.mcpshim import clients as C

ROOT = Path(__file__).resolve().parents[1]
GOVERNS = ("ddflow/mcpshim/clients.py",)
TODAY = date(2026, 10, 23)


def cap(name, version="", **kw):
    info = {"name": name, **({"version": version} if version else {})}
    return C.capability(info, today=TODAY, **kw)


def test_the_module_is_stdlib_only():
    tree = ast.parse((ROOT / "ddflow/mcpshim/clients.py").read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0:
            mods.add((n.module or "").split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            raise AssertionError("the shim table must not import from the ddflow package")
    assert mods <= set(sys.stdlib_module_names), mods


# ---- the seeded rows ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,version",
    [
        ("claude-code", "2.1.296"),
        ("claude-code", "2.2.0"),
        ("kilo", "7.8.12"),
        ("opencode", "unknown"),
        ("gemini-cli-mcp-client", "0.30.0"),
        ("Cline", "3.0.0"),
        ("Zed", "0.200.0"),
        ("goose", "1.0.0"),
    ],
)
def test_clients_known_to_honour_list_changed(name, version):
    assert cap(name, version) == "honours"


@pytest.mark.parametrize(
    "name",
    [
        "qwen-code-mcp-client",
        "Roo Code",
        "continue-client",
        "continue-cli-client",
        "codex-mcp-client",
    ],
)
def test_clients_known_to_ignore_it(name):
    assert cap(name, "1.0.0") == "ignores"


def test_an_unknown_client_is_unknown_and_so_hard():
    assert cap("some-new-agent", "1.0") == "unknown"
    assert C.capability(None, today=TODAY) == "unknown"
    assert C.capability({}, today=TODAY) == "unknown"
    assert C.capability({"name": 7}, today=TODAY) == "unknown"


def test_names_match_case_insensitively_and_ignore_padding():
    assert cap("  Claude-Code ", "2.1.296") == "honours"
    assert cap("CLINE") == "honours"


def test_every_client_of_the_matrix_has_a_row_with_evidence():
    names = {e.key for e in C.ENTRIES}
    for expected in (
        "claude-code",
        "kilo",
        "opencode",
        "gemini-cli-mcp-client",
        "codex-mcp-client",
        "qwen-code-mcp-client",
        "kimi",
        "cline",
        "roo code",
        "continue",
        "zed",
        "goose",
        "openhands",
        "vscode-copilot",
        "cursor-ide",
        "cursor-cli",
        "grok-cli",
        "aider",
        "copilot-cli",
        "antigravity",
        "grok-build",
        "windsurf",
        "amp",
        "droid",
    ):
        assert expected in names, expected
    for e in C.ENTRIES:
        assert e.list_changed in C.LIST_CHANGED and e.source in C.SOURCES, e
        assert e.evidence, e.key


def test_the_three_unknown_kinds_are_not_confused_with_ignores():
    unknown = {e.key for e in C.ENTRIES if e.list_changed == "unknown"}
    assert {"copilot-cli", "antigravity", "grok-build", "windsurf", "amp", "droid"} <= unknown


# ---- versions ---------------------------------------------------------------------------


def test_a_ranged_client_below_its_verified_version_is_unknown():
    assert cap("claude-code", "2.1.295") == "unknown"
    assert cap("claude-code", "1.9.0") == "unknown"
    assert cap("kilo", "7.8.11") == "unknown"
    assert cap("kilo", "7.10.0") == "honours"  # numeric, not lexical, order


def test_a_ranged_client_with_no_usable_version_is_unknown():
    assert cap("claude-code") == "unknown"
    assert cap("claude-code", "weird") == "unknown"


def test_a_name_only_client_ignores_its_version():
    assert cap("qwen-code-mcp-client", "0.0.1") == "ignores"
    assert cap("qwen-code-mcp-client", "99.0.0") == "ignores"
    assert cap("opencode", "unknown") == "honours"
    assert cap("opencode") == "honours"


def test_version_ordering_handles_prefixes_and_suffixes():
    assert C.version_tuple("v2.1.296") == (2, 1, 296)
    assert C.version_tuple("2.1.296-beta.1") == (2, 1, 296)
    assert C.version_tuple("7.8") == (7, 8)
    assert C.version_tuple("") is None and C.version_tuple("x") is None


# ---- expiry -----------------------------------------------------------------------------


def test_a_stale_honours_row_without_a_verified_version_falls_back_to_unknown():
    old = C.Entry("zed2", "honours", "source", "", None, "2026-01-01", "x", reconnect="unknown")
    assert C.effective(old, "9.9", date(2026, 10, 23), max_age_days=100) == "unknown"
    assert C.effective(old, "9.9", date(2026, 3, 1), max_age_days=100) == "honours"


def test_a_stale_ignores_row_stays_ignores():
    old = C.Entry("old", "ignores", "source", "", None, "2020-01-01", "x", reconnect="unknown")
    assert C.effective(old, "1", date(2026, 10, 23), max_age_days=100) == "ignores"


def test_a_verified_row_with_a_version_does_not_expire_by_age():
    assert (
        C.capability({"name": "claude-code", "version": "2.1.296"}, today=date(2035, 1, 1))
        == "honours"
    )


# ---- the other ways to learn ----------------------------------------------------------------


def test_a_listen_optin_makes_any_client_honour():
    assert cap("some-new-agent", listen_optin=True) == "honours"
    assert cap("qwen-code-mcp-client", listen_optin=True) == "honours"


def test_an_observation_overrides_the_table_when_it_is_stronger():
    assert cap("some-new-agent", observed="honours") == "honours"
    assert cap("some-new-agent", observed="ignores") == "ignores"
    assert cap("claude-code", "2.1.296", observed="ignores") == "ignores"
    assert cap("some-new-agent", observed="bogus") == "unknown"


# ---- the other columns -----------------------------------------------------------------------


def test_reconnect_and_http_404_columns():
    cc = C.lookup({"name": "claude-code", "version": "2.1.296"})
    assert cc.reconnect == "respawn" and cc.http_404 == "reinit+retry"
    kilo = C.lookup({"name": "kilo", "version": "7.8.12"})
    assert kilo.reconnect == "manual"
    vs = next(e for e in C.ENTRIES if e.key == "vscode-copilot")
    assert vs.reconnect == "respawn+relist"
    assert all(e.reconnect in C.RECONNECT for e in C.ENTRIES)
    assert all(e.http_404 in C.HTTP_404 for e in C.ENTRIES)


def test_every_linked_harness_has_a_descriptor():
    have = {p.stem for p in (ROOT / "ddflow" / "harnesses").glob("*.toml")}
    for e in C.ENTRIES:
        if e.harness:
            assert e.harness in have, (e.key, e.harness)


def test_keys_are_unique_and_lowercase():
    keys = [e.key for e in C.ENTRIES]
    assert len(keys) == len(set(keys)) and all(k == k.lower() for k in keys)
    names = [n for e in C.ENTRIES for n in e.names]
    assert len(names) == len(set(names)) and all(n == n.lower() for n in names)


def test_the_matrix_document_lists_the_rows_the_table_seeds():
    text = (ROOT / "docs/ddflow/research/mcp-client-matrix.md").read_text().lower()
    for e in C.ENTRIES:
        if e.source in ("verified", "source"):
            assert e.key.split("-")[0] in text, e.key


def test_a_row_is_found_by_its_key_as_well_as_its_names():
    assert cap("kimi") == "ignores"
    assert cap("Kimi") == "ignores"
    assert C.lookup({"name": "claude-code"}).key == "claude-code"


def test_an_observation_of_unknown_means_nothing_was_learned():
    assert cap("opencode", observed="unknown") == "honours"
    assert cap("qwen-code-mcp-client", observed="unknown") == "ignores"
    assert cap("some-new-agent", observed="unknown") == "unknown"


def test_a_prerelease_of_the_verified_floor_is_below_it():
    assert cap("claude-code", "2.1.296-beta.1") == "unknown"
    assert cap("claude-code", "2.1.297-beta.1") == "honours"
    assert cap("kilo", "7.8.12-rc1") == "unknown"
