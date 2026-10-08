"""Aliases and deprecations (D-compat): an old command, flag, MCP tool, argument or config key
keeps working with a one-line notice until 1.0.

Each alias works, notices ONCE per session, is hidden from listings, and an unknown name gets
a 'did you mean'. The parity test sees an alias as its canonical command: a hidden alias is
not a command of its own.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from dataclasses import fields

import pytest

from ddflow.config import RENAMED, Config
from ddflow.config_sections._compat import check_rename
from ddflow.config_sections._docs import knob
from ddflow.core.outcome import Outcome
from ddflow.services.configwrite import _write_config
from ddflow.surfaces import cli
from ddflow.surfaces import registry as R
from ddflow.surfaces.mcp import TOOLS, Server
from ddflow.surfaces.registry import Alias, Command, Param, SuggestingParser, add_commands

SINCE = "0.1.17"


def _parser(*cmds: Command, group_aliases=None) -> SuggestingParser:
    root = SuggestingParser(prog="ddflow")
    add_commands(
        root.add_subparsers(dest="cmd", required=True),
        list(cmds),
        group_aliases=group_aliases,
    )
    return root


def _show() -> Command:
    return Command(
        path=("show",),
        summary="show it",
        params=(
            Param("item", positional=True),
            Param("note", aliases=("comment",), deprecated_since=SINCE),
        ),
        handler=lambda a, c: 0,
        aliases=("display",),
        deprecated_since=SINCE,
    )


# -- the declaration ---------------------------------------------------------------------


def test_an_alias_cannot_be_removed_before_one_point_zero():
    with pytest.raises(ValueError, match=r"before 1\.0"):
        Alias("command", "old", "new", SINCE, removed_in="0.9")
    with pytest.raises(ValueError, match=r"before 1\.0"):
        Command(path=("x",), aliases=("y",), deprecated_since=SINCE, removed_in="0.5.2")
    Alias("command", "old", "new", SINCE, removed_in="1.0.0")  # 1.0 spelled long
    Alias("command", "old", "new", SINCE, removed_in="2.1")


def test_an_alias_names_the_release_that_renamed_it():
    with pytest.raises(ValueError, match="deprecated_since"):
        Command(path=("x",), aliases=("y",))
    with pytest.raises(ValueError, match="deprecated_since"):
        Param("a", aliases=("b",))
    with pytest.raises(ValueError, match="not a version"):
        Alias("tool", "old", "new", "soon")


def test_an_alias_differs_from_its_name_and_is_unique():
    with pytest.raises(ValueError):
        Param("a", aliases=("a",), deprecated_since=SINCE)
    with pytest.raises(ValueError):
        Param("a", aliases=("b", "b"), deprecated_since=SINCE)
    with pytest.raises(ValueError):
        Command(path=("x",), aliases=("x",), deprecated_since=SINCE)
    with pytest.raises(ValueError, match="CLI path"):
        Command(path=(), tool="t", aliases=("x",), deprecated_since=SINCE)


def test_the_notice_names_the_old_the_new_and_the_removal():
    line = Alias("flag", "--old", "--new", SINCE).notice()
    assert "'--old'" in line and "'--new'" in line and SINCE in line and "1.0" in line
    assert "\n" not in line


def test_notices_come_once_each():
    n = R.Notices()
    a, b = Alias("tool", "a", "b", SINCE), Alias("tool", "c", "d", SINCE)
    assert n.fresh([a]) == [a]
    assert n.fresh([a, b]) == [b]
    assert n.fresh([a, b]) == []


# -- the CLI -----------------------------------------------------------------------------


def test_a_command_alias_runs_the_command_and_is_hidden(capsys):
    root = _parser(_show())
    ns = root.parse_args(["display", "I1"])
    assert ns.item == "I1" and ns.fn is root.parse_args(["show", "I1"]).fn
    (sub,) = [a for a in root._actions if isinstance(a, argparse._SubParsersAction)]
    assert list(sub.choices) == ["show"] and "display" not in dict(sub.choices)
    assert "display" in sub.choices and sub.choices["display"] is sub.choices["show"]
    root.print_help()
    out = capsys.readouterr().out
    assert "display" not in out and "show" in out


def test_a_flag_alias_works_and_is_hidden(capsys):
    root = _parser(_show())
    assert root.parse_args(["show", "I1", "--comment", "hi"]).note == "hi"
    assert root.parse_args(["show", "I1", "--comment=hi"]).note == "hi"
    with pytest.raises(SystemExit):
        root.parse_args(["show", "--help"])
    assert "comment" not in capsys.readouterr().out


def test_a_required_flag_may_be_given_by_its_alias():
    cmd = Command(
        path=("pick",),
        params=(Param("what", required=True, aliases=("which",), deprecated_since=SINCE),),
        handler=lambda a, c: 0,
    )
    assert _parser(cmd).parse_args(["pick", "--which", "x"]).what == "x"


def test_used_aliases_reports_the_command_and_flag_typed():
    root = _parser(_show())
    typed = ["display", "I1", "--comment", "hi"]
    got = R.used_aliases(root, root.parse_args(typed), typed)
    assert [(a.kind, a.old, a.new) for a in got] == [
        ("command", "display", "show"),
        ("flag", "--comment", "--note"),
    ]
    plain = ["show", "I1", "--note", "hi"]
    assert R.used_aliases(root, root.parse_args(plain), plain) == []


def test_a_flag_after_double_dash_is_a_value_not_an_alias():
    root = _parser(_show())
    typed = ["show", "--", "--comment"]
    assert R.used_aliases(root, root.parse_args(typed), typed) == []


def test_a_renamed_group_keeps_working_under_its_old_name():
    docs_add = Command(
        path=("docs", "add"),
        params=(Param("name"),),
        handler=lambda a, c: 0,
        aliases=("append",),
        deprecated_since=SINCE,
    )
    root = _parser(docs_add, group_aliases={"docs": (Alias("command", "doc", "docs", SINCE),)})
    for typed in (["docs", "add"], ["doc", "add"], ["doc", "append"], ["docs", "append"]):
        assert root.parse_args(typed).fn is docs_add.handler
    (sub,) = [a for a in root._actions if isinstance(a, argparse._SubParsersAction)]
    assert list(sub.choices) == ["docs"]
    got = R.used_aliases(root, root.parse_args(["doc", "append"]), ["doc", "append"])
    assert [a.old for a in got] == ["doc", "docs append"]
    with pytest.raises(ValueError, match="no command declares"):
        _parser(docs_add, group_aliases={"nope": (Alias("command", "x", "nope", SINCE),)})


def test_an_unknown_command_gets_a_did_you_mean(capsys):
    root = _parser(_show())
    with pytest.raises(SystemExit) as exit_:
        root.parse_args(["shw"])
    assert exit_.value.code == 2
    assert "Did you mean 'show'?" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        root.parse_args(["zzzzzz"])
    assert "Did you mean" not in capsys.readouterr().err


def test_main_says_so_once_on_stderr(monkeypatch, capsys):
    ran = []
    cmd = Command(
        path=("show",),
        params=(Param("item", positional=True),),
        handler=lambda a, c: ran.append(a.item) or 0,
        aliases=("display",),
        deprecated_since=SINCE,
    )
    monkeypatch.setattr(cli, "build_parser", lambda: _parser(cmd))
    monkeypatch.setattr(cli, "Ctx", lambda a: None)
    assert cli.main(["display", "I1"]) == 0
    err = capsys.readouterr().err
    assert ran == ["I1"] and err.count("deprecated") == 1 and "'display'" in err
    assert cli.main(["show", "I1"]) == 0
    assert "deprecated" not in capsys.readouterr().err


def test_the_real_parser_lists_no_alias_and_parity_sees_only_canonical_commands():
    """No command of the real CLI is an alias today; the walks the parity test makes
    (`sorted(choices)`, `choices.items()`) see the canonical names only, even where a hidden
    alias is registered."""
    root = _parser(_show())
    (sub,) = [a for a in root._actions if isinstance(a, argparse._SubParsersAction)]
    assert sorted(sub.choices) == [name for name, _ in sub.choices.items()] == ["show"]
    assert len(sub.choices) == 1
    assert list(cli.build_parser()._subparsers._group_actions[0].choices)


# -- MCP ---------------------------------------------------------------------------------


def _install(monkeypatch, **kw) -> Command:
    cmd = Command(
        path=("probe",),
        tool="ddflow_probe",
        description="probe",
        params=(
            Param("item", required=True, help="the item", aliases=("id",), deprecated_since=SINCE),
            Param("note", help="a note"),
        ),
        call=lambda repo, args, agent: Outcome("probe", {"got": dict(args)}),
        tool_aliases=("ddflow_peek",),
        deprecated_since=SINCE,
        **kw,
    )
    monkeypatch.setitem(TOOLS, "ddflow_probe", cmd.tool_entry())
    return cmd


def _call(server: Server, name: str, args: dict) -> dict:
    reply = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    return reply["result"]


def _texts(result: dict) -> list[str]:
    return [c["text"] for c in result["content"]]


def test_an_old_tool_name_works_and_is_not_listed(tmp_path, monkeypatch):
    _install(monkeypatch)
    server = Server(tmp_path)
    listed = {
        t["name"]
        for t in server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"][
            "tools"
        ]
    }
    assert "ddflow_probe" in listed and "ddflow_peek" not in listed
    result = _call(server, "ddflow_peek", {"item": "I1"})
    assert not result.get("isError")
    assert json.loads(_texts(result)[0])["got"] == {"item": "I1"}


def test_the_tool_notice_is_said_once_per_session(tmp_path, monkeypatch):
    _install(monkeypatch)
    server = Server(tmp_path)
    first = _texts(_call(server, "ddflow_peek", {"item": "I1"}))
    assert sum("deprecated" in t and "ddflow_peek" in t for t in first) == 1
    second = _texts(_call(server, "ddflow_peek", {"item": "I1"}))
    assert not any("deprecated" in t for t in second)
    assert any(
        "deprecated" in t for t in _texts(_call(Server(tmp_path), "ddflow_peek", {"item": "I"}))
    )
    assert not any("deprecated" in t for t in _texts(_call(server, "ddflow_probe", {"item": "I1"})))


def test_an_old_argument_works_is_hidden_and_notices_once(tmp_path, monkeypatch):
    cmd = _install(monkeypatch)
    assert "id" not in cmd.input_schema()["properties"]
    assert "id" not in TOOLS["ddflow_probe"]["properties"]
    server = Server(tmp_path)
    first = _call(server, "ddflow_probe", {"id": "I9", "note": "n"})
    assert json.loads(_texts(first)[0])["got"] == {"item": "I9", "note": "n"}
    assert any("argument 'id'" in t for t in _texts(first))
    assert not any("deprecated" in t for t in _texts(_call(server, "ddflow_probe", {"id": "I9"})))


def test_both_spellings_of_an_argument_are_refused(tmp_path, monkeypatch):
    _install(monkeypatch)
    result = _call(Server(tmp_path), "ddflow_probe", {"id": "a", "item": "b"})
    assert result["isError"] and "both 'item' and its deprecated name 'id'" in _texts(result)[0]


def test_an_old_name_alone_still_satisfies_a_required_argument(tmp_path, monkeypatch):
    _install(monkeypatch)
    assert not _call(Server(tmp_path), "ddflow_probe", {"id": "x"}).get("isError")
    assert _call(Server(tmp_path), "ddflow_probe", {})["isError"]


def test_an_unknown_tool_or_argument_suggests_the_closest(tmp_path, monkeypatch):
    _install(monkeypatch)
    server = Server(tmp_path)
    bad = _call(server, "ddflow_prob", {})
    assert bad["isError"] and "Did you mean 'ddflow_probe'?" in _texts(bad)[0]
    far = _call(server, "ddflow_qqqqqqqq", {})
    assert far["isError"] and "Did you mean" not in _texts(far)[0]
    arg = _call(server, "ddflow_probe", {"item": "I1", "noet": "x"})
    assert arg["isError"] and "Did you mean 'note' (for 'noet')?" in _texts(arg)[0]
    nothing = _call(server, "ddflow_probe", {"item": "I1", "zzzzzz": "x"})
    assert nothing["isError"] and "Did you mean" not in _texts(nothing)[0]


def test_no_shipped_tool_collides_with_an_alias():
    names = set(TOOLS)
    for name, spec in TOOLS.items():
        for alias in spec.get("aliases", ()):
            assert alias.old not in names, f"{name}: alias {alias.old} is also a tool"
        for old in spec.get("arg_aliases", {}):
            assert old not in spec["properties"], f"{name}: alias {old} is also an argument"


# -- config keys -------------------------------------------------------------------------


@pytest.fixture
def renamed(monkeypatch):
    """`lease.old_ttl_s` is the earlier spelling of `lease.ttl_s`."""
    monkeypatch.setitem(RENAMED, "lease.old_ttl_s", ("lease.ttl_s", SINCE, "1.0"))
    monkeypatch.setattr("ddflow.config._WARNED_RENAMED", set())


def _config(tmp_path, text: str) -> None:
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(text)


def test_an_old_config_key_is_read_with_one_warning(tmp_path, renamed, capsys):
    _config(tmp_path, "[lease]\nold_ttl_s = 777\n")
    cfg = Config.load(tmp_path, env={})
    assert cfg.lease.ttl_s == 777 and cfg.sources["lease.ttl_s"] == "file"
    assert not cfg.unknown_knobs
    err = capsys.readouterr().err
    assert err.count("lease.old_ttl_s") == 1 and "use 'lease.ttl_s'" in err and SINCE in err
    Config.load(tmp_path, env={})
    assert "old_ttl_s" not in capsys.readouterr().err


def test_an_old_environment_variable_still_works_and_the_new_one_wins(tmp_path, renamed):
    assert Config.load(tmp_path, env={"DDFLOW_LEASE_OLD_TTL_S": "321"}).lease.ttl_s == 321
    both = {"DDFLOW_LEASE_OLD_TTL_S": "321", "DDFLOW_LEASE_TTL_S": "654"}
    assert Config.load(tmp_path, env=both).lease.ttl_s == 654


def test_when_both_keys_are_in_the_file_the_new_one_wins(tmp_path, renamed):
    _config(tmp_path, "[lease]\nold_ttl_s = 1\nttl_s = 2\n")
    assert Config.load(tmp_path, env={}).lease.ttl_s == 2


def test_an_old_config_section_is_not_called_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(RENAMED, "oldsec.ttl", ("lease.ttl_s", SINCE, "1.0"))
    monkeypatch.setattr("ddflow.config._WARNED_RENAMED", set())
    _config(tmp_path, "[oldsec]\nttl = 99\n")
    cfg = Config.load(tmp_path, env={})
    assert cfg.lease.ttl_s == 99 and not cfg.unknown_knobs
    Config.check({"oldsec": {"ttl": 5}})  # a write being validated accepts it too


def test_the_next_write_moves_the_old_key_and_keeps_the_comments(tmp_path, renamed):
    _config(
        tmp_path,
        "# my notes\n[lease]\n# how long\nold_ttl_s = 777  # seconds\n\n[review]\nmax_rounds = 3\n",
    )
    err, text = _write_config(tmp_path, [("review.max_rounds", "2")], check_workflow=False)
    assert not err
    data = tomllib.loads(text)
    assert data["lease"] == {"ttl_s": 777} and data["review"]["max_rounds"] == 2
    assert "# my notes" in text and "# how long" in text and "old_ttl_s" not in text
    assert tomllib.loads((tmp_path / ".ddflow" / "config.toml").read_text()) == data


def test_setting_an_old_key_writes_the_current_one(tmp_path, renamed):
    _config(tmp_path, "[lease]\nttl_s = 5\n")
    err, text = _write_config(tmp_path, [("lease.old_ttl_s", "900")], check_workflow=False)
    assert not err and tomllib.loads(text)["lease"] == {"ttl_s": 900}


def test_an_emptied_old_section_is_dropped_by_the_write(tmp_path, monkeypatch):
    monkeypatch.setitem(RENAMED, "oldsec.ttl", ("lease.ttl_s", SINCE, "1.0"))
    _config(tmp_path, "[oldsec]\nttl = 99\n")
    err, text = _write_config(tmp_path, [("review.max_rounds", "2")], check_workflow=False)
    assert not err and "oldsec" not in text and tomllib.loads(text)["lease"] == {"ttl_s": 99}


def test_a_knob_declares_its_old_names_with_a_release_and_a_floor():
    with pytest.raises(ValueError, match="deprecated_since"):
        knob(1, doc="d", renamed_from=("a.b",))
    with pytest.raises(ValueError, match=r"before 1\.0"):
        knob(1, doc="d", renamed_from=("a.b",), since=SINCE, removed_in="0.9")
    with pytest.raises(ValueError, match=r"section\.knob"):
        knob(1, doc="d", renamed_from=("b",), since=SINCE)
    knob(1, doc="d", renamed_from=("a.b",), since=SINCE)


def test_no_declared_old_key_is_also_a_live_knob():
    cfg = Config()
    live = {f"{s}.{f.name}" for s in cfg._sections() for f in fields(getattr(cfg, s))}
    for old, (new, since, removed_in) in RENAMED.items():
        assert old not in live and new in live
        check_rename(old, since, removed_in)
