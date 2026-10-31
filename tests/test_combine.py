"""DRV-combine: the pure combination rule."""

from datetime import date

from ddflow.services import combine, modelreg


def F(v, s="documented", **kw):
    return {"v": v, "s": s, "src": "t", "d": "2026-10-01", **kw}


def H(**f):
    return {"id": "claude-code", "facts": f}


def M(fam="claude", mid="claude-sonnet-5", **f):
    return {"id": mid, "family": fam, "facts": f, "family_facts": {}}


def test_claude_code_on_non_anthropic_route_loses_explicit_cache():
    m = M(**{"cache.explicit": F(True)})
    anth = combine.effective(H(), m, {"id": "anthropic", "facts": {}})
    gw = combine.effective(H(), m, {"id": "gw", "facts": {"cache.explicit": F(False)}})
    assert anth["cache.explicit"] is True and anth.fields["cache.explicit"].source == "model"
    assert gw["cache.explicit"] is False and gw.fields["cache.explicit"].source == "route"


def test_copilot_long_context_pair_tier():
    m = M("openai", "gpt-5", price_in=F(1.0))
    pair = combine.match_pair(
        [{"harness": "copilot", "model": "openai", "facts": {"price_in": F(2.0)}}],
        "copilot",
        "gpt-5",
        "openai",
    )
    r = combine.effective({"id": "copilot", "facts": {}}, m, None, pair=pair, keys=["price_in"])
    assert r["price_in"] == 2.0 and r.fields["price_in"].source == "pair"
    none = combine.effective(H(), m, keys=["price_in"], pair=combine.match_pair([], "x", "y", "z"))
    assert none["price_in"] == 1.0


def test_unknown_model_gives_lcd_and_names_it():
    r = combine.effective(H(), M("unknown", "mystery-9"), keys=["cache.explicit", "window"])
    assert r["cache.explicit"] is False and r.fields["cache.explicit"].unknown
    assert r.unknown_models == ("mystery-9",)
    assert "mystery-9" in r.fields["window"].reason and r["window"] is None


def test_caps_take_the_minimum_and_prohibition_wins():
    m = M(window=F(1_000_000), long_context=F(True))
    h = H(**{"cap.window": F(400_000)})
    op = {"cap.window": F(200_000), "prohibit.long_context": F(True)}
    r = combine.effective(h, m, None, operator=op, keys=["window", "long_context"])
    assert r["window"] == 200_000 and r.fields["window"].source == "cap"
    assert r["long_context"] is False and r.fields["long_context"].source == "prohibit"
    loose = combine.effective(h, m, None, keys=["window"])
    assert loose["window"] == 400_000


def test_drifted_fact_does_not_beat_verified():
    stale = F(True, "verified", expires="2026-01-01")
    m = M(**{"cache.explicit": F(False, "verified")})
    r = combine.effective(
        H(),
        m,
        {"id": "r", "facts": {"cache.explicit": stale}},
        today=date(2026, 10, 31),
        keys=["cache.explicit"],
    )
    assert r["cache.explicit"] is False and r.fields["cache.explicit"].source == "model"
    assert "expired" in r.fields["cache.explicit"].reason


def test_observation_beats_operator_and_deterministic():
    kw = {
        "operator": {"window": F(1)},
        "observed": {"window": F(2, "verified")},
        "keys": ["window"],
    }
    a = combine.effective(H(), M(window=F(9)), **kw)
    assert (
        a["window"] == 2 and a.reasons() == combine.effective(H(), M(window=F(9)), **kw).reasons()
    )


def test_catalog_adapter_unknown_resolves_to_unknown_family():
    cat = modelreg.Catalog()
    got = combine.model_layer(cat, "who-knows")
    assert got["model"]["family"] == "unknown" and got["model"]["facts"] == {}
    r = combine.effective(H(), got["model"], got["route"], keys=["window"])
    assert r.unknown_models == ("who-knows",)
