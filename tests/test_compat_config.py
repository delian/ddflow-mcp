"""Config across versions (B-uni-compat-config, decision D-compat 2).

An older ddflow meeting a config a newer one wrote changes the keys it knows and keeps the
rest byte-identical, instead of refusing the whole file (R-upgrade-experiment); a checked
list whose set of members grows tolerates a member it does not know; a config `format = N`
newer than this code is read but never written; and the remedy a report names fits how this
ddflow is installed.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import config as C
from ddflow.config import CONFIG_FORMAT, Config, InvalidValue
from ddflow.config_sections import _compat as CV
from ddflow.config_sections._docs import knob
from ddflow.core import version as V
from ddflow.infra import tomlcfg
from ddflow.services import configwrite as CW
from ddflow.services import install_info as II

# What a NEWER release's config looks like to this one: a section and a knob it lacks, an
# enum value it lacks, a record kind it lacks, and a router shape it lacks.
NEWER = """\
[lease]
ttl_s = 900

[newsection]
x = 1

[flow]
future_knob = 3

[upgrade]
skew = "a-mode-from-the-future"

[dedupe]
kinds = ["bug", "task", "kind-from-the-future"]

[agent.routers]
hydrafusion = ["openai"]
shape-from-the-future = { members = ["a"] }
"""


def _repo(tmp_path: Path, text: str = NEWER) -> Path:
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(text)
    return tmp_path


def _set(repo: Path, key: str, value: str) -> tuple[str, str]:
    return CW._write_config(repo, [(key, value)], check_workflow=False)


# -- writes keep what they do not understand -------------------------------------------


def test_an_unrelated_set_keeps_every_newer_key_byte_identical(tmp_path):
    repo = _repo(tmp_path)
    err, new = _set(repo, "lease.ttl_s", "600")
    assert err == ""
    assert new == NEWER.replace("ttl_s = 900", "ttl_s = 600")  # nothing else moved
    assert (repo / ".ddflow" / "config.toml").read_text() == new


@pytest.mark.parametrize(
    "key, fragment",
    [
        ("flow.future_knob", "unknown knob 'flow.future_knob'"),
        ("newsection.x", "unknown config section [newsection]"),
        ("lease.typo_knob", "unknown knob 'lease.typo_knob'"),
    ],
)
def test_writing_an_unknown_key_is_still_refused_even_when_the_file_holds_it(
    tmp_path, key, fragment
):
    """The tolerance is for what the file already holds. A key being WRITTEN is a typo."""
    repo = _repo(tmp_path)
    err, _ = _set(repo, key, "1")
    assert fragment in err
    assert (repo / ".ddflow" / "config.toml").read_text() == NEWER


def test_a_value_this_code_cannot_take_is_refused_when_it_is_written(tmp_path):
    repo = _repo(tmp_path)
    err, _ = _set(repo, "upgrade.skew", "also-from-the-future")
    assert isinstance(err, CW.KeyRefused) and "upgrade.skew" in err


def test_a_newer_enum_value_in_a_key_not_written_does_not_stop_an_edit(tmp_path):
    repo = _repo(tmp_path)
    assert _set(repo, "lease.ttl_s", "700")[0] == ""
    assert 'skew = "a-mode-from-the-future"' in (repo / ".ddflow" / "config.toml").read_text()


def test_append_toml_over_a_newer_file_keeps_it(tmp_path):
    repo = _repo(tmp_path)
    err, path = CW._append_config(repo, "[review]\nmax_rounds = 3\n")
    assert err == ""
    assert path.read_text().startswith(NEWER)


def test_a_typo_in_the_appended_block_is_refused(tmp_path):
    repo = _repo(tmp_path)
    err, _ = CW._append_config(repo, "[review]\nmax_rounds_typo = 3\n")
    assert "max_rounds_typo" in err


def test_a_lone_string_for_written_is_one_key_not_its_characters():
    with pytest.raises(ValueError, match="widget"):
        Config.check({"widget": {"bad_knob": 1}}, written="widget.bad_knob")


def test_check_without_written_keeps_every_key_strict():
    with pytest.raises(ValueError, match="newsection"):
        Config.check({"newsection": {"x": 1}})
    Config.check({"newsection": {"x": 1}}, written={"lease.ttl_s"})  # passes through


def test_check_still_judges_a_known_key_of_the_wrong_type(tmp_path):
    with pytest.raises(InvalidValue, match=r"lease\.ttl_s"):
        Config.check({"lease": {"ttl_s": "ten"}}, written={"lease.heartbeat_s"})


# -- checked lists tolerate members they do not know ------------------------------------


def test_a_dedupe_kind_from_a_newer_release_is_ignored_with_a_note(tmp_path):
    cfg = Config.load(_repo(tmp_path))
    assert cfg.dedupe.kinds == ["bug", "task"]
    assert any("dedupe.kinds" in n and "kind-from-the-future" in n for n in cfg.ignored_members)


def test_a_router_in_a_shape_this_code_lacks_stays_a_router_with_unknown_families(tmp_path):
    """Dropping the entry would let its author pass as an ordinary model; an empty family
    set is "unknown", which `router_set` refuses to call independent."""
    from ddflow.config_sections.agent import router_set

    cfg = Config.load(_repo(tmp_path))
    assert cfg.agent.routers == {"hydrafusion": ["openai"], "shape-from-the-future": []}
    assert any("shape-from-the-future" in n for n in cfg.ignored_members)
    assert router_set("x-shape-from-the-future-v2", cfg.agent.routers) == []


def test_a_trailer_waiver_this_code_cannot_read_is_ignored(tmp_path):
    text = '[enforce.trailer_waivers]\nPhase-ships = ["none"]\nNew-Trailer = { words = ["a"] }\n'
    cfg = Config.load(_repo(tmp_path, text))
    assert cfg.enforce.trailer_waivers == {"Phase-ships": ["none"]}
    assert any("New-Trailer" in n for n in cfg.ignored_members)


def test_a_list_with_nothing_usable_left_keeps_the_value_below(tmp_path):
    cfg = Config.load(_repo(tmp_path, '[dedupe]\nkinds = ["only-from-the-future"]\n'))
    assert cfg.dedupe.kinds == list(C.DEDUPE_KINDS)
    assert any("nothing usable left" in n for n in cfg.ignored_members)


def test_a_table_the_filter_empties_keeps_the_layer_below(tmp_path):
    (tmp_path / ".ddflow" / "local").mkdir(parents=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(
        '[enforce.trailer_waivers]\nPhase-ships = ["none"]\n'
    )
    (tmp_path / ".ddflow" / "local" / "config.toml").write_text(
        "[enforce.trailer_waivers]\nNew-Trailer = { words = ['a'] }\n"
    )
    cfg = Config.load(tmp_path)
    assert cfg.enforce.trailer_waivers == {"Phase-ships": ["none"]}
    assert any("nothing usable left" in n for n in cfg.ignored_members)


def test_the_environment_and_the_write_paths_still_refuse_an_unknown_member(tmp_path):
    with pytest.raises(InvalidValue):
        Config.load(env={"DDFLOW_DEDUPE_KINDS": "bug,kind-from-the-future"})
    err, _ = _set(_repo(tmp_path, ""), "dedupe.kinds", '["bug", "kind-from-the-future"]')
    assert isinstance(err, CW.KeyRefused) and "dedupe.kinds" in err


def test_a_malformed_waiver_is_refused_when_written_and_only_noted_in_a_file(tmp_path):
    """The tolerance is for what a FILE holds: the same value given to `--set` is refused."""
    for bad in ('{ "Phase-ships" = "none" }', '{ "Phase-ships" = ["none", 5] }'):
        err, _ = _set(_repo(tmp_path, ""), "enforce.trailer_waivers", bad)
        assert isinstance(err, CW.KeyRefused) and "enforce.trailer_waivers" in err


def test_a_member_filter_is_declared_on_each_growing_list():
    assert {"dedupe.kinds", "agent.routers", "enforce.trailer_waivers"} <= set(C._MEMBER_FILTERS)


def test_load_says_on_stderr_which_members_were_ignored(tmp_path, capsys):
    Config.load(_repo(tmp_path))
    err = capsys.readouterr().err
    assert "kind-from-the-future" in err and "rest of each is applied" in err


# -- the config format ------------------------------------------------------------------


def test_a_newer_config_format_is_read_and_never_written(tmp_path):
    repo = _repo(tmp_path, f"format = {CONFIG_FORMAT + 1}\n\n[lease]\nttl_s = 321\n")
    cfg = Config.load(repo)
    assert cfg.lease.ttl_s == 321 and cfg.file_format == CONFIG_FORMAT + 1
    err, _ = _set(repo, "lease.ttl_s", "600")
    assert isinstance(err, CW.KeyRefused)
    assert f"format {CONFIG_FORMAT + 1}" in err and "ddflow" in err
    assert "ttl_s = 321" in (repo / ".ddflow" / "config.toml").read_text()
    assert isinstance(CW._append_config(repo, "[lease]\nttl_s = 5\n")[0], CW.KeyRefused)


def test_append_block_refuses_a_newer_format_too(tmp_path):
    repo = _repo(tmp_path, f"format = {CONFIG_FORMAT + 1}\n")
    block = '\n[[reviewer]]\nname = "r"\nmodel = "m"\n'
    with pytest.raises(CW.FormatRefused) as exc:
        CW.append_block(repo, block, shared=True)
    assert exc.value.exit_code == 3 and "format" in str(exc.value)
    assert (repo / ".ddflow" / "config.toml").read_text() == f"format = {CONFIG_FORMAT + 1}\n"


def test_a_newer_format_in_a_file_that_does_not_parse_still_refuses_a_write(tmp_path):
    repo = _repo(tmp_path, f"format = {CONFIG_FORMAT + 1}\n[gate]\nenabled = yess\n")
    err, _ = _set(repo, "lease.ttl_s", "600")
    assert isinstance(err, CW.KeyRefused) and f"format {CONFIG_FORMAT + 1}" in err


def test_format_is_not_a_section_and_the_current_one_writes(tmp_path):
    repo = _repo(tmp_path, f"format = {CONFIG_FORMAT}\n\n[lease]\nttl_s = 321\n")
    assert Config.load(repo).unknown_knobs == []
    assert _set(repo, "lease.ttl_s", "600")[0] == ""
    assert tomllib.loads((repo / ".ddflow" / "config.toml").read_text())["format"] == 1


@pytest.mark.parametrize("bad", ['"two"', "0", "true", "-1"])
def test_a_bad_format_is_noted_in_a_file_and_refused_when_checked(tmp_path, bad):
    cfg = Config.load(_repo(tmp_path, f"format = {bad}\n"))
    assert [k for k in cfg.unknown_knobs if k.startswith("format")]
    with pytest.raises(InvalidValue, match="format"):
        Config.check(tomllib.loads(f"format = {bad}\n"))


def test_the_format_bookkeeping_is_not_a_knob(tmp_path):
    cfg = Config.load(_repo(tmp_path, "format = 1\n"))
    assert "file_format" not in cfg.as_dict() and "file_format" not in cfg._sections()
    assert "ignored_members" not in cfg.as_dict()


# -- knob metadata ------------------------------------------------------------------------


def test_a_changed_default_is_declared_on_the_knob_and_shown_in_its_doc():
    field = knob(5, doc="How many.", default_changed_in="0.1.20", default_was=3)
    assert "Default changed in 0.1.20; it was 3." in field.metadata["ddflow.knob"].doc
    assert field.metadata["ddflow.knob"].default_changed_in == "0.1.20"


def test_a_changed_default_must_name_a_release():
    with pytest.raises(ValueError, match="not a version"):
        knob(5, doc="How many.", default_changed_in="soon", default_was=3)


# -- the install-aware advice -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["source-tree", "editable"])
def test_a_source_checkout_is_told_to_merge_main(kind):
    assert "merge main" in CV.upgrade_advice("0.3.0", kind)
    assert "pip install" not in CV.upgrade_advice("0.3.0", kind)


@pytest.mark.parametrize("kind", ["index", "installed", "unknown"])
def test_an_installed_ddflow_is_told_to_upgrade_its_package(kind):
    text = CV.upgrade_advice("0.3.0", kind)
    assert "upgrade ddflow-mcp to >= 0.3.0" in text and "merge main" not in text


def test_a_vcs_install_is_told_to_reinstall_from_its_source():
    assert "reinstall" in CV.upgrade_advice("", "vcs") and "git" in CV.upgrade_advice("", "vcs")


def test_the_advice_without_a_version_names_no_target():
    assert ">=" not in CV.upgrade_advice("", "index")


def test_install_info_fits_the_advice_to_the_install(monkeypatch):
    fake = II.InstallInfo(version="0.1", kind="index", commit=None, is_own_dev_tree=False)
    assert "merge main" not in II.upgrade_advice("0.3.0", fake)
    fake = II.InstallInfo(version="0.1", kind="source-tree", commit=None, is_own_dev_tree=True)
    assert "merge main" in II.upgrade_advice("0.3.0", fake)


def test_the_running_version_has_one_reader(monkeypatch):
    import ddflow

    monkeypatch.setattr(ddflow, "__version__", "9.9.9")
    assert V.running() == "9.9.9"
    monkeypatch.delattr(ddflow, "__version__")
    assert V.running() == ""


def test_the_source_tree_test_is_one_function(monkeypatch):
    seen: list = []
    monkeypatch.setattr(CV, "is_source_tree", lambda p=None: seen.append(p) or "sentinel")
    assert II.running_from_source("/x") == "sentinel" and seen == ["/x"]
    monkeypatch.undo()
    assert CV.is_source_tree("/usr/lib/python3/site-packages/ddflow") is False
    assert CV.is_source_tree("/home/x/src/ddflow/ddflow") is True


def test_the_startup_warning_fits_the_install(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, "[lease]\nfrom_the_future = true\n")
    monkeypatch.setattr(C, "is_source_tree", lambda *a: False)
    C._WARNED.clear()
    Config.load(repo)
    err = capsys.readouterr().err
    assert "merge main" not in err and "ddflow-mcp" in err
    monkeypatch.setattr(C, "is_source_tree", lambda *a: True)
    C._WARNED.clear()
    Config.load(repo)
    assert "merge main into this tree" in capsys.readouterr().err


# -- doctor lists what an older ddflow skipped, in every table ------------------------------

TABLES = """\
[gate.unit_tests]
command = "pytest -q"
gate_field_from_the_future = 1

[[reviewer]]
name = "r1"
model = "m"
reviewer_field_from_the_future = 1

[[companion]]
id = "c1"
title = "t"
companion_field_from_the_future = 1

[[macro]]
name = "mac"
prompt = "p"
macro_field_from_the_future = 1
"""


def test_skipped_fields_are_found_for_every_table(tmp_path):
    repo = _repo(tmp_path, TABLES)
    from ddflow.services import configcompat

    found = {w.split(" in ")[0]: f for w, f in configcompat.skipped_table_fields(repo)}
    assert found == {
        "[gate.unit_tests]": ["gate_field_from_the_future"],
        "[[reviewer]] #1 (r1)": ["reviewer_field_from_the_future"],
        "[[companion]] #1 (c1)": ["companion_field_from_the_future"],
        "[[macro]] #1 (mac)": ["macro_field_from_the_future"],
    }


def test_skipped_fields_survive_a_table_in_the_wrong_container(tmp_path):
    repo = _repo(tmp_path, '[[gate]]\nname = "g"\nnew_field = 1\n\n[macro]\nx = 1\n')
    from ddflow.services.gates.defs import GateDef
    from ddflow.services.macros import Macro

    path = [repo / ".ddflow" / "config.toml"]
    assert tomlcfg.skipped_fields(path, "gate", GateDef) == []
    assert tomlcfg.skipped_fields(path, "macro", Macro, array=True) == []


def test_skipped_fields_helper_reads_without_warning(tmp_path, capsys):
    repo = _repo(tmp_path, TABLES)
    from ddflow.services.gates.defs import GateDef

    got = tomlcfg.skipped_fields([repo / ".ddflow" / "config.toml"], "gate", GateDef)
    assert got and got[0][1] == ["gate_field_from_the_future"]
    assert capsys.readouterr().err == ""


def test_doctor_names_every_skipped_field_ignored_member_and_newer_format(repo):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        f"format = {CONFIG_FORMAT + 1}\n\n" + cfg.read_text() + "\n" + TABLES + NEWER_BITS
    )
    _code, out, _err = run_cli(repo, "doctor")
    for word in (
        "gate_field_from_the_future",
        "reviewer_field_from_the_future",
        "companion_field_from_the_future",
        "macro_field_from_the_future",
        "kind-from-the-future",
        f"config is format {CONFIG_FORMAT + 1}",
    ):
        assert word in out, word
    if II.install_info().kind in ("source-tree", "editable"):
        assert "merge main" in out
    else:
        assert "upgrade ddflow-mcp" in out


NEWER_BITS = '\n[dedupe]\nkinds = ["bug", "kind-from-the-future"]\n'


def test_set_through_the_cli_works_over_a_newer_file(repo):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + "\n[newsection]\nx = 1\n")
    code, out, err = run_cli(repo, "config", "--set", "lease.ttl_s", "1234")
    assert code == 0, err + out
    text = cfg.read_text()
    assert "[newsection]\nx = 1\n" in text and "ttl_s = 1234" in text
