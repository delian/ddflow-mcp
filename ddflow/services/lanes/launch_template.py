"""Lane templates and their approval (SW-launch, D-swarm-answers Q2 and Q6).

A template is data: harness + route + model + budget + trust tier + the weakest hook level it
accepts. The scheduler may launch a lane only from a template a PERSON approved at the template's
current digest: ``approval.check`` over the subject ``lane-template:<name>``. Any edit changes the
digest, so an edited template is not approved. Approval is a person's act (``approval.grant``
refuses under an agent identity); nothing here approves, and a template file cannot approve
itself: an ``approved`` key in a file is ignored and reported, never trusted.
"""

from __future__ import annotations

import difflib
import json
import math
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ...core.digest import content_digest
from .. import approval

PREFIX = "lane-template:"
HOOK_LEVELS = ("detect", "prevent")  # weakest first
#: Harnesses the shipped launch can start (the opencode/kilo process lane).
HARNESSES = ("opencode", "kilo")
_TEXT = ("harness", "route", "model", "family")
_NUM = ("trust", "budget_usd", "price_in", "price_out")


@dataclass(frozen=True)
class Template:
    name: str
    harness: str  #: opencode | kilo
    route: str  #: a model-catalog route id
    family: str  #: model family (independence and per-family caps)
    model: str = ""  #: "" = the route's served model
    trust: int = 0  #: tier the lane holds
    budget_usd: float = (
        0.0  #: most one launch may be estimated to cost; the launch is refused above it
    )
    min_hooks: str = "detect"  #: weakest hook level accepted: detect | prevent
    kinds: tuple[str, ...] = ()  #: empty = any kind
    price_in: float | None = None  #: USD per million tokens, for the budget check; None = unknown
    price_out: float | None = None
    local: bool = False

    @property
    def subject(self) -> str:
        return PREFIX + self.name

    def canonical(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return content_digest(self.canonical(), "sha256", length=16)

    def body(self) -> str:
        """What the operator reads before approving: one ``key = value`` line per field."""
        return "".join(f"{k} = {json.dumps(v)}\n" for k, v in sorted(asdict(self).items()))


@dataclass(frozen=True)
class Consent:
    """The template is not approved: nothing was started."""

    subject: str
    digest: str
    diff: str
    how: str


def problems(t: Template) -> list[str]:
    out = [f"{k} is empty" for k in ("name", *_TEXT) if k != "model" and not getattr(t, k)]
    if t.name and ("/" in t.name or any(c.isspace() for c in t.name)):
        out.append("name must not contain whitespace or a slash")
    if t.harness and t.harness not in HARNESSES:
        out.append(f"harness {t.harness!r} cannot be launched yet (supported: {list(HARNESSES)})")
    if t.min_hooks not in HOOK_LEVELS:
        out.append(f"min_hooks must be one of {list(HOOK_LEVELS)}")
    if min(t.trust, t.budget_usd, t.price_in or 0, t.price_out or 0) < 0:
        out.append("trust, budget_usd and prices must not be negative")
    if t.budget_usd <= 0 and not t.local:
        out.append("a template that is not local needs a budget_usd above zero")
    return out


def consent(t: Template) -> Consent:
    """The diff (the template as an added file) and what a person runs to approve it."""
    lines = t.body().splitlines(keepends=True)
    diff = "".join(difflib.unified_diff([], lines, "/dev/null", f"templates/{t.name}"))
    how = (
        f"Launching lanes from template {t.name!r} needs a person's approval, once per template and "
        f"digest: `ddflow lanes approve {t.name}` records the approval at the template's current "
        f"digest {t.digest()}, then re-run. An agent never approves it."
    )
    return Consent(t.subject, t.digest(), diff, how)


def approved(state: Any, t: Template) -> approval.Verdict:
    return approval.check(state, t.subject, t.digest())


def approve(
    log: Any, t: Template, *, requested_agent: str = "", note: str = ""
) -> approval.Approval:
    """A PERSON approves the template as it is now. Raises ``ApprovalRefused`` under an agent."""
    return approval.grant(log, t.subject, t.digest(), note=note, requested_agent=requested_agent)


def _bad_type(raw: dict[str, Any]) -> str:
    """The first field of ``raw`` that has the wrong type or a non-finite number, or ""."""
    for k in (*_TEXT, "min_hooks"):
        if k in raw and not isinstance(raw[k], str):
            return f"{k} must be text"
    for k in _NUM:
        v = raw.get(k)
        if v is not None and (
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        ):
            return f"{k} must be a finite number"
    if "local" in raw and not isinstance(raw["local"], bool):
        return "local must be true or false"
    kinds = raw.get("kinds", [])
    if not isinstance(kinds, list) or not all(isinstance(x, str) for x in kinds):
        return "kinds must be a list of text"
    return ""


def _row(name: str, raw: dict[str, Any]) -> tuple[Template | None, list[str]]:
    notes: list[str] = []
    if "approved" in raw:
        notes.append(f"{name}: the `approved` key is ignored; approval is a person's event")
    known = set(Template.__dataclass_fields__)
    if unknown := sorted(set(raw) - known - {"approved"}):
        return None, [*notes, f"{name}: unknown key(s) {unknown}"]
    if bad := _bad_type(raw):
        return None, [*notes, f"{name}: {bad}"]
    kw = {k: v for k, v in raw.items() if k in known and k != "name"}
    if "kinds" in kw:
        kw["kinds"] = tuple(kw["kinds"])
    for req in ("harness", "route", "family"):
        kw.setdefault(req, "")  # a missing field is reported by `problems`, not a crash
    t = Template(name=name, **kw)
    if bad := problems(t):
        return None, [*notes, *(f"{name}: {b}" for b in bad)]
    return t, notes


def load(path: Path) -> tuple[dict[str, Template], list[str]]:
    """``[template.<name>]`` tables of a TOML file. Returns the valid templates and one note per
    defect (a missing file is no templates and no note: launching is then simply unavailable)."""
    path = Path(path)
    if not path.is_file():
        return {}, []
    try:
        doc = tomllib.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return {}, [f"{path.name}: cannot be read: {e}"]
    out: dict[str, Template] = {}
    notes: list[str] = []
    tables = doc.get("template", {})
    if not isinstance(tables, dict):
        return {}, [f"{path.name}: `template` must hold [template.<name>] tables"]
    for name, raw in tables.items():
        t, n = _row(name, raw if isinstance(raw, dict) else {})
        notes += n
        if t is not None:
            out[name] = t
    return out, notes
