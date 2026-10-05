"""The `ddflow` argument parser, one module per command group.

`cli.build_parser` builds the root parser and its global options, then calls each module's
`register(s)` in the order of `REGISTER_ORDER` -- which is the order `ddflow --help` lists
the subcommands, so it is part of the interface.
"""

from __future__ import annotations

from . import (
    flow,
    jobs,
    knowledge,
    lifecycle,
    maintenance,
    queue,
    records,
    reporting,
    review,
    rules,
    workflow,
)

#: Each command group's `register`, in `ddflow --help` order.
REGISTER_ORDER = (
    queue.register,
    lifecycle.register,
    flow.register,
    jobs.register,
    knowledge.register,
    rules.register,
    records.register,
    reporting.register,
    maintenance.register,
    review.register,
    workflow.register,
)
