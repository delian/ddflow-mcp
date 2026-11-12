"""`ddflow completion install/uninstall`: append and remove ONLY the guarded block."""

from __future__ import annotations

from ddflow.services.completion import install


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


def test_target_file_per_shell(tmp_path):
    assert install.target_file("fish", tmp_path) == tmp_path / ".config" / "fish" / "config.fish"
    assert install.target_file("zsh", tmp_path) == tmp_path / ".zshrc"
    assert install.target_file("bash", tmp_path) == tmp_path / ".bashrc"
