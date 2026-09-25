Bring this project's existing work into the queue{% if scope %}, starting from {{ scope }}{% endif %}, so ddflow continues it rather than restarting it.

This project has history. A queue that starts empty tells the next agent "nothing is in flight" about a repository that may have three branches in flight and a todo file with forty open items. That is worse than no queue, because the scheduler hands it out.

## First: has this already been done?

    ddflow_import_verify           → what is imported, and what is still owed

Run this before anything else. It is safe and it writes nothing, and its answer decides which half of this prompt you are doing. Exit 2 means nothing was ever imported.

**If nothing is imported yet**, start at §1 below.

**If something is imported already**, you are not repeating the import — you are finishing and refreshing it. Skip to §3 and work from what `ddflow_import_verify` reported:

- **Tasks with no globs.** The commonest leftover, and the one with teeth: an item that has not said what it writes is one the conflict detector cannot protect, so two agents can be handed the same file and neither is refused. `ddflow_update <id> --globs "..."`.
- **Phases claiming SHIPPED over an open task.** Real drift between a heading and its checkboxes. Do not guess which is stale — if the heading is right the queue is about to hand out finished work, and if the boxes are right the phase is not done. Ask the operator.
- **The source has moved on.** `ddflow_import` (no flags) shows exactly what a re-run would add; `apply=true` adds it. Re-running is safe and idempotent: ids derive from the source, so it adds what is new and leaves the rest alone.
- **A source file that has vanished.** Something was renamed or deleted since the import. The items are still real; their provenance is not checkable any more. Tell the operator which.

Then stop. Do not re-import work that is already in the queue, and do not "tidy" ids — the ids came from the project's own files and changing them breaks every `Needs:` pointing at them.

## What is mechanical, and what is yours

`ddflow_import` reads what it can **verify** — a ticked checkbox, a `##` heading in a lessons file, a file under `docs/adr/`, a branch with commits not on the base. It reports; it writes nothing until told.

Everything below is the half it cannot do. Do not skip it and apply the proposal as-is: an import that guesses produces a confident, wrong queue, and every wrong item teaches the operator to distrust the rest.

## 1. Look before you write

    ddflow_import                  → what it found, with the source line of each

Read the proposal against the actual files. Three things are guesses and are usually the ones that are wrong:

- **Headings became phases, checkboxes became tasks.** Often right, sometimes badly wrong — a "Notes" heading is not a phase, and a checkbox under it is not a task.
- **No task has globs** unless the file happened to declare them. A task with no globs is one the conflict detector cannot protect, so two agents can be handed the same file.
- **Dependencies are only what the file said**, which in most projects is nothing. Order in a markdown list is not a dependency, and treating it as one would be inventing a constraint the operator never stated.

## 2. Ask the operator the questions only they can answer

Put these to them plainly, with the proposal in front of you:

- **Which of the open items are actually live?** Todo files accumulate. Something filed eighteen months ago and never started is usually not "ready to start now" — it is a wish. Importing it as ready means an agent may pick it up.
- **What is genuinely in flight?** The branches are the strongest signal. For each: is it live work, an abandoned experiment, or something already merged another way?
- **Which lessons still apply?** A lessons corpus contains rules about symbols that no longer exist. Import them anyway — `ddflow_recall` ranks by relevance, and a lesson nobody can search is a lesson nobody applies — but tell the operator which ones you suspect are stale rather than silently retiring them.
- **Which decisions are still in force?** An ADR marked `Superseded` should be imported as superseded, not dropped: the reconstruction needs to know what was once believed and why it changed.

## 3. Apply, then repair

    ddflow_import (apply=true)     → writes them, each recording its source

Then, and this is the part that makes the queue usable rather than decorative:

- **Give every task its globs.** `ddflow_update <id> --globs "..."`. Until a task declares what it writes, parallel work on it is unsafe and ddflow cannot say so.
- **Declare the dependencies you and the operator identified.** `ddflow_update <id> --needs "..."`. Remember these are inherited: a phase's dependency governs every task inside it.
- **Check the picker can see the work.** `ddflow_next` must offer something. A queue where everything is blocked usually means a dependency was imported as a typo — an unknown dependency is treated as unmet, deliberately, so a typo surfaces as a blocked item rather than as work that starts early.
- **Record the import itself.** `ddflow_session_prompt` with what the operator told you, and `ddflow_decision_add` for any structural choice you made together — "we treated each release heading as a phase" is exactly the kind of decision the next reader will otherwise have to reverse-engineer.

## 4. Say what you did not import

Report it explicitly:

- how many completed items were left out (they are history, not a queue — `include_done` brings them in as closed if the operator wants the record);
- any file that looked like a source but yielded nothing, which usually means an unusual format rather than an empty file;
- anything you found and deliberately skipped, with why.

A silent import is one nobody can check. The operator should finish this able to say what is now in the queue and what is not.

## If the mechanical scan found nothing

That is not failure — it looks in the usual places (`docs/todo.md`, `tasks/todo.md`, `docs/lessons.md`, `docs/adr/`, unmerged branches). If this project keeps its work somewhere else, ask the operator where, read it yourself, and file the items directly with `ddflow_task_add` / `ddflow_lesson_add` / `ddflow_decision_add`. The importer is a convenience; you are the thing that can read an unfamiliar format.
