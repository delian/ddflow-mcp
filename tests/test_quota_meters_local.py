"""Quota meters, the local counters: transcripts, kilo.db and the ddflow log."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from quota_meters_support import (
    HOUR,
    NOW,
    ctx,
    iso,
    kilo_db,
    kilo_tokens,
    none_of,
    of,
    turn,
    write_transcript,
)

from ddflow.core.events import Event
from ddflow.infra.store._schema import ReadOnlyError, read_only_rows
from ddflow.services import meters_base as MB
from ddflow.services import meters_local as L


def _tokens(c, window="5h"):
    return of(L.read_claude_tokens(c), window)


class TestTranscriptUsage:
    def test_sums_all_four_fields_with_a_breakdown(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn()])
        got = _tokens(c)
        assert (got.status, got.value, got.unit, got.unmetered) == (MB.OK, 116.0, "tokens", 0)
        assert got.detail["input"] == 10 and got.detail["cache_read"] == 100

    def test_token_fields_choose_what_counts(self, tmp_path):
        c = ctx(tmp_path, token_fields=("input", "output"))
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn()])
        assert _tokens(c).value == 15.0

    def test_dedupes_on_message_and_request_inside_and_across_files(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(), turn(), turn(rid="r2")])
        write_transcript(c.claude_projects / "p" / "s" / "subagents" / "b.jsonl", [turn()])
        assert _tokens(c).value == 232.0

    def test_subagent_turns_count(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn()])
        sub = c.claude_projects / "p" / "s" / "subagents" / "b.jsonl"
        write_transcript(sub, [turn(mid="m2")])
        assert _tokens(c).value == 232.0

    def test_a_repeat_keeps_the_largest_figures(self, tmp_path):
        c = ctx(tmp_path, token_fields=("output",))
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(out=1), turn(out=9)])
        assert _tokens(c).value == 9.0

    def test_a_turn_seen_on_both_sides_of_a_window_start_stays_in_it(self, tmp_path):
        start = NOW - 5 * HOUR
        c = ctx(tmp_path, windows={"5h": start, "week": NOW - 168 * HOUR}, token_fields=("input",))
        rows = [turn(at=start - 10, inp=1), turn(at=start + 5, inp=500)]
        write_transcript(c.claude_projects / "p" / "a.jsonl", rows)
        assert _tokens(c).value == 500.0

    @pytest.mark.parametrize("first", [-10, 5])
    def test_a_turn_with_no_figure_seen_twice_counts_at_its_latest_time(self, tmp_path, first):
        start = NOW - 5 * HOUR
        c = ctx(tmp_path, windows={"5h": start, "week": NOW - 168 * HOUR})
        rows = []
        for at in sorted((start + first, start + 5)):
            row = turn(at=at)
            del row["message"]["usage"]
            rows.append(row)
        write_transcript(c.claude_projects / "p" / "a.jsonl", rows)
        got = _tokens(c)
        assert got.status == MB.UNAVAILABLE and got.unmetered == 1

    def test_a_turn_with_no_figure_and_then_no_time_counts_in_every_window(self, tmp_path):
        c = ctx(tmp_path)
        late, lost = turn(at=NOW - 6 * HOUR), turn()
        del late["message"]["usage"], lost["message"]["usage"]
        lost["timestamp"] = "yesterday"
        write_transcript(c.claude_projects / "p" / "a.jsonl", [late, lost])
        assert _tokens(c).status == MB.UNAVAILABLE

    def test_a_later_unusable_line_moves_the_turn_into_the_window(self, tmp_path):
        start = NOW - 5 * HOUR
        c = ctx(tmp_path, windows={"5h": start, "week": NOW - 168 * HOUR}, token_fields=("output",))
        late = turn(at=start + 5)
        late["message"]["usage"] = {"input_tokens": None, "output_tokens": 5}
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(at=start - 10), late])
        assert (_tokens(c).status, _tokens(c).value) == (MB.OK, 5.0)

    def test_rows_without_a_key_are_each_counted(self, tmp_path):
        c = ctx(tmp_path, token_fields=("output",))
        row = turn()
        del row["requestId"]
        write_transcript(c.claude_projects / "p" / "a.jsonl", [row, row])
        assert _tokens(c).value == 10.0

    def test_each_window_counts_its_own_rows_in_one_pass(self, tmp_path):
        c = ctx(tmp_path, token_fields=("output",))
        rows = [turn(mid="old", at=NOW - 6 * HOUR), turn(mid="new", at=NOW - 60)]
        write_transcript(c.claude_projects / "p" / "a.jsonl", rows)
        got = L.read_claude_tokens(c)
        assert of(got, "5h").value == 5.0 and of(got, "week").value == 10.0

    def test_a_file_untouched_since_the_window_is_not_opened(self, tmp_path, monkeypatch):
        c = ctx(tmp_path, windows={"5h": NOW - 5 * HOUR})
        old = c.claude_projects / "p" / "old.jsonl"
        write_transcript(old, [turn(at=NOW - 6 * HOUR)], mtime=NOW - 6 * HOUR)
        os.chmod(old, 0)  # would fail the read if it were opened
        write_transcript(c.claude_projects / "p" / "new.jsonl", [turn(mid="m2")])
        assert _tokens(c).value == 116.0

    def test_turns_without_usage_are_unmetered_not_zero(self, tmp_path):
        c = ctx(tmp_path)
        bare = turn(mid="m2")
        del bare["message"]["usage"]
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(), bare])
        got = _tokens(c)
        assert (got.status, got.value, got.unmetered) == (MB.OK, 116.0, 1)

    @pytest.mark.parametrize(
        "bad",
        [{"input_tokens": "x", "output_tokens": 1}, {"input_tokens": -1, "output_tokens": 1}, {}],
    )
    def test_only_unusable_usage_is_unavailable(self, tmp_path, bad):
        c = ctx(tmp_path)
        row = turn()
        row["message"]["usage"] = bad
        write_transcript(c.claude_projects / "p" / "a.jsonl", [row])
        got = _tokens(c)
        assert got.status == MB.UNAVAILABLE and got.value is None and "1" in got.reason

    def test_a_turn_with_no_timestamp_is_unmetered_in_every_window(self, tmp_path):
        c = ctx(tmp_path)
        row = turn(mid="m2")
        row["timestamp"] = "yesterday"
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(), row])
        got = L.read_claude_tokens(c)
        assert of(got, "5h").unmetered == 1 and of(got, "week").unmetered == 1

    def test_a_torn_last_line_is_unmetered(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(), '{"type":"assistant","me'])
        got = _tokens(c)
        assert (got.value, got.unmetered) == (116.0, 1)

    def test_only_a_torn_line_is_unavailable_never_zero(self, tmp_path):
        c = ctx(tmp_path)
        torn = '{"type":"assistant","message":{"usage":{"input_tokens":5'
        write_transcript(c.claude_projects / "p" / "a.jsonl", [torn])
        got = _tokens(c)
        assert got.status == MB.UNAVAILABLE and got.value is None and got.unmetered == 1

    @pytest.mark.parametrize("torn", ['{"type":"ass', '{"typ', "{"])
    def test_a_last_line_torn_before_its_type_is_never_a_zero(self, tmp_path, torn):
        c = ctx(tmp_path)
        path = write_transcript(c.claude_projects / "p" / "a.jsonl", [])
        path.write_text(torn)  # no newline: the writer was stopped mid-line
        os.utime(path, (NOW, NOW))
        got = _tokens(c)
        assert got.status == MB.UNAVAILABLE and got.unmetered == 1

    def test_a_torn_line_ending_in_a_newline_is_never_a_zero(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", ['{"type":"ass'])
        got = _tokens(c)
        assert got.status == MB.UNAVAILABLE and got.unmetered == 1

    def test_a_blank_line_is_nothing(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", ["", turn(), "  "])
        assert (_tokens(c).value, _tokens(c).unmetered) == (116.0, 0)

    def test_a_finished_last_line_of_another_kind_is_nothing(self, tmp_path):
        c = ctx(tmp_path)
        path = write_transcript(c.claude_projects / "p" / "a.jsonl", [])
        path.write_text('{"type":"user"}')  # no newline, but whole
        os.utime(path, (NOW, NOW))
        assert (_tokens(c).status, _tokens(c).value) == (MB.OK, 0.0)

    def test_a_kind_the_turn_does_not_expose_is_never_a_zero(self, tmp_path):
        c = ctx(tmp_path, token_fields=("cache_read",))
        row = turn()
        del row["message"]["usage"]["cache_read_input_tokens"]
        write_transcript(c.claude_projects / "p" / "a.jsonl", [row])
        assert _tokens(c).status == MB.UNAVAILABLE

    def test_a_kind_missing_from_one_turn_makes_the_total_a_lower_bound(self, tmp_path):
        c = ctx(tmp_path)
        row = turn(mid="m2")
        del row["message"]["usage"]["cache_read_input_tokens"]
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(), row])
        got = _tokens(c)
        assert (got.status, got.value, got.unmetered) == (MB.OK, 132.0, 1)

    def test_an_absurd_count_is_unmetered_not_a_crash(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(), turn(mid="m2", inp=10**400)])
        assert _tokens(c).unmetered == 1

    def test_no_directory_is_unavailable(self, tmp_path):
        assert none_of(L.read_claude_tokens(ctx(tmp_path)))

    def test_a_directory_with_no_transcripts_is_unavailable(self, tmp_path):
        c = ctx(tmp_path)
        (c.claude_projects / "p").mkdir(parents=True)
        assert _tokens(c).status == MB.UNAVAILABLE

    def test_transcripts_with_nothing_in_the_window_measure_zero(self, tmp_path):
        c = ctx(tmp_path)
        old = NOW - 200 * HOUR
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(at=old)], mtime=old)
        got = _tokens(c)
        assert (got.status, got.value) == (MB.OK, 0.0)

    def test_a_transcript_gone_mid_scan_is_unavailable_not_free(self, tmp_path, monkeypatch):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn()])

        def gone(*_a):
            raise FileNotFoundError("deleted")

        monkeypatch.setattr(L, "_scan", gone)
        assert none_of(L.read_claude_tokens(c))

    def test_a_recent_transcript_gone_at_stat_is_unavailable_beside_an_old_one(
        self, tmp_path, monkeypatch
    ):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "old.jsonl", [turn()], mtime=NOW - 200 * HOUR)
        gone = write_transcript(c.claude_projects / "p" / "new.jsonl", [turn(mid="m2")])
        real = type(gone).stat

        def stat(self, **kw):
            if self == gone:
                raise FileNotFoundError("deleted")
            return real(self, **kw)

        monkeypatch.setattr(type(gone), "stat", stat)
        assert none_of(L.read_claude_tokens(c))

    def test_the_only_transcript_gone_at_stat_is_unavailable(self, tmp_path, monkeypatch):
        c = ctx(tmp_path)
        path = write_transcript(c.claude_projects / "p" / "a.jsonl", [turn()])
        real = type(path).stat

        def gone(self, **kw):
            if self.suffix == ".jsonl":
                raise FileNotFoundError("deleted")
            return real(self, **kw)

        monkeypatch.setattr(type(path), "stat", gone)
        assert none_of(L.read_claude_tokens(c))

    def test_over_the_byte_cap_is_unavailable_not_a_partial_sum(self, tmp_path):
        c = ctx(tmp_path, transcript_max_bytes=10)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn()])
        got = _tokens(c)
        assert got.status == MB.UNAVAILABLE and "transcript_max_bytes" in got.reason

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
    def test_an_unreadable_file_is_unavailable_not_an_undercount(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn()])
        bad = write_transcript(c.claude_projects / "p" / "b.jsonl", [turn(mid="m2")])
        os.chmod(bad, 0)
        assert _tokens(c).status == MB.UNAVAILABLE

    def test_one_function_is_the_swap_point(self, tmp_path):
        c = ctx(tmp_path)
        write_transcript(c.claude_projects / "p" / "a.jsonl", [turn(), turn()])
        got = L.transcript_usage(c.claude_projects, 0.0, 1 << 20)
        assert len(got.rows) == 1 and got.rows[0].input == 10 and got.rows[0].model == "m"


#: A row that reports every kind, so no gap of its own: 4 tokens.
FULL = kilo_tokens(1, 1, 0, 1, 1)


def _kilo(c, unit, window="5h"):
    return of(L.read_kilo(c), window, unit)


class TestKilo:
    def test_counts_message_rows_not_the_session_lifetime(self, tmp_path):
        c = ctx(tmp_path)
        rows = [
            {"at": NOW - 60, "tokens": kilo_tokens(10, 5, 2, 100, 1)},
            {"at": NOW - 6 * HOUR, "tokens": kilo_tokens(1000, 0)},
        ]
        kilo_db(c.kilo_db, rows)
        got = _kilo(c, "tokens")
        assert (got.status, got.value) == (MB.OK, 118.0)
        assert got.detail["output"] == 7  # reasoning counts as output
        assert _kilo(c, "tokens", "week").value == 1118.0

    def test_a_user_message_is_not_a_turn(self, tmp_path):
        c = ctx(tmp_path)
        rows = [
            {"at": NOW - 60, "tokens": kilo_tokens(10)},
            {"at": NOW - 60, "role": "user", "tokens": kilo_tokens(99)},
        ]
        kilo_db(c.kilo_db, rows)
        assert _kilo(c, "tokens").value == 10.0

    def test_a_zero_row_is_unmetered_not_counted(self, tmp_path):
        c = ctx(tmp_path)
        rows = [{"at": NOW - 60, "tokens": FULL}, {"at": NOW - 60, "tokens": kilo_tokens()}]
        kilo_db(c.kilo_db, rows)
        got = _kilo(c, "tokens")
        assert (got.value, got.unmetered) == (4.0, 1)

    @pytest.mark.parametrize("row", [{"tokens": kilo_tokens()}, {}])
    def test_only_zero_or_missing_tokens_is_unavailable_never_zero(self, tmp_path, row):
        c = ctx(tmp_path)
        kilo_db(c.kilo_db, [{"at": NOW - 60, **row}])
        got = _kilo(c, "tokens")
        assert got.status == MB.UNAVAILABLE and got.value is None and got.unmetered == 1

    def test_a_kind_the_row_does_not_hold_is_never_a_zero(self, tmp_path):
        c = ctx(tmp_path, token_fields=("cache_read",))
        kilo_db(c.kilo_db, [{"at": NOW - 60, "tokens": {"input": 100, "output": 50}}])
        assert _kilo(c, "tokens").status == MB.UNAVAILABLE

    def test_a_kind_missing_from_one_row_makes_the_total_a_lower_bound(self, tmp_path):
        c = ctx(tmp_path)
        rows = [
            {"at": NOW - 60, "tokens": FULL},
            {"at": NOW - 60, "tokens": {"input": 100, "output": 50}},
        ]
        kilo_db(c.kilo_db, rows)
        got = _kilo(c, "tokens")
        assert (got.status, got.value, got.unmetered) == (MB.OK, 154.0, 1)

    def test_a_zero_beside_a_figure_is_not_a_measured_zero(self, tmp_path):
        c = ctx(tmp_path, token_fields=("output",))
        kilo_db(c.kilo_db, [{"at": NOW - 60, "tokens": kilo_tokens(100)}])
        assert _kilo(c, "tokens").status == MB.UNAVAILABLE

    def test_a_zero_kind_makes_the_default_total_a_lower_bound(self, tmp_path):
        c = ctx(tmp_path)
        kilo_db(c.kilo_db, [{"at": NOW - 60, "tokens": kilo_tokens(100)}])
        got = _kilo(c, "tokens")
        assert (got.status, got.value, got.unmetered) == (MB.OK, 100.0, 1)

    def test_a_missing_output_beside_a_zero_reasoning_is_not_a_zero(self, tmp_path):
        c = ctx(tmp_path, token_fields=("output",))
        kilo_db(c.kilo_db, [{"at": NOW - 60, "tokens": {"input": 10, "reasoning": 0}}])
        assert _kilo(c, "tokens").status == MB.UNAVAILABLE

    def test_no_turn_in_the_window_measures_zero_tokens(self, tmp_path):
        c = ctx(tmp_path)
        kilo_db(c.kilo_db, [])
        assert _kilo(c, "tokens").value == 0.0

    def test_zero_cost_is_unavailable_never_a_free_session(self, tmp_path):
        c = ctx(tmp_path)
        kilo_db(c.kilo_db, [{"at": NOW - 60, "tokens": kilo_tokens(10), "cost": 0}])
        got = _kilo(c, "usd")
        assert got.status == MB.UNAVAILABLE and got.value is None and "cost" in got.reason

    def test_no_turns_gives_no_cost_either(self, tmp_path):
        c = ctx(tmp_path)
        kilo_db(c.kilo_db, [])
        assert _kilo(c, "usd").status == MB.UNAVAILABLE

    def test_reported_cost_is_summed_and_gaps_are_named(self, tmp_path):
        c = ctx(tmp_path)
        rows = [
            {"at": NOW - 60, "tokens": kilo_tokens(1), "cost": 0.25},
            {"at": NOW - 60, "tokens": kilo_tokens(1), "cost": 0},
        ]
        kilo_db(c.kilo_db, rows)
        got = _kilo(c, "usd")
        assert (got.status, got.value, got.unmetered) == (MB.OK, 0.25, 1)

    def test_missing_store_is_unavailable(self, tmp_path):
        got = L.read_kilo(ctx(tmp_path))
        assert {(r.window, r.unit) for r in got} == {
            ("5h", "tokens"),
            ("week", "tokens"),
            ("5h", "usd"),
            ("week", "usd"),
        }
        assert none_of(got)

    def test_not_a_database_is_unavailable(self, tmp_path):
        c = ctx(tmp_path)
        c.kilo_db.write_text("plain text, not sqlite")
        assert none_of(L.read_kilo(c))

    def test_a_changed_schema_is_unavailable(self, tmp_path):
        c = ctx(tmp_path)
        con = sqlite3.connect(c.kilo_db)
        con.execute("CREATE TABLE other (x)")
        con.commit()
        con.close()
        assert none_of(L.read_kilo(c))

    def test_a_query_over_its_time_budget_is_unavailable(self, tmp_path):
        c = ctx(tmp_path, kilo_timeout_s=0.0)
        kilo_db(c.kilo_db, [{"at": NOW - 60 - n, "tokens": kilo_tokens(1)} for n in range(500)])
        got = _kilo(c, "tokens")
        assert got.status == MB.UNAVAILABLE and "budget" in got.reason

    def test_a_path_with_uri_characters_still_opens(self, tmp_path):
        odd = tmp_path / "a b?c#d"
        odd.mkdir()
        c = ctx(tmp_path, kilo_db=odd / "kilo.db")
        kilo_db(c.kilo_db, [{"at": NOW - 60, "tokens": kilo_tokens(3)}])
        assert _kilo(c, "tokens").value == 3.0

    def test_the_store_is_opened_read_only(self, tmp_path):
        c = ctx(tmp_path)
        kilo_db(c.kilo_db, [{"at": NOW - 60, "tokens": kilo_tokens(1)}])
        with pytest.raises(ReadOnlyError, match="readonly"):
            read_only_rows(c.kilo_db, "DELETE FROM message", timeout_s=5.0)
        assert _kilo(c, "tokens").value == 1.0

    def test_a_path_that_cannot_resolve_is_a_read_failure(self, tmp_path, monkeypatch):
        def loop(*_a, **_k):
            raise RuntimeError("Symlink loop")

        monkeypatch.setattr(type(tmp_path), "resolve", loop)
        with pytest.raises(ReadOnlyError, match="loop"):
            read_only_rows(tmp_path / "x.db", "SELECT 1", timeout_s=5.0)

    def test_reading_never_creates_a_store(self, tmp_path):
        missing = tmp_path / "none.db"
        with pytest.raises(ReadOnlyError):
            read_only_rows(missing, "SELECT 1", timeout_s=5.0)
        assert not missing.exists()


def _started(at, tool="claude-code"):
    return Event("session.started", "s", {"tool": tool}, ts=iso(at))


class TestSessions:
    def test_counts_per_window_with_a_project_scope(self, tmp_path):
        ev = [_started(NOW - 60), _started(NOW - 6 * HOUR, "kilo"), Event("x", "s", ts=iso(NOW))]
        got = L.read_ddflow_sessions(ctx(tmp_path, events=ev))
        five, week = of(got, "5h"), of(got, "week")
        assert (five.status, five.value, five.unit) == (MB.OK, 1.0, "sessions")
        assert week.value == 2.0 and week.detail["scope"] == "project"
        assert week.detail["by_tool"] == {"claude-code": 1, "kilo": 1}

    def test_no_log_handed_in_is_unavailable(self, tmp_path):
        assert none_of(L.read_ddflow_sessions(ctx(tmp_path)))

    def test_a_start_with_no_usable_time_is_never_a_zero(self, tmp_path):
        ev = [Event("session.started", "s", {"tool": "x"}, ts="garbage")]
        got = of(L.read_ddflow_sessions(ctx(tmp_path, events=ev)), "5h")
        assert got.status == MB.UNAVAILABLE and got.unmetered == 1

    def test_a_start_with_no_usable_time_makes_the_count_a_lower_bound(self, tmp_path):
        ev = [_started(NOW - 60), Event("session.started", "s", {}, ts="garbage")]
        got = of(L.read_ddflow_sessions(ctx(tmp_path, events=ev)), "5h")
        assert (got.value, got.unmetered) == (1.0, 1)

    def test_a_log_with_no_session_measures_zero(self, tmp_path):
        got = L.read_ddflow_sessions(ctx(tmp_path, events=[]))
        assert of(got, "5h").value == 0.0


def test_readings_serialise(tmp_path):
    c = ctx(tmp_path)
    write_transcript(c.claude_projects / "p" / "a.jsonl", [turn()])
    assert json.loads(json.dumps(_tokens(c).to_dict()))["value"] == 116.0
