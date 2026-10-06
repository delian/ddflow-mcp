"""The `[loops]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc


@dataclass
class LoopsConfig:
    """Runtime loop detection — work that repeats instead of finishing."""

    max_claims_per_item: int = 3
    max_gate_flaps: int = 4
    max_reopens: int = 2
    max_repeated_failures: int = 3
    max_duplicate_items: int = 2
    no_progress_window: int = 60
    on_detect: str = "warn"  # warn | block


_doc(
    "loops",
    "max_claims_per_item",
    "How many times an item may be claimed and given up WITHOUT completing before it is reported as thrashing. Expiries (crash recovery) are excluded — only deliberate release/re-claim cycles count, because a crash is a different problem with a different remedy.",
)
_doc(
    "loops",
    "max_gate_flaps",
    "How many times a gate's verdict may flip between passed and failed on one item before it is reported as flapping. A gate that cannot decide is flaky or measuring a moving target; re-running it will not converge.",
)
_doc(
    "loops",
    "max_repeated_failures",
    'How many CONSECUTIVE failed runs of one gate with the SAME output digest on one item are reported as `repeated_failure` (default 3; 0 turns the detector off). A pass, a different output, or a failure with no recorded digest ends the streak; the reviewer gates (rubber_duck, critic) are never counted. It warns in `doctor`, `ddflow loops` and the item\'s brief; with `on_detect = "block"` it also refuses `claim`, and refuses a `gate run` of that gate while the work is byte-for-byte what last failed. The digest is over the raw output, so a gate that prints timings never repeats -- a deterministic summary line is what makes it comparable.',
)
_doc(
    "loops",
    "max_reopens",
    "How many times an item may be COMPLETED before that is reported as work that will not stay done — usually a sign the acceptance criteria are not written in the item, so each pass finishes something different.",
)
_doc(
    "loops",
    "max_duplicate_items",
    "How many live items may declare exactly the same file globs before that is reported. Two items writing one file cannot run in parallel; whether one re-describes the other is a question for their titles and bodies, not their globs.",
)
_doc(
    "loops",
    "no_progress_window",
    "How many recent events with NO completion, gate pass or merge count as a stalled queue. Measured in events, not minutes, because an agent that is thinking produces no events and waiting is not looping.",
)
_doc(
    "loops",
    "on_detect",
    "'warn' reports loops in `doctor` and `next` and lets work continue; 'block' additionally makes `ddflow claim` REFUSE an item that is already looping, which is the only thing that actually stops an agent spinning on it.",
)
