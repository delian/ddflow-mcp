# isort: skip_file
"""The `[section]` dataclasses of `ddflow.config`, one module per section.

`ddflow.config` imports every module here in the order the sections are declared --
which is the order their knob docs register, and so the order `config --explain` lists
them -- and assembles `Config` from them. Import from `ddflow.config`, not from here.
"""

from __future__ import annotations

# Declaration order, NOT alphabetical: importing a section registers its knob docs, so this
# line is the order `config --explain` lists every knob in.
from . import (
    lease,
    worktree,
    flow,
    gates,
    lessons,
    session,
    schedule,
    bugs,
    memory,
    dedupe,
    imports,
    companions,
    reinstruct,
    cadence,
    rules,
    ci,
    prompts,
    log,
    upgrade,
    mcp,
    export,
    loops,
    review,
    enforce,
    agent,
)

#: Every section module, in declaration order.
SECTION_MODULES = (
    lease,
    worktree,
    flow,
    gates,
    lessons,
    session,
    schedule,
    bugs,
    memory,
    dedupe,
    imports,
    companions,
    reinstruct,
    cadence,
    rules,
    ci,
    prompts,
    log,
    upgrade,
    mcp,
    export,
    loops,
    review,
    enforce,
    agent,
)
