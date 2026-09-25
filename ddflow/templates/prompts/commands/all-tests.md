Get every test in the project green, and leave the tree clean.

## 1. Clean first

Run the `code-clean` workflow to completion before testing anything. Testing a tree with unlanded work tells you about a state that does not exist: the suite you run is not the suite anyone else will run.

## 2. Run every kind of test, not just the fast one

    ddflow_gate_run <item> unit_tests
{% for gate in test_gates %}
    ddflow_gate_run <item> {{ gate }}
{% endfor %}

Configure the ones this project has in `.ddflow/config.toml` — integration, UI, end-to-end, property, smoke, whatever exists:

    [gate.integration_tests]
    command = "pytest tests/integration -q"

    [gate.e2e_tests]
    command = "npm run test:e2e"

**A suite that is not configured is not passing — it is absent.** `ddflow_gate_status` shows `[?]` for a gate that could not run, and that is a coverage gap, never a tick.

## 3. Run the FULL suite, not a selection

A targeted run tells you your change is fine and says nothing about what was already broken. Standing breakage hides behind targeted gates indefinitely, and the first full run after a long gap is always the expensive one.

Where selection is unavoidable, derive it from the diff rather than reasoning about it: `git diff --name-only`, then the tests that reference those paths. Choosing by intuition is guessing.

## 4. Fix what fails — under the bug-hunt rule

Every failure is a finding, and the same rule applies: the fix ships with a probe that demonstrates the defect, mutation-verified by reverting the fix and watching it fail.

Before fixing, decide which kind of failure it is:

- **a real defect** — fix it, keep the test
- **a wrong test** — fix the test, and say in the commit why it was wrong
- **a flaky test** — re-run it in isolation first. A test that passes alone and fails in the suite is an *order dependency*, which is a real bug in one of them, not noise. Find which test leaves the state behind.
- **an environment failure** — record it as `unavailable` with the reason. It is not a pass and it is not a defect in this code.

## 5. Report honestly

    ddflow_gate_record <item> unit_tests --outcome passed --evidence "<command, counts, duration>"

Attach the real numbers. "Tests pass" with no command and no counts is an assertion, not evidence — and a suite that collected zero tests also "passes".

## 6. Close out

    ddflow_loops       — a test that keeps flapping is a loop; re-running it will not converge
    ddflow_progress    — where the effort went
