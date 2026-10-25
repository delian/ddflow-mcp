"""`ddflow waves --stamp` reports that stamping is not implemented, with its guidance.

Bug B47acb25115: the CLI register had no `waves` word at all, so `ddflow waves --stamp`
died with a bare `argument cmd: invalid choice: 'waves'` and nothing told the caller what
D-wave-semantics says and what to do meanwhile. The register now surfaces the command so
the missing `--stamp` is reported WITH the followable interim mechanism. The real compute
and stamping arrive with LB-waves; this command neither implements nor promises them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

#: The documented interim mechanism (docs/ddflow/decisions/token-spend.md): the roadmap
#: restamp script driving `ddflow update --add-tags`/`--remove-tags` until LB-waves ships.
_STAMP_SCRIPT = "wave_stamp_accurate.py"


def test_waves_stamp_is_reported_missing_with_guidance(repo):
    code, out, err = run_cli(repo, "waves", "--stamp")
    shown = out + err
    assert code == 2, (code, out, err)
    assert "invalid choice" not in shown, shown
    assert "LB-waves" in shown, shown
    assert _STAMP_SCRIPT in shown, shown


def test_waves_alone_reports_the_same_pending_status(repo):
    code, out, err = run_cli(repo, "waves")
    shown = out + err
    assert code == 2, (code, out, err)
    assert "LB-waves" in shown, shown
    assert _STAMP_SCRIPT in shown, shown
