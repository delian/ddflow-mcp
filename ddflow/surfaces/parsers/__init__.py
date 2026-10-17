"""The `ddflow` argument parser, one module per command group.

`cli.build_parser` builds the root parser and its global options, then calls each module's
`register(s)` in the order of `REGISTER_ORDER` -- which is the order `ddflow --help` lists
the subcommands, so it is part of the interface.
"""

from __future__ import annotations

from . import (
    chore,
    decide,
    docs,
    flow,
    jobs,
    knowledge,
    lanes,
    lifecycle,
    maintenance,
    metrics,
    plan,
    queue,
    records,
    report,
    reporting,
    review,
    rules,
    workflow,
)

#: Each command group's `register`, in `ddflow --help` order.
REGISTER_ORDER = (
    queue.register,
    chore.register,
    plan.register,
    lifecycle.register,
    flow.register,
    jobs.register,
    knowledge.register,
    rules.register,
    records.register,
    reporting.register,
    report.register,
    maintenance.register,
    lanes.register,
    decide.register,
    docs.register,
    metrics.register,
    review.register,
    workflow.register,
)
