"""`ddflow completion install/uninstall`: append and remove ONLY the guarded block."""

from __future__ import annotations

import argparse

from ddflow.services.completion import install
from ddflow.surfaces.parsers import completion


def test_install_appends_a_guarded_block(tmp_path):
    text = install.install_text("bash", "# my rc\n")
    assert "# >>> ddflow completion >>>" in text
    assert "source <(ddflow completion bash)" in text
    assert "# my rc\n" in text  # the existing content is left alone


def test_install_is_idempotent(tmp_path):
    once = install.install_text("bash", "")
    again = install.install_text("bash", once)
    assert once == again


def test_uninstall_removes_only_the_guard_block(tmp_path):
    with_block = install.install_text("zsh", "export X=1\n")
    gone = install.uninstall_text("zsh", with_block)
    assert "ddflow completion" not in gone
    assert "export X=1\n" in gone  # the line install did not write stays


def test_target_file_per_shell(tmp_path, monkeypatch):
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert install.target_file("fish", tmp_path) == tmp_path / ".config" / "fish" / "config.fish"
    assert install.target_file("zsh", tmp_path) == tmp_path / ".zshrc"
    assert install.target_file("bash", tmp_path) == tmp_path / ".bashrc"


def test_target_file_honors_zdotdir_and_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZDOTDIR", str(tmp_path / "zdot"))
    assert install.target_file("zsh", tmp_path) == tmp_path / "zdot" / ".zshrc"
    monkeypatch.delenv("ZDOTDIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert install.target_file("fish", tmp_path) == tmp_path / "xdg" / "fish" / "config.fish"


def test_activate_line_differs_for_fish():
    text = install.install_text("fish", "")
    assert "ddflow completion fish | source" in text
    text = install.install_text("bash", "")
    assert "source <(ddflow completion bash)" in text


def test_uninstall_a_dangling_begin_marker_keeps_the_rest():
    src = "# >>> ddflow completion >>>\n# user comment\n"
    assert install.uninstall_text("bash", src) == "# user comment\n"


def test_uninstall_an_orphaned_begin_before_a_block_keeps_user_content():
    src = (
        "# >>> ddflow completion >>>\n"
        "export PATH=/x\n"
        "# >>> ddflow completion >>>\n"
        "source <(ddflow completion bash)\n"
        "# <<< ddflow completion <<<\n"
    )
    assert install.uninstall_text("bash", src) == "export PATH=/x\n"


def test_uninstall_does_not_write_without_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    rc = tmp_path / ".zshrc"
    rc.write_text(install.install_text("zsh", "export X=1\n"))
    a = argparse.Namespace(cmd="uninstall", shell="zsh", yes=False)
    assert completion._do_uninstall(a) == 0
    assert "ddflow completion" in rc.read_text()  # previewed, not written


def test_uninstall_writes_only_with_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    rc = tmp_path / ".zshrc"
    rc.write_text(install.install_text("zsh", "export X=1\n"))
    a = argparse.Namespace(cmd="uninstall", shell="zsh", yes=True)
    assert completion._do_uninstall(a) == 0
    assert "ddflow completion" not in rc.read_text()
    assert "export X=1\n" in rc.read_text()  # the line install did not write stays


def test_install_skips_a_commented_not_activated_line(tmp_path):
    commented = f"# {install._activate_line('bash')}\n"
    assert install.install_text("bash", commented) != commented
    assert "# >>> ddflow completion >>>" in install.install_text("bash", commented)
