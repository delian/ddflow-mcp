"""The `[verify]` section: how a project builds and smokes itself, per stack (R-maintainable-code).

Re-exported from `ddflow.config`, which assembles `Config` from every section. The data is the
stack table (`ddflow/templates/stacks/`, `ddflow/services/stacks.py`); these knobs choose and
override it."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("verify")
@dataclass
class VerifyConfig:
    """Build and smoke verification: which stack a project is, and the commands it runs."""

    stack: str = knob(
        "",
        doc="The stack of this project, by its name in the stack table (python, node, rust, go, java, or one a project adds under `stacks_dir`). Empty (default): detected from the project's own files (pyproject.toml, package.json, Cargo.toml, go.mod, pom.xml ...). A name the table does not know resolves to an explicit `none` with the command that fixes it, never to another stack's commands. `ddflow stacks` shows what this repository resolves to.",
    )
    stacks_dir: str = knob(
        ".ddflow/stacks",
        doc="A project's own stack table: a directory (relative to the repository root) of `<stack>.toml` files in the shipped shape (see ddflow/templates/stacks/README.md). A file whose stack name matches a shipped one replaces it; a new name adds a stack. A file that does not parse is reported and skipped. Empty: no project table.",
    )
    build_cmd: str = knob(
        "",
        doc="Overrides the stack's build command (run in a clean clone by the periodic clean-clone pass, and shown by `ddflow stacks`). Empty (default): the stack table's command. Also the way to give a project no table describes a build at all.",
    )
    smoke_cmd: str = knob(
        "",
        doc="Overrides the stack's smoke command: the cheapest run that fails when the build is broken (never the whole test suite). Empty (default): the stack table's command.",
    )
    lock_check_cmd: str = knob(
        "",
        doc="Overrides the stack's lock-check command (does the lockfile still match the manifest). Empty (default): the stack table's command; a stack with none has no lock check.",
    )
    test_cmd: str = knob(
        "",
        doc="Overrides the stack's test command as the stack table reports it. This does NOT change the `unit_tests` gate (`.ddflow/gates.toml` does). Empty (default): the stack table's command.",
    )
