"""Quota meters, the vendor side and the registry (D-quotas-governor, r-no-silent-cost)."""

from __future__ import annotations

import contextlib
import json
import stat
from pathlib import Path
from typing import ClassVar

import pytest
from quota_meters_support import HOUR, NOW, ctx, iso, none_of, of, window, write_cache

from ddflow.adapters.registry import DuplicateName
from ddflow.config import Config
from ddflow.services import meters as M
from ddflow.services import meters_base as MB
from ddflow.services import meters_vendor as V


class TestReadingInvariant:
    def test_ok_needs_a_number(self):
        with pytest.raises(ValueError, match="number"):
            MB.Reading("s", "h", "5h", "percent", MB.OK, None, iso(NOW), False)

    def test_unavailable_cannot_carry_a_value(self):
        with pytest.raises(ValueError, match="never a value"):
            MB.Reading("s", "h", "5h", "percent", MB.UNAVAILABLE, 0.0, "", False, reason="x")

    def test_unavailable_needs_a_reason(self):
        with pytest.raises(ValueError, match="reason"):
            MB.Reading("s", "h", "5h", "percent", MB.UNAVAILABLE, None, "", False)

    def test_nan_is_not_a_number(self):
        with pytest.raises(ValueError, match="number"):
            MB.Reading("s", "h", "5h", "percent", MB.OK, float("nan"), iso(NOW), False)


class TestPercent:
    @pytest.mark.parametrize(
        ("raw", "scale", "want"),
        [(43, 1, 43.0), (0.5, 1, 0.5), (0, 1, 0.0), (0.53, 100, 53.0), (1, 100, 100.0)],
    )
    def test_normalises(self, raw, scale, want):
        assert MB.percent(raw, scale) == pytest.approx(want)

    @pytest.mark.parametrize(
        "raw", [None, "43", True, float("nan"), float("inf"), -1, 101, 10**400]
    )
    def test_refuses(self, raw):
        assert MB.percent(raw, 1) is None

    def test_a_wrong_scale_is_refused_not_clamped(self):
        assert MB.percent(53, 100) is None  # a 0-100 figure on a 0-1 source


class TestCacheReader:
    def test_fresh_reading(self, tmp_path):
        c = ctx(tmp_path)
        write_cache(c.claude_state, five=window(43), week=window(36, 48 * HOUR))
        got = V.read_claude_cache(c)
        five, week = of(got, "5h"), of(got, "week")
        assert (five.status, five.value, five.unit, five.stale) == (MB.OK, 43.0, "percent", False)
        assert five.source == "claude-cache" and five.observed_at == iso(NOW - 10)
        assert five.resets_at == iso(NOW + HOUR)
        assert week.value == 36.0

    def test_the_real_cache_shape_is_a_percent_not_a_fraction(self, tmp_path):
        """The shape read from a real ~/.claude.json: `limits` repeats each figure as `percent`,
        which is what shows `utilization` is 0-100 here, unlike stream-json's 0-1."""
        c = ctx(tmp_path)
        resets = iso(NOW + HOUR)
        write_cache(
            c.claude_state,
            five={"utilization": 43, "resets_at": resets, "limit_dollars": None},
            week={"utilization": 36, "resets_at": resets, "limit_dollars": None},
        )
        data = json.loads(c.claude_state.read_text())
        table = data["cachedUsageUtilization"]["utilization"]
        table["limits"] = [{"kind": "session", "percent": 43, "resets_at": resets}]
        c.claude_state.write_text(json.dumps(data))
        got = of(V.read_claude_cache(c), "5h")
        assert got.value == table["limits"][0]["percent"]

    def test_stale_by_age(self, tmp_path):
        c = ctx(tmp_path)
        write_cache(c.claude_state, age_s=901, five=window(43), week=window(36))
        assert of(V.read_claude_cache(c), "5h").stale is True

    def test_stale_once_the_reset_passed(self, tmp_path):
        c = ctx(tmp_path)
        write_cache(c.claude_state, five=window(43, -1), week=window(36))
        got = V.read_claude_cache(c)
        assert of(got, "5h").stale is True and of(got, "week").stale is False

    def test_a_fetch_from_the_future_is_stale(self, tmp_path):
        c = ctx(tmp_path)
        write_cache(c.claude_state, age_s=-4000, five=window(43), week=window(36))
        assert of(V.read_claude_cache(c), "5h").stale is True

    def test_a_null_window_is_unavailable(self, tmp_path):
        c = ctx(tmp_path)
        write_cache(c.claude_state, five=None, week=window(36))
        got = V.read_claude_cache(c)
        assert of(got, "5h").status == MB.UNAVAILABLE and of(got, "5h").value is None
        assert of(got, "week").status == MB.OK

    def test_a_measured_zero_stays_zero(self, tmp_path):
        c = ctx(tmp_path)
        write_cache(c.claude_state, five=window(0), week=window(36))
        five = of(V.read_claude_cache(c), "5h")
        assert (five.status, five.value) == (MB.OK, 0.0)

    @pytest.mark.parametrize(
        "body",
        ["", "{not json", "[]", '{"cachedUsageUtilization": 3}', '{"other": 1}'],
    )
    def test_bad_file_is_unavailable_for_every_window(self, tmp_path, body):
        c = ctx(tmp_path)
        c.claude_state.write_text(body)
        got = V.read_claude_cache(c)
        assert {r.window for r in got} == {"5h", "week"}
        assert none_of(got) and all(r.reason for r in got)

    def test_missing_file_is_unavailable(self, tmp_path):
        got = V.read_claude_cache(ctx(tmp_path))
        assert none_of(got) and {r.window for r in got} == {"5h", "week"}

    def test_no_fetch_time_is_unavailable(self, tmp_path):
        c = ctx(tmp_path)
        c.claude_state.write_text(
            json.dumps({"cachedUsageUtilization": {"utilization": {"five_hour": window(4)}}})
        )
        assert of(V.read_claude_cache(c), "5h").status == MB.UNAVAILABLE

    def test_raw_account_id_is_never_carried(self, tmp_path):
        c = ctx(tmp_path)
        write_cache(c.claude_state, five=window(1), week=window(2))
        five = of(V.read_claude_cache(c), "5h")
        assert five.account and "1111" not in five.account
        assert "11111111" not in json.dumps(five.to_dict())


class TestSnapshot:
    STATUS: ClassVar[dict] = {
        "rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": int(NOW + HOUR)},
            "seven_day": {"used_percentage": 41.2, "resets_at": int(NOW + 48 * HOUR)},
        }
    }

    def test_statusline_round_trip(self, tmp_path):
        c = ctx(tmp_path)
        assert V.snapshot_statusline(c, json.dumps(self.STATUS)) == 2
        got = V.read_claude_snapshot(c)
        five = of(got, "5h")
        assert (five.status, five.value, five.stale) == (MB.OK, 23.5, False)
        assert five.resets_at == iso(NOW + HOUR) and five.observed_at == iso(NOW)
        assert five.detail["origin"] == "statusline"
        assert of(got, "week").value == 41.2

    def test_the_snapshot_is_private(self, tmp_path):
        c = ctx(tmp_path)
        V.snapshot_statusline(c, json.dumps(self.STATUS))
        assert stat.S_IMODE(c.snapshot.stat().st_mode) == 0o600

    @pytest.mark.parametrize(
        "text",
        ["", "not json", "{}", '{"rate_limits": null}', '{"rate_limits": {"five_hour": {}}}', "[]"],
    )
    def test_no_rate_limits_writes_nothing_never_zero(self, tmp_path, text):
        c = ctx(tmp_path)
        assert V.snapshot_statusline(c, text) == 0
        assert not c.snapshot.exists()
        assert none_of(V.read_claude_snapshot(c))

    def test_an_absent_window_keeps_the_last_reading(self, tmp_path):
        c = ctx(tmp_path)
        V.snapshot_statusline(c, json.dumps(self.STATUS))
        later = ctx(tmp_path, now=NOW + 60)
        only_week = {"rate_limits": {"seven_day": self.STATUS["rate_limits"]["seven_day"]}}
        assert V.snapshot_statusline(later, json.dumps(only_week)) == 1
        assert of(V.read_claude_snapshot(later), "5h").observed_at == iso(NOW)

    def test_an_older_figure_never_replaces_a_newer_one(self, tmp_path):
        new, old = ctx(tmp_path, now=NOW + 100), ctx(tmp_path, now=NOW)
        late = {"rate_limits": {"five_hour": {"used_percentage": 80, "resets_at": int(NOW + HOUR)}}}
        early = {"rate_limits": {"five_hour": {"used_percentage": 0, "resets_at": int(NOW + HOUR)}}}
        V.snapshot_statusline(new, json.dumps(late))
        assert V.snapshot_statusline(old, json.dumps(early)) == 0
        assert of(V.read_claude_snapshot(new), "5h").value == 80.0

    def test_stream_event_single_window_is_rescaled(self, tmp_path):
        c = ctx(tmp_path)
        line = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed_warning",
                "rateLimitType": "five_hour",
                "utilization": 0.81,
                "resetsAt": int(NOW + HOUR),
            },
        }
        assert V.snapshot_stream_line(c, json.dumps(line)) == 1
        five = of(V.read_claude_snapshot(c), "5h")
        assert five.value == pytest.approx(81.0) and five.detail["origin"] == "stream-json"

    def test_stream_event_unified_windows(self, tmp_path):
        c = ctx(tmp_path)
        info = {
            "status": "allowed",
            "unifiedWindows": {
                "five_hour": {"utilization": 0.1, "resetsAt": int(NOW + HOUR)},
                "seven_day": {"utilization": 0.2, "resetsAt": int(NOW + 2 * HOUR)},
            },
        }
        line = {"type": "rate_limit_event", "rate_limit_info": info}
        assert V.snapshot_stream_line(c, json.dumps(line)) == 2
        got = V.read_claude_snapshot(c)
        assert of(got, "5h").value == pytest.approx(10.0)
        assert of(got, "week").value == pytest.approx(20.0)

    def test_a_unified_window_with_no_figure_does_not_hide_the_top_level_one(self, tmp_path):
        c = ctx(tmp_path)
        info = {
            "rateLimitType": "five_hour",
            "utilization": 0.42,
            "resetsAt": int(NOW + HOUR),
            "unifiedWindows": {"five_hour": {"resetsAt": int(NOW + HOUR)}},
        }
        line = {"type": "rate_limit_event", "rate_limit_info": info}
        assert V.snapshot_stream_line(c, json.dumps(line)) == 1
        assert of(V.read_claude_snapshot(c), "5h").value == pytest.approx(42.0)

    @pytest.mark.parametrize(
        "info",
        [
            {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": 1},  # no figure
            {"rateLimitType": "five_hour", "utilization": 53, "resetsAt": 1},  # wrong scale
            {"rateLimitType": "overage", "utilization": 0.5, "resetsAt": 1},  # not our window
        ],
    )
    def test_stream_event_without_a_usable_figure_writes_nothing(self, tmp_path, info):
        c = ctx(tmp_path)
        line = {"type": "rate_limit_event", "rate_limit_info": info}
        assert V.snapshot_stream_line(c, json.dumps(line)) == 0
        assert not c.snapshot.exists()

    def test_other_stream_lines_are_ignored(self, tmp_path):
        c = ctx(tmp_path)
        assert V.snapshot_stream_line(c, '{"type": "assistant"}') == 0
        assert V.snapshot_stream_line(c, "torn") == 0

    def test_a_version_of_true_is_not_this_version(self, tmp_path):
        c = ctx(tmp_path)
        c.snapshot.parent.mkdir(parents=True)
        row = {"percent": 0, "observed_at": iso(NOW)}
        c.snapshot.write_text(json.dumps({"version": True, "windows": {"5h": row}}))
        assert none_of(V.read_claude_snapshot(c))

    def test_corrupt_snapshot_is_unavailable_and_a_new_write_heals_it(self, tmp_path):
        c = ctx(tmp_path)
        c.snapshot.parent.mkdir(parents=True)
        c.snapshot.write_text("{torn")
        assert none_of(V.read_claude_snapshot(c))
        assert V.snapshot_statusline(c, json.dumps(self.STATUS)) == 2
        assert of(V.read_claude_snapshot(c), "5h").status == MB.OK

    def test_a_bad_reset_time_keeps_the_figure_without_a_reset(self, tmp_path):
        c = ctx(tmp_path)
        body = {"rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": True}}}
        assert V.snapshot_statusline(c, json.dumps(body)) == 1
        five = of(V.read_claude_snapshot(c), "5h")
        assert (five.value, five.resets_at) == (5.0, "")


class TestRegistry:
    def test_a_raising_reader_is_unavailable_never_a_raise(self, tmp_path, monkeypatch):
        def boom(_c):
            raise RuntimeError("no")

        monkeypatch.setitem(M.READERS.table, "boom", boom)
        got = [r for r in M.read_all(ctx(tmp_path)) if r.source == "boom"]
        assert len(got) == 1 and got[0].status == MB.UNAVAILABLE and "no" in got[0].reason

    def test_a_name_cannot_be_registered_twice(self):
        with pytest.raises(DuplicateName, match="already"):
            M.READERS.register(V.read_claude_cache, "claude-cache")

    def test_nothing_readable_gives_no_number_at_all(self, tmp_path):
        got = M.read_all(ctx(tmp_path))
        assert none_of(got)

    def test_names_limit_the_readers(self, tmp_path):
        assert {r.source for r in M.read_all(ctx(tmp_path), ["kilo"])} == {"kilo"}


def _ok(source, value, *, stale=False, at=NOW, window_="5h"):
    return MB.Reading(source, "claude-code", window_, "percent", MB.OK, value, iso(at), stale)


def _gone(source):
    return MB.unavailable(source, "claude-code", "5h", "percent", "gone", NOW)


class TestBest:
    def test_fresh_beats_stale_whatever_the_order(self):
        assert M.best([_ok("a", 1, stale=True), _ok("b", 2)], "5h", "percent").source == "b"

    def test_newest_of_the_fresh(self):
        got = M.best([_ok("a", 1, at=NOW - 5), _ok("b", 2, at=NOW)], "5h", "percent")
        assert got.source == "b"

    def test_a_stale_figure_beats_none_and_says_so(self):
        got = M.best([_gone("a"), _ok("b", 2, stale=True)], "5h", "percent")
        assert got.source == "b" and got.stale is True

    def test_only_unavailable_stays_unavailable(self):
        assert M.best([_gone("a")], "5h", "percent").status == MB.UNAVAILABLE

    def test_no_reader_for_it_is_none(self):
        assert M.best([_ok("a", 1)], "week", "percent") is None

    def test_a_broken_reader_is_never_read_as_no_reader(self, tmp_path, monkeypatch):
        def boom(_c):
            raise RuntimeError("no")

        monkeypatch.setitem(M.READERS.table, "boom", boom)
        got = M.best(M.read_all(ctx(tmp_path), ["boom"]), "5h", "tokens")
        assert got is not None and got.status == MB.UNAVAILABLE and "no" in got.reason

    def test_only_a_reader_failure_answers_for_what_it_might_have_covered(self):
        other = MB.unavailable("cache", "claude-code", "", "percent", "gone", NOW)
        assert M.best([other], "5h", "tokens") is None

    def test_a_real_figure_beats_a_broken_reader(self, tmp_path):
        broken = MB.unavailable("boom", "", "", "", "RuntimeError: no", NOW)
        assert M.best([broken, _ok("a", 1)], "5h", "percent").source == "a"


def _rows(c):
    if not c.samples.exists():
        return []
    rows = []
    for line in c.samples.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


class TestSamples:
    def test_written_private_and_read_back(self, tmp_path):
        c = ctx(tmp_path)
        M.record(c, [_ok("a", 7.0), _gone("b")])
        rows = _rows(c)
        assert [r["value"] for r in rows] == [7.0, None]
        assert rows[1]["status"] == MB.UNAVAILABLE
        assert stat.S_IMODE(c.samples.stat().st_mode) == 0o600

    def test_a_torn_last_line_does_not_defeat_the_throttle(self, tmp_path):
        c = ctx(tmp_path)
        M.record(c, [_ok("a", 7.0)])
        with c.samples.open("a") as fh:
            fh.write('{"torn"\n')
        assert M.sample_if_due(c, ["claude-cache"]) is False

    def test_nothing_to_record_writes_nothing(self, tmp_path):
        c = ctx(tmp_path)
        M.record(c, [])
        assert not c.samples.exists()

    def test_the_ring_stays_inside_its_bound_keeping_the_newest(self, tmp_path):
        c = ctx(tmp_path, samples_max_bytes=2000)
        for n in range(60):
            M.record(c, [_ok("a", float(n))])
        assert c.samples.stat().st_size <= 2000
        assert _rows(c)[-1]["value"] == 59.0

    def test_a_row_over_half_the_bound_still_survives_its_own_trim(self, tmp_path):
        c = ctx(tmp_path, samples_max_bytes=300)
        M.record(c, [_ok("a", 1.0), _ok("b", 2.0)])
        assert [r["value"] for r in _rows(c)] == [2.0]

    def test_the_append_and_the_trim_both_happen_inside_the_lock(self, tmp_path, monkeypatch):
        c = ctx(tmp_path, samples_max_bytes=300)
        inside = []
        seen = []
        real_lock, real_trim = M.locked, M._trim
        real_open, real_fdopen = M.os.open, M.os.fdopen

        @contextlib.contextmanager
        def spy(path):
            with real_lock(path):
                inside.append(path)
                yield
                inside.pop()

        class Spied:
            """The real file, noting who holds the lock when the bytes go in. The open is noted too:
            an fd opened before the lock can point at the file a trim then replaces."""

            def __init__(self, fh):
                self.fh = fh

            def __enter__(self):
                self.fh.__enter__()
                return self

            def __exit__(self, *exc):
                return self.fh.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(self.fh, name)

            def write(self, data):
                seen.append(("append", list(inside)))
                return self.fh.write(data)

        def spy_fdopen(*a, **k):
            return Spied(real_fdopen(*a, **k))

        def spy_open(path, *a, **k):
            if path == c.samples:  # the call that binds the descriptor to today's inode
                seen.append(("open", list(inside)))
            return real_open(path, *a, **k)

        def spy_trim(cx):
            seen.append(("trim", list(inside)))
            real_trim(cx)

        monkeypatch.setattr(M, "locked", spy)
        monkeypatch.setattr(M.os, "open", spy_open)
        monkeypatch.setattr(M.os, "fdopen", spy_fdopen)
        monkeypatch.setattr(M, "_trim", spy_trim)
        M.record(c, [_ok("a", 1.0), _ok("b", 2.0)])
        assert {kind for kind, _held in seen} == {"open", "append", "trim"}
        assert all(held == [c.samples] for _kind, held in seen)

    def test_a_sample_landing_during_the_read_wins_the_interval(self, tmp_path, monkeypatch):
        c = ctx(tmp_path)

        def slow(cx):
            M.record(cx, [_ok("other", 2.0)])  # another process, while this one reads
            return [_ok("mine", 1.0)]

        monkeypatch.setitem(M.READERS.table, "slow", slow)
        assert M.sample_if_due(c, ["slow"]) is False
        assert [r["source"] for r in _rows(c)] == ["other"]

    def test_a_row_past_the_tail_bound_reads_as_no_last_row(self, tmp_path):
        c = ctx(tmp_path, sample_tail_bytes=8)
        M.record(c, [_ok("a", 7.0)])
        assert M.sample_if_due(c, ["claude-cache"]) is True

    def test_sample_if_due_throttles(self, tmp_path):
        c = ctx(tmp_path)
        assert M.sample_if_due(c, ["claude-cache"]) is True
        assert M.sample_if_due(c, ["claude-cache"]) is False
        later = ctx(tmp_path, now=NOW + 301)
        assert M.sample_if_due(later, ["claude-cache"]) is True


class TestConfig:
    @pytest.mark.parametrize("key", ["token_fields", "token_windows"])
    def test_a_repeated_entry_is_refused_it_would_count_twice(self, tmp_path, key):
        value = "input" if key == "token_fields" else "5h"
        (tmp_path / ".ddflow").mkdir()
        (tmp_path / ".ddflow" / "config.toml").write_text(
            f'[quota]\n{key} = ["{value}", "{value}"]\n'
        )
        with pytest.raises(ValueError, match=f"quota.{key}"):
            Config.load(tmp_path)

    def test_defaults_and_context(self):
        cfg = Config()
        c = MB.context(cfg, now=NOW)
        assert c.stale_after_s == cfg.quota.stale_after_s > 0
        assert set(c.windows) == set(cfg.quota.token_windows)
        assert c.windows["5h"] == NOW - 5 * HOUR
        assert str(c.claude_projects).endswith(".claude/projects")

    def test_state_files_follow_xdg_and_their_knobs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        c = MB.context(Config(), now=NOW)
        assert c.samples.parent == tmp_path / "ddflow"
        assert c.snapshot.parent == tmp_path / "ddflow"
        cfg = Config()
        cfg.quota.samples_path = str(tmp_path / "x.jsonl")
        assert MB.context(cfg, now=NOW).samples == tmp_path / "x.jsonl"

    def test_the_harness_paths_come_from_the_data_file_until_a_knob_sets_one(self, tmp_path):
        data = MB.sources()
        c = MB.context(Config(), now=NOW)
        assert c.claude_state == Path(data["vendor"]["state"]).expanduser()
        assert c.kilo_db == Path(data["store"]["path"]).expanduser()
        cfg = Config()
        cfg.quota.kilo_db = str(tmp_path / "k.db")
        assert MB.context(cfg, now=NOW).kilo_db == tmp_path / "k.db"

    def test_the_tail_bound_is_a_knob(self):
        cfg = Config()
        cfg.quota.sample_tail_bytes = 64
        assert MB.context(cfg, now=NOW).sample_tail_bytes == 64

    def test_total_has_no_since_it_reads_from_the_start(self):
        cfg = Config()
        cfg.quota.token_windows = ["total"]
        assert MB.context(cfg, now=NOW).windows == {"total": 0.0}
