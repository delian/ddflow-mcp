"""`ddflow approvals`: request, list, show, approve, deny, revoke, withdraw. Generated from
`surfaces/declared/approvals.py`."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..commands import approvals as H
from ..declared import approvals as declared
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `approvals` to `s`, the root `ddflow` subparsers."""
    add_commands(
        s,
        declared.COMMANDS,
        groups={"approvals": "approval requests for operator-gated commands (a person answers)"},
        handlers={
            ("approvals", "list"): H.cmd_approvals_list,
            ("approvals", "log"): H.cmd_approvals_log,
            ("approvals", "show"): H.cmd_approvals_show,
            ("approvals", "request"): H.cmd_approvals_request,
            ("approvals", "ask"): H.cmd_approvals_ask,
            ("approvals", "attest"): H.cmd_approvals_attest,
            ("approvals", "approve"): H.cmd_approvals_approve,
            ("approvals", "deny"): H.cmd_approvals_deny,
            ("approvals", "revoke"): H.cmd_approvals_revoke,
            ("approvals", "withdraw"): H.cmd_approvals_withdraw,
        },
        executor=cliexec.handler,
    )
