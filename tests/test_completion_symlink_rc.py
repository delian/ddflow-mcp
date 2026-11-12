"""`ddflow completion install/uninstall` writes THROUGH a symlinked rc file, never replacing the link."""

from __future__ import annotations

import argparse

from ddflow.surfaces.parsers import completion as P


def _args(cmd: str) -> argparse.Namespace:
    return argparse.Namespace(cmd=cmd, shell="bash", yes=True)


def test_install_and_uninstall_keep_a_symlinked_bashrc(tmp_path, monkeypatch) -> None:
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "bashrc"
    real.write_text("# mine\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").symlink_to(real)
    monkeypatch.setattr(P, "_home", lambda: str(home))
    assert P._do_install(_args("install")) == 0
    assert (home / ".bashrc").is_symlink()
    assert "ddflow" in real.read_text(encoding="utf-8")
    assert P._do_uninstall(_args("uninstall")) == 0
    assert (home / ".bashrc").is_symlink()
