"""The harness channel (L2): a person answers an AskUserQuestion in the agent's own chat and
ddflow reads the answer from the transcript the HARNESS wrote, never from the agent (RA-channel-harness,
D-RA-Q5, research/remote-approvals-probe.md).

Flow: `ddflow approvals ask ID` prints the exact AskUserQuestion payload (the question is the text
ddflow rendered, with the ask id and digest, and two labelled options); the agent puts it to the
person; `ddflow approvals attest ID` finds the harness-written answer row in the session transcript
and records the ask answered at level 2 with the tool_use id as proof. The agent only couriers:
a row counts only when it is an accepted result for an AskUserQuestion whose tool_use asked
EXACTLY this question, with row ids that line up, after the request was filed.

What this does not prove (said in the probe and by `doctor`): that a person, not a script holding
the session, answered, and that the transcript file was not edited -- it is the agent's own uid's
file. Accepted for tier T2 only -- except a TYPED answer to a T3 act when the operator opted in
(`[approvals].accept_chat_for_t3`, D-RA-T3-chat), rated "L2-typed" and warned on at every use.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from ...config_sections._layers import LAYERS, layer_path, read_toml
from ...core import clock
from ...core.model import fold
from ...infra.docindex import read_only_rows
from .. import configwrite as CW
from .. import harnessreg
from ..actors import resolve_actor
from ..ask import engine as E

TOOL = "AskUserQuestion"
#: attest's message begins with this while the person has simply not answered yet (exit 2)
WAITING = "nothing to attest yet: "
VIA = "harness"
LEVEL = 2


TYPED_PROOF = "typed:"
#: The grade a tier-3 act approved by a typed chat answer carries in the audit trail.
GRADE_T3_CHAT = "L2-typed"
KNOB_T3 = "approvals.accept_chat_for_t3"
_TRUE = ("1", "true", "yes", "on")

#: Kilo holds its session transcripts in SQLite, not JSONL, at this standard path (a pinned
#: fact from the operator's machine; sessions/memory also live under `~/.local/share/kilo/`).
#: A surfaced AskUserQuestion is a `part` row whose `data.tool` is `question` and whose answer
#: is the person's selected option in `state.metadata.answers` -- the harness-written record.
KILO_DB = Path("~/.local/share/kilo/kilo.db").expanduser()
KILO_TOOL = "question"


def _forbidden(cfg: Config) -> bool:
    """`forbid_chat_for_t3` true in ANY layer (the merged value, each file layer read raw, the
    environment): a later layer saying false must not undo a tightening below it."""
    if cfg.approvals.forbid_chat_for_t3:
        return True
    if os.environ.get("DDFLOW_APPROVALS_FORBID_CHAT_FOR_T3", "").strip().lower() in _TRUE:
        return True
    if cfg.root is None:
        return False
    for layer in LAYERS:
        path = layer_path(cfg.root, layer)
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):
                continue  # the config loader skips a non-regular file too; opening a FIFO would hang
            data = read_toml(path)
        except FileNotFoundError:
            if os.path.islink(path):
                return True  # a dangling symlink is a layer that exists and cannot be read: fail closed
            continue  # no such layer (a missing parent directory is the same absence)
        except (OSError, ValueError):
            return True  # a layer that exists and cannot be read may be the one that forbids: fail closed
        if (data.get("approvals") or {}).get("forbid_chat_for_t3") is True:
            return True
    return False


def chat_t3(cfg: Config) -> bool:
    """May a typed chat answer approve a tier-3 act under THIS config? Only when the operator set
    the opt-in in the machine-local layer: a value from the committed file or the environment is
    ignored (a cloned repository must not enable it), and `forbid_chat_for_t3` in any layer wins."""
    return bool(
        cfg.approvals.accept_chat_for_t3
        and cfg.sources.get(KNOB_T3) == "local"
        and not _forbidden(cfg)
    )


def typed_t3(row: dict[str, Any], cfg: Config, level: int) -> bool:
    """Is this answered row a T3 act approved by a verified typed chat answer under the opt-in?"""
    return (
        row["min_level"] > LEVEL
        and level == LEVEL
        and row.get("via") == VIA
        and str(row.get("proof", "")).startswith(TYPED_PROOF)
        and chat_t3(cfg)
    )


@dataclass(frozen=True)
class Attested:
    tool_use_id: str
    answer: str
    at: str
    file: str


def labels(ask_id: str) -> tuple[str, str]:
    return (f"Approve {ask_id}", f"Deny {ask_id}")


#: The "approve + standing window" choice: how long it opens the window (hours, the window's
#: cap) and which generic standing rules it switches on. It is COUNT-independent of the
#: retry-threshold knob: this is the operator's IN-QUESTION opt-in, the retry rule is the
#: ask-refused-count fallback.
WINDOW_CHOICE_HOURS = 24
WINDOW_CHOICE_POLICY = (
    "complete_unavailable",
    "claim_unavailable",
    "skip_after_unavailable",
    "accept_baseline_red",
)


def window_label(ask_id: str) -> str:
    """The third AskUserQuestion option a GENERIC request offers: approve AND open the window."""
    return f"Approve {ask_id} + standing window ({WINDOW_CHOICE_HOURS}h)"


def _generic(cfg: Config, row: dict[str, Any]) -> bool:
    """May a request of this verb be answered with the standing-window choice? The generic
    T2 class the window pre-authorises (`[approvals].standing_verbs`), nothing else."""
    return row["verb"] in cfg.approvals.standing_verbs and row["min_level"] <= LEVEL


def _outcome(answer: str, ask_id: str) -> str | None:
    """The outcome an accepted answer expresses: approved (plain or +window), denied, or None."""
    yes, no = labels(ask_id)
    if answer in (yes, window_label(ask_id)):
        return E.APPROVED
    return E.DENIED if answer == no else None


def payload(row: dict[str, Any], cfg: Config) -> dict[str, Any]:
    """The AskUserQuestion input the agent must send, unaltered. An approval offers
    approve/deny (a generic request also the standing-window choice); a question offers its
    preset options (one is the answer) when it has them, else a free-text answer for exactly
    its text (no `options` key: the person types the answer)."""
    if row["type"] == E.QUESTION:
        q: dict[str, Any] = {
            "question": row["text"],
            "header": "Question",
            "multiSelect": False,
        }
        opts = row.get("options") or []
        if opts:
            q["options"] = [
                {"label": str(o), "description": "answer the question with this option"}
                for o in opts
            ]
        return {"questions": [q]}
    yes, no = labels(row["id"])
    options = [
        {"label": yes, "description": "approve exactly this command, once"},
        {"label": no, "description": "deny it"},
    ]
    if _generic(cfg, row):
        options.append(
            {
                "label": window_label(row["id"]),
                "description": (
                    f"approve this command AND pre-authorise the generic T2 class for "
                    f"{WINDOW_CHOICE_HOURS}h: sets approvals.standing_window_s and switches on "
                    f"the generic approvals.policy rules"
                ),
            }
        )
    return {
        "questions": [
            {
                "question": row["text"],
                "header": "Approval",
                "multiSelect": False,
                "options": options,
            }
        ]
    }


def _dirs(cfg: Config) -> list[Path]:
    roots = list(cfg.approvals.transcript_dirs)
    for h in harnessreg.load():
        if h.transcripts:
            roots.extend(h.transcripts.root)
    out: list[Path] = []
    seen: set[Path] = set()
    for d in roots:
        p = Path(d).expanduser()
        try:
            p = p.resolve()  # collapse a symlink alias: the same dir scanned once
        except (OSError, RuntimeError):
            p = p.absolute()  # an untraversable component or symlink loop: keep the spelling
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _files(cfg: Config, since: float) -> list[Path]:
    found: list[Path] = []
    for base in _dirs(cfg):
        if base.is_dir():
            found += [p for p in base.rglob("*.jsonl") if p.stat().st_mtime >= since]
    return sorted(found, key=lambda p: p.stat().st_mtime)


def _is_sqlite(path: Path) -> bool:
    """Is this file a SQLite database (Kilo's transcript store)? Told by the magic header,
    never by its name: a `.jsonl` the store happens to be must not be opened as one."""
    try:
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _kilo_dbs(cfg: Config) -> list[Path]:
    """The Kilo transcript stores to read: every `[approvals].transcript_dirs` entry that is a
    SQLite database file (the configurable path) plus Kilo's default store when it exists."""
    dbs: list[Path] = []
    seen: set[Path] = set()
    for base in [*_dirs(cfg), KILO_DB]:
        if base in seen:
            continue
        seen.add(base)
        if base.is_file() and _is_sqlite(base):
            dbs.append(base)
    return dbs


def _text(answer: Any) -> str:
    if isinstance(answer, str):
        return answer
    return answer[0] if isinstance(answer, list) and len(answer) == 1 else ""


def _asks(row: dict[str, Any], content: list, question: str) -> dict[str, str | None]:
    """The AskUserQuestion tool_uses of an assistant row that ask exactly ``question``."""
    out: dict[str, str | None] = {}
    for b in content:
        if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == TOOL):
            continue
        asked = (b.get("input") or {}).get("questions")
        if isinstance(asked, list) and any(
            isinstance(q, dict) and q.get("question") == question for q in asked
        ):
            out[str(b.get("id"))] = row.get("uuid")
    return out


def _accepted(row: dict[str, Any], uuid: str | None, question: str) -> str:
    """The answer text of an accepted harness result to ``question``, or ""."""
    answers = (row.get("toolUseResult") or {}).get("answers")
    ok = (
        uuid
        and (row.get("permissionDecision") or {}).get("decision") == "accept"
        and row.get("sourceToolAssistantUUID") == uuid
        and isinstance(answers, dict)
    )
    return _text(answers.get(question)) if ok else ""


def _results(row: dict[str, Any], content: list, uses: dict, question: str, name: str):
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            tid = str(b.get("tool_use_id"))
            if tid in uses and (ans := _accepted(row, uses[tid], question)):
                yield Attested(tid, ans, str(row.get("timestamp", "")), name)


def scan(path: Path, question: str, marker: str) -> list[Attested]:
    """The accepted answers to exactly ``question`` in one transcript (``marker`` is a cheap
    substring every line about it contains: the ask id)."""
    uses: dict[str, str | None] = {}  # tool_use id -> uuid of the assistant row that asked
    found: list[Attested] = []
    for line in path.read_text(errors="replace").splitlines():
        if marker not in line and not any(u in line for u in uses):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if row.get("type") == "assistant":
            uses.update(_asks(row, content, question))
        elif row.get("type") == "user":
            found.extend(_results(row, content, uses, question, path.name))
    return found


#: What the person types in their own chat: `approve|deny <request id> <first characters of the digest>`
TYPED = re.compile(r"^\s*(approve|deny)\s+(Q[0-9a-f]+)\s+([0-9a-f]+)\s*$", re.IGNORECASE)


def _human_text(row: dict[str, Any]) -> str:
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def scan_typed(path: Path, ask: dict[str, Any], min_chars: int) -> list[Attested]:
    """Answers the person TYPED in their chat: a user row the harness marked `origin.kind == human`
    whose whole text is `approve|deny <this request id> <digest prefix>`. An agent's text, a tool
    result, a notification and a quotation inside a longer message are not such rows."""
    found: list[Attested] = []
    for line in path.read_text(errors="replace").splitlines():
        if ask["id"] not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") != "user" or (row.get("origin") or {}).get("kind") != "human":
            continue
        m = TYPED.match(_human_text(row))
        if not m or m.group(2) != ask["id"] or row.get("toolUseResult"):
            continue
        prefix = m.group(3).lower()
        if len(prefix) >= min_chars and ask["digest"].startswith(prefix):
            word = "Approve" if m.group(1).lower() == "approve" else "Deny"
            found.append(
                Attested(
                    f"typed:{row.get('uuid')}",
                    f"{word} {ask['id']}",
                    str(row.get("timestamp", "")),
                    path.name,
                )
            )
    return found


def _kilo_answer(state: Any, question: str) -> str:
    """The answer text a completed Kilo `question` part holds for exactly ``question``, or ""."""
    if not isinstance(state, dict) or state.get("status") != "completed":
        return ""
    _input = state.get("input")
    _meta = state.get("metadata")
    if not isinstance(_input, dict) or not isinstance(_meta, dict):
        return ""  # a ``question`` part whose input/metadata is not a dict holds no answer
    questions = _input.get("questions")
    answers = _meta.get("answers")
    if not isinstance(questions, list) or not isinstance(answers, list):
        return ""
    for i, q in enumerate(questions):
        if isinstance(q, dict) and q.get("question") == question:
            ans = answers[i] if i < len(answers) else None
            return ans[0] if isinstance(ans, list) and len(ans) == 1 else ""
    return ""


def scan_kilo(path: Path, question: str, marker: str, since: float) -> list[Attested]:
    """The accepted answer to exactly ``question`` in one Kilo transcript store (``marker`` is
    the ask id: every question part about it contains it). Only the harness-written `question`
    tool part counts -- its `state.metadata.answers` holds the option the person picked; text
    the agent wrote is not such a part, and a T3 act never counts (its filter wants a typed
    answer, which this is not)."""
    found: list[Attested] = []
    rows = read_only_rows(
        path,
        "SELECT id, time_created, data FROM part "
        "WHERE json_extract(data, '$.type') = 'tool' AND json_extract(data, '$.tool') = ? "
        "AND time_created >= ? AND instr(data, ?) > 0 ORDER BY time_created ASC",
        (KILO_TOOL, since * 1000, marker),
    )  # no rows when this is not a readable Kilo store (wrong schema, locked, missing)
    for pid, created, raw in rows:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        ans = _kilo_answer(data.get("state"), question)
        if ans:
            found.append(Attested(f"kilo:{pid}", ans, clock.iso_at(created / 1000), path.name))
    return found


def find_all(cfg: Config, row: dict[str, Any]) -> list[Attested]:
    """Every accepted answer to this request's question, from transcripts touched since the
    request was filed (oldest first, both channels: an answer read after it was asked cannot
    sit before one read before it)."""
    since = clock.epoch(row["requested_at"], default=0.0)
    hits: list[Attested] = []
    for path in _files(cfg, since):
        hits += scan(path, row["text"], row["id"])
        hits += scan_typed(path, row, cfg.approvals.min_digest_chars)
    for db in _kilo_dbs(cfg):
        hits += scan_kilo(db, row["text"], row["id"], since)
    hits.sort(key=lambda h: clock.epoch(h.at, default=0.0))
    return hits


def find(cfg: Config, row: dict[str, Any], *, typed_only: bool = False) -> Attested | None:
    """The newest accepted answer to this request's question (``typed_only``: a typed one)."""
    hits = find_all(cfg, row)
    if typed_only:
        hits = [h for h in hits if h.tool_use_id.startswith(TYPED_PROOF)]
    return hits[-1] if hits else None


def verify(row: dict[str, Any], cfg: Config) -> bool:
    """Re-check a recorded harness answer from the transcript (the level verifier): the proof
    tool_use id must still be there, answered the way the row records (approve, +window,
    deny, a question's chosen option, or a question's free text). A later duplicate answer to
    the same question does not undo it."""
    if row.get("type") == E.QUESTION:
        if row.get("options"):
            # a multiple-choice question records the selected option as the outcome
            return any(
                h.tool_use_id == row.get("proof") and h.answer == row.get("outcome")
                for h in find_all(cfg, row)
            )
        return any(
            h.tool_use_id == row.get("proof") and h.answer == row.get("answer")
            for h in find_all(cfg, row)
        )
    return any(
        h.tool_use_id == row.get("proof") and _outcome(h.answer, row["id"]) == row.get("outcome")
        for h in find_all(cfg, row)
    )


def t3_refusal(row: dict[str, Any]) -> str:
    return (
        f"'{row['verb']}' needs level L{row['min_level']}: never approved over chat unless the "
        "operator opted in on the host ([approvals].accept_chat_for_t3 in .ddflow/local/config.toml; "
        "typed answers only, rated L2-typed)"
    )


def attest(
    log, cfg: Config, ask_id: str, *, repo: Path | None = None, now: float | None = None
) -> tuple[bool, str]:
    """Record the harness-attested answer to ``ask_id``. (ok, message); the ask engine still
    enforces expiry, digest and single answer. The standing-window choice also opens the window
    (sets `approvals.standing_window_s` and switches on the generic `approvals.policy` rules) as
    the person's own config, when ``repo`` names the repository."""
    if not cfg.approvals.accept_harness:
        return False, "the harness channel is off ([approvals].accept_harness)"
    row = E.get(fold(log.read_all(), strict=False), ask_id)
    if row is None:
        return False, f"no ask {ask_id}"
    t3 = row.get("min_level", 0) > LEVEL
    if t3 and not chat_t3(cfg):
        return False, t3_refusal(row)
    if E.status(row, now) != E.PENDING:
        return False, f"{ask_id} is {E.status(row, now)}"
    hit = find(cfg, row, typed_only=t3)
    if hit is None:
        return False, WAITING + (
            f"no answer to {ask_id} in the transcripts under {', '.join(cfg.approvals.transcript_dirs)}"
            f": put `ddflow approvals ask {ask_id}` to the person with AskUserQuestion first"
        )
    if row["type"] == E.QUESTION:
        if row["options"]:
            outcome = hit.answer  # a multiple-choice question: the selected option IS the outcome
            text = ""
        else:
            outcome = E.ANSWERED_WORD  # a free-text question: the text is the answer, not a label
            text = hit.answer
    else:
        outcome = _outcome(hit.answer, ask_id)
        if outcome is None:
            return False, f"the answer {hit.answer!r} is none of the labels for {ask_id}"
        text = ""
    actor = resolve_actor(
        cfg,
        f"harness:{Path(hit.file).stem}",
        credential_role=cfg.agent.operator_role,
        authenticated=True,
    )
    try:
        E.answer(
            log, cfg, ask_id, outcome, actor=actor, digest=row["digest"], text=text,
            via=VIA, level=LEVEL, proof=hit.tool_use_id, now=now,
        )  # fmt: skip
    except E.AskRefused as exc:
        return False, str(exc)
    tail = ""
    if hit.answer == window_label(ask_id):
        if repo is None:
            tail = " (the standing window was not written: no repository path)"
        else:
            res = CW.apply_edit(
                repo,
                CW.SetPairs(
                    (
                        ("approvals.standing_window_s", WINDOW_CHOICE_HOURS * 3600),
                        ("approvals.policy", list(WINDOW_CHOICE_POLICY)),
                    )
                ),
                layer="file",
                person=True,
            )
            if res.error:
                tail = f" (approved, but the standing window could not be enabled: {res.error})"
            else:
                tail = f"; enabled the standing window for {WINDOW_CHOICE_HOURS}h (generic policy rules on)"
    return True, f"{ask_id} {outcome} by the person who answered in {hit.file} ({hit.at}){tail}"


def attest_pending(
    log, cfg: Config, *, repo: Path | None = None, now: float | None = None
) -> list[dict[str, Any]]:
    """Auto-attest every pending request whose canonical question already has an accepted
    answer in a transcript (B3068414423). The agent couriers the canonical `ask` payload to
    the person, the person answers in chat, and ONE call here attests each answered request
    from the harness-written transcript -- a chat answer alone flips it to answered, with no
    terminal and no `approve --all`. Each result is ``{"id", "ok", "message"}`` for one
    request that `attest` considered (nothing is invented: unanswered requests come back
    ``ok: False`` with `attest`'s waiting message)."""
    state = fold(log.read_all(), strict=False)
    done: list[dict[str, Any]] = []
    for row in E.pending(state, now):
        ok, message = attest(log, cfg, row["id"], repo=repo, now=now)
        done.append({"id": row["id"], "ok": ok, "message": message})
    return done


E.LEVEL_VERIFIERS[VIA] = verify
