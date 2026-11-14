# Stack table

One file per stack: how a project of that stack builds, smokes, lock-checks and tests, plus the
facts the stack-aware tools need (import extractor, dead-code and dependency tools, comment
tokens). Read by `ddflow/services/stacks.py`; `ddflow stacks` prints what a repository resolves to.

A project adds or replaces a stack by dropping a file of the same shape into
`[verify].stacks_dir` (default `.ddflow/stacks/`); the same name replaces the shipped entry.

    [stack]
    name = "rust"                  # the id; `[verify].stack` forces it
    title = "Rust (cargo)"
    detect = ["Cargo.toml"]        # globs relative to the repo root; any match selects it
    priority = 50                  # the higher wins when several stacks match (ties: name)
    lock_files = ["Cargo.lock"]
    comment_tokens = ["//"]
    import_extractor = "regex:^\\s*use\\s+([\\w:]+)"   # consumed by the import-graph work
    dead_tool = "cargo-udeps"
    dep_tool = "cargo-audit"

    [commands]                     # roles: build, smoke, lock_check, test
    build = "cargo build --locked" # a string, or a list of {when = "<file>", run = "<cmd>"};
                                   # the first entry whose `when` file exists wins, one without `when` is the default

A role with no entry means the stack has no such step (there is no lock-check for Maven). The
program each command starts with must be on PATH: when it is not, the step is `unavailable`,
never passed.
