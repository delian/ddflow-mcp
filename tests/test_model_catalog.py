"""DRV-model-catalog: families, models and routes as fact records with provenance."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from ddflow.config_sections.agent import FAMILY_HINTS, family_for
from ddflow.infra import paths
from ddflow.services import modelreg as M
from ddflow.services import review


def _cat(local: str | None = None, tmp_path=None):
    if local is None:
        return M.load()
    (tmp_path / M.LOCAL_FILE).write_text(local, encoding="utf-8")
    return M.load(local_dir=tmp_path)


def test_shipped_catalog_is_well_formed():
    assert M.validate(M.load()) == []


def test_every_family_has_source_and_date():
    # `local` (resolved by route class) and `unknown` (no facts, by design) are the only exempt rows.
    cat = M.load()
    for fid, row in cat.family.items():
        if fid in ("local", "unknown"):
            continue
        ident = row["identity"]
        assert ident["src"] in cat.sources and ident["d"], fid


def test_a_fact_without_source_or_date_is_a_problem():
    cat = M.load()
    cat.model["deepseek-flash"]["window"] = {"v": 1, "s": "documented"}
    bad = M.validate(cat)
    assert any("source" in p for p in bad) and any("date" in p for p in bad)


def test_family_hints_reader_matches_the_former_table_in_order():
    assert list(M.family_hints(M.load()).items()) == list(FAMILY_HINTS.items())


def test_presets_reader_matches_review_presets():
    assert M.presets(M.load()) == review.PRESETS


def test_unknown_model_never_matches_a_neighbour():
    cat = M.load()
    for name in ("", "totally-new-model", "deepseek"[:4], "flash-9"):
        r = M.resolve(cat, name)
        assert (r.family, r.vendor, r.how) == (M.UNKNOWN, "", "unknown")
        assert M.read(cat, name, "window").value is None


def test_resolution_agrees_with_family_for():
    cat = M.load()
    for name in ("claude-sonnet-5", "Qwen3-coder", "gpt-5", "kimi-k3", "llama-3", "x-unknown"):
        assert M.resolve(cat, name).vendor == family_for(name)


def test_listed_model_wins_over_family_and_reads_price():
    cat = M.load()
    assert M.resolve(cat, "DeepSeek-V4-Pro").how == "model"
    assert M.read(cat, "deepseek-v4-pro", "price_in").value == 0.66
    assert M.read(cat, "deepseek-v4-pro", "price_out").value is None  # left out, not guessed


def test_cache_family_facts_are_read_with_model_scoping():
    cat = M.load()
    assert M.read(cat, "claude-sonnet-5.5", "read_mult").value == 0.05
    assert M.read(cat, "claude-opus-4", "read_mult").value == 0.1
    # an unverified fact is a lead: never read as true
    p = M.read(cat, "kimi-k3", "read_mult")
    assert p.value is None and "not verified" in p.reason


def test_unlisted_model_of_known_family_reads_family_window():
    assert M.read(M.load(), "deepseek-new", "window").value == 1000000


def test_local_route_is_free_and_window_comes_from_the_served_model(tmp_path):
    cat = _cat(
        """
[sources]
op = { note = "operator measured" }
[route.mybox]
class = "local"
endpoint = { v = { kind = "openai", base_url = "http://10.0.0.5:8000/v1", api_key_env = "BOX_KEY" }, s = "verified", src = "op", d = "2026-10-30" }
[route.mybox.served."qwen3-coder"]
window = { v = 131072, s = "verified", src = "op", d = "2026-10-30" }
rpm = { v = 30, s = "verified", src = "op", d = "2026-10-30" }
""",
        tmp_path,
    )
    assert M.validate(cat) == []
    assert M.read(cat, "qwen3-coder", "price_in", "mybox").value == 0
    assert M.read(cat, "qwen3-coder", "window", "mybox").value == 131072
    assert M.read(cat, "qwen3-coder", "rpm", "mybox").value == 30
    # the same model direct keeps its own (here: no) window; a route never leaks sideways
    assert M.read(cat, "qwen3-coder", "window").value is None
    assert "route.mybox" in cat.local_ids
    assert M.presets(cat)["mybox"]["base_url"].startswith("http://10.0.0.5")


def test_committed_files_hold_no_keys_and_no_remote_local_address():
    data = M.data_dir()
    text = "".join(p.read_text() for p in data.glob("*.toml"))
    assert not re.search(
        r'(?m)^\s*\w*(key|token|secret)\w*\s*=\s*"[^"]{8,}"', text.replace("_env", "")
    )
    assert not re.search(r"\b(sk-[A-Za-z0-9_-]{16,}|Bearer [A-Za-z0-9._-]{16,})", text)
    cat = M.load()
    for rid, row in cat.route.items():
        if row.get("class") == "local":
            url = row["endpoint"]["v"].get("base_url", "")
            assert urlparse(url).hostname in ("127.0.0.1", "localhost"), (rid, url)


def test_drifted_fact_is_not_believed():
    cat = M.load()
    cat.model["deepseek-flash"]["window"]["expires"] = "2026-01-01"
    p = M.read(cat, "deepseek-flash", "window", today=date(2026, 10, 30))
    # the expired model fact is skipped; the cache-family table answers, and says so
    assert p.value == 1000000 and p.reason == "cache-family"


def test_stale_names_old_facts():
    old = M.stale(M.load(), 30, today=date(2027, 1, 1))
    assert any(s.startswith("model.deepseek-flash.window") for s in old)
    assert M.stale(M.load(), 30, today=date(2026, 10, 30)) == []


def test_files_ship_inside_the_package():
    assert M.data_dir().is_relative_to(paths.package_dir())
    assert {p.name for p in M.data_dir().glob("*.toml")} == {
        "families.toml",
        "models.toml",
        "routes.toml",
    }


def test_local_served_facts_merge_per_model_not_replace(tmp_path):
    fact = '{ v = %d, s = "verified", src = "op", d = "2026-10-30" }'
    cat = _cat(
        '[sources]\nop = { note = "x" }\n'
        f"[route.vllm.served.m1]\nwindow = {fact % 8}\n"
        f"[route.vllm.served.m2]\nwindow = {fact % 9}\n",
        tmp_path,
    )
    assert M.read(cat, "m1", "window", "vllm").value == 8
    assert M.read(cat, "m2", "window", "vllm").value == 9


def test_local_overlay_does_not_drop_shipped_served_facts(tmp_path):
    shipped = M.load()
    shipped.route["vllm"]["served"] = {"m": {"window": {"v": 1}}}
    M._merge_row(shipped.route["vllm"], {"served": {"m": {"rpm": {"v": 2}}, "n": {}}})
    assert set(shipped.route["vllm"]["served"]["m"]) == {"window", "rpm"}
    assert "n" in shipped.route["vllm"]["served"]


def test_listed_model_with_a_missing_family_is_unknown_not_a_needle_match():
    cat = M.load()
    cat.model["claude-ish"] = {"family": "missing"}
    assert M.resolve(cat, "claude-ish").family == M.UNKNOWN
    assert any("claude-ish" in p for p in M.validate(cat))


def test_litellm_proxy_is_not_priced_as_local():
    cat = M.load()
    assert M.read(cat, "qwen3:8b", "price_in", "litellm").value is None


def test_non_fact_values_and_bad_identity_are_problems():
    cat = M.load()
    cat.model["deepseek-flash"]["window"] = 1000000
    cat.family["claude"]["identity"]["v"] = {"needles": "claude"}
    bad = " ".join(M.validate(cat))
    assert "model.deepseek-flash.window is not a fact" in bad
    assert "family.claude.identity" in bad


def test_malformed_local_rows_are_problems_not_crashes():
    cat = M.load()
    cat.model["bad"] = {"family": {}}
    cat.route["r"] = {"class": [], "served": {"model": "oops"}}
    bad = M.validate(cat)
    assert any("model.bad.family must be a string" in p for p in bad)
    assert any("route.r.served must be a table of tables" in p for p in bad)
    assert M.resolve(cat, "bad").family == M.UNKNOWN


def test_loose_value_under_served_is_a_problem():
    cat = M.load()
    cat.route["vllm"]["served"] = {"m": {"window": 128000}}
    assert any("served" in p and "is not a fact" in p for p in M.validate(cat))


def test_malformed_served_overlay_loads_and_is_reported():
    base = M.load()
    base.route["vllm"]["served"] = {"m": {"window": {"v": 1}}}
    M._merge_row(base.route["vllm"], {"served": {"m": 1}})
    assert any("served" in p for p in M.validate(base))
    assert M.read(base, "m", "window", "vllm").value is None


def test_a_row_that_is_not_a_table_is_reported(tmp_path):
    cat = _cat('[model]\ndeepseek-flash = "paid"\n', tmp_path)
    assert "model.deepseek-flash is not a table" in M.validate(cat)


def test_malformed_top_level_and_identity_do_not_crash(tmp_path):
    cat = _cat('model = "oops"\nsources = 3\n', tmp_path)
    assert "model is not a table of rows" in M.validate(cat)
    cat = M.load()
    cat.family["claude"]["identity"]["v"]["needles"] = [1, "x"]
    assert M.resolve(cat, "claude-x").family == M.UNKNOWN
    assert any("claude.identity" in p for p in M.validate(cat))
    cat = M.load()
    cat.route["openai"]["endpoint"]["v"] = "http://x"
    assert any("route.openai.endpoint" in p for p in M.validate(cat))
    assert "openai" not in M.presets(cat)


def test_falsy_non_table_top_level_value_is_reported(tmp_path):
    cat = _cat("family = []\n", tmp_path)
    assert "family is not a table of rows" in M.validate(cat)
