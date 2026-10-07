# The ddflow upgrade manifest

`changes.toml` lists, per release, what a project upgrading ddflow will meet: config knobs
added, removed, or given a new default (with what a project that never set the knob will
see), event kinds added or removed, and the changes no code diff shows -- instruction, hook
or template refreshes, data repairs, opt-in features and the command that enables each.

It starts from a base (every knob default and event kind of ddflow 0.1.3); replaying the
base and every release gives exactly the knobs and event kinds of the ddflow that ships
it. Changes not yet released are one fragment file each under `unreleased/`.

Read it with `ddflow.services.upgrade_manifest`: `changes_since(version)` is every change
in a newer release, oldest first; `replay()` is what a release has. In the ddflow source
tree, `scripts/upgrade_manifest.py backfill` rebuilds it from the release commits and keeps
every hand-written `why`, `effect`, `impact` and `enable`.

## The release lint

`ddflow version lint` compares the code's knob defaults and event kinds with the manifest's
replay: a change with no entry is reported by name, with the fragment that would announce it
and the operator's options (write the entries; `--waive <change> --reason ...`, recorded in
`waivers.toml` beside the manifest; or change `[release].manifest_lint`). `ddflow version
cut`, `scripts/release.sh` and the publish workflow run it, and `block` (the default) stops
the release.
