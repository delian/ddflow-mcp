"""Reviewer management: list, add, detect, and the per-run reply file."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...config import family_for
from ...core import clock
from ...core import outcome as O
from ...core.digest import content_digest
from ...core.slug import safe_filename
from ...infra import tomlcfg as TC
from ...services import review as RV
from ...services import review_keys
from ...services import modelreg
from ...services.configwrite import Block, KeyRefused, ReviewerRefusal, apply_edit
from ...views import human
from .._base import _load


def reviewers_list(repo: Path, *, agent: str = "") -> O.Outcome:
    """Every configured reviewer, and which of them cannot satisfy the family rule."""

    _log, _cfg, _st = _load(repo, agent)
    revs = RV.load_reviewers(repo)
    rows = [
        {
            "name": r.name,
            "family": r.resolved_family(),
            "gates": list(r.gates),
            "enabled": r.enabled,
            "base_url": r.base_url,
            "model": r.model,
            "key": review_keys.status(r.api_key_env, r.api_key_file),
        }
        for r in revs
    ]
    # Not cosmetic: an unclassified reviewer cannot satisfy the different-family
    # requirement, so `complete` refuses and the reason looks like it is about the review
    # rather than about a missing `family = "..."` line.
    unclassified = [r["name"] for r in rows if not r["family"] and r["enabled"]]

    # Names only: which enabled reviewer has neither its variable nor a readable key file.
    no_key = [
        r["name"] for r in rows if r["enabled"] and r["key"].startswith(("missing", "UNAVAILABLE"))
    ]
    data: dict[str, Any] = {"reviewers": rows, "unclassified": unclassified, "no_key": no_key}
    if not rows:
        empty = "No reviewers configured. Run `ddflow reviewers detect --write`."
        return O.nothing("reviewers.list", empty, text=empty, **data)
    out = O.ok("reviewers.list", text="", **data)
    out.data["text"] = human.render(out)
    return out


def reviewer_block(entry: dict[str, object]) -> str:
    """One `[[reviewer]]` block, every value written by `tomlcfg.value`."""
    return "\n[[reviewer]]\n" + "".join(f"{k} = {TC.value(v)}\n" for k, v in entry.items())


def reviewers_add(
    repo: Path,
    *,
    preset: str = "",
    name: str = "",
    model: str = "",
    base_url: str = "",
    gates: Sequence[str] = (),
    no_launch: bool = False,
    shared: bool = False,
    person: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Append a `[[reviewer]]` block. Writes the KEY's variable NAME, never the key.

    Local unless ``shared``: an endpoint and a key variable are one operator's setup, and a
    reviewer added here is for THIS machine (bug B-reviewers-write-committed). A command
    reviewer is written only for a ``person`` (decision D-reviewer-trust)."""
    entry = dict(RV.PRESETS.get(preset, {}))
    if preset and not entry:
        # A route the operator declared in `.ddflow/local/models.toml` (e.g. a self-hosted
        # SW-local-endpoint) is a preset too: the shipped PRESETS come from the catalog's
        # shipped routes; the machine's own endpoints arrive here, never committed.
        entry = dict(modelreg.presets(modelreg.load(local_dir=repo / ".ddflow" / "local")).get(preset, {}))
    if preset and not entry:
        return O.failed(
            "reviewers.add", f"unknown preset {preset!r}; `ddflow reviewers presets`", name=name
        )
    if model:
        entry["model"] = model
    if base_url:
        entry["base_url"] = base_url
    if gates:
        entry["gates"] = list(gates)
    label = name or preset or str(entry.get("model", "reviewer"))
    # No `family` guessed from the model name: a written one wins over the project's
    # `[agent].families` for good (B98650136a8). A preset that declares one keeps it.
    if no_launch:
        entry.pop("launch", None)
    launch = entry.pop("launch", None)
    body = {
        "name": label,
        **{k: v for k, v in entry.items() if k != "name"},
        **({"launch": launch} if launch else {}),
    }
    res = apply_edit(
        repo,
        Block(reviewer_block(body), own="reviewers.toml"),
        layer="file" if shared else "local",
        person=person,
        agent=agent,
    )
    if res.error:
        done = O.refused if isinstance(res.error, (KeyRefused, ReviewerRefusal)) else O.failed
        return done(
            "reviewers.add",
            res.error,
            name=label,
            reviewer_refused=isinstance(res.error, ReviewerRefusal),
        )
    note = (
        "\n  Committed config: every clone gets this reviewer."
        if shared
        else "\n  Git-ignored, machine-local: not committed. --shared commits a reviewer "
        "every clone should use."
    )
    if entry.get("api_key_env"):
        note += (
            f"\n  Set ${entry['api_key_env']} in your environment. The KEY is never "
            f"written to any config — only the variable's name."
        )
    return O.ok(
        "reviewers.add",
        name=label,
        path=str(res.path),
        shared=shared,
        text=f"added reviewer {label!r} to {res.path}{note}",
    )


def reviewers_detect(
    repo: Path, *, write: bool = False, shared: bool = False, agent: str = ""
) -> O.Outcome:
    """Probe well-known local ports for an OpenAI-compatible endpoint.

    `write` records what answered in the git-ignored `.ddflow/local/reviewers.toml`:
    an endpoint on this machine is this machine's, and committing it hands every clone
    an address that does not exist there (bug B-reviewers-write-committed). `shared`
    commits it to `.ddflow/config.toml` instead, deliberately.
    """

    _log, cfg, _st = _load(repo, agent)
    found = RV.detect()
    if not found:
        none = (
            "No local OpenAI-compatible endpoint answered on any well-known port.\n"
            "Checked: " + ", ".join(u for u, _ in RV.WELL_KNOWN_ENDPOINTS)
        )
        return O.nothing("reviewers.detect", none, found=[], blocks="", written="", text=none)
    rows, blocks = [], []
    for url, label, models in found:
        for m in models:
            # Shown as the block written here will resolve once loaded (it declares no
            # family, so `load_reviewers` then `Reviewer.resolved_family`: the project's
            # map, else the shipped one; a reviewer configured elsewhere with its own
            # `family =` keeps that) -- and NOT written into the block: a written `family`
            # wins over `[agent].families` for good, and here it would only be a guess
            # (B98650136a8).
            fam = family_for(m, cfg.agent.families) or family_for(m)
            rows.append({"url": url, "label": label, "model": m, "family": fam})
            # `reviewer_block` writes through tomlcfg.value, not "{m}": a model name is
            # whatever the endpoint reported, and a quote in it broke the block (Bb11e7a8186).
            blocks.append(
                reviewer_block(
                    {
                        "name": m.split("/")[-1].lower(),
                        "base_url": url,
                        "model": m,
                        "gates": ["critic"],
                    }
                )
            )
    written = ""
    if write:
        res = apply_edit(
            repo,
            Block("".join(blocks), own="reviewers.toml"),
            layer="file" if shared else "local",
            agent=agent,
        )
        if res.error:
            done = O.refused if isinstance(res.error, (KeyRefused, ReviewerRefusal)) else O.failed
            return done(
                "reviewers.detect",
                res.error,
                found=rows,
                blocks="".join(blocks),
                written="",
                text="",
            )
        written = str(res.path)

    out = O.ok(
        "reviewers.detect",
        found=rows,
        blocks="".join(blocks),
        written=written,
        count=len(rows),
        text="",
    )
    out.data["text"] = human.render(out)
    return out


class _ReplyFile:
    """Every chunk's whole reply, appended to ``.ddflow/local/reviews/<item>.<gate>.jsonl``
    as it ARRIVES (bug B206): a review can outlive its caller (the MCP client gave up at
    1800 s on a 2055 s critic), and a finding body that lives only in the tool response
    is then lost. The gate evidence names the file and its digest. The name is scoped to
    THIS run and reviewer (time and a random token): a re-review, or a retry of an aborted call running at the
    same time, must not truncate the file an earlier record's digest refers to."""

    def __init__(self, repo: Path, item: str, gate: str, reviewer: str) -> None:

        run = clock.run_stamp()
        who = safe_filename(reviewer, repl="_")
        self.repo = repo
        self.item = item
        self.gate = gate
        self.reviewer = reviewer
        self.path = repo / ".ddflow" / "local" / "reviews" / f"{item}.{gate}.{who}.{run}.jsonl"
        self._lock = threading.Lock()
        self._started = False
        _ACTIVE.append(self)

    def plan(
        self, diff_sha: str, chunks_total: int, max_chunk_chars: int, wanted: list[int]
    ) -> None:
        """Checkpoint the cut's identity the moment it is known, so a killed review's record
        can RESUME from the next chunk instead of re-running every chunk (bug Bd8c4e1d8d6).
        A chunk number means nothing across two cuts, so a resume must name the same diff,
        the same chunk size, the same reviewer and the same ``wanted`` subset -- this one line
        carries all five."""
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "plan": {
                                    "reviewer": self.reviewer,
                                    "diff_sha": diff_sha,
                                    "chunks_total": chunks_total,
                                    "max_chunk_chars": max_chunk_chars,
                                    "wanted": wanted,
                                }
                            }
                        )
                        + "\n"
                    )
                self._started = True
            except OSError:
                pass

    def plan_meta(self) -> dict[str, Any]:
        """The ``plan`` line this run checkpointed, or ``{}`` when it never got one."""
        try:
            lines = self.path.read_text("utf-8").splitlines()
        except OSError:
            return {}
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if "plan" in row:
                return row["plan"]
        return {}

    def add(self, chunk: int, reply: str) -> None:

        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"chunk": chunk, "reply": reply}) + "\n")
                self._started = True
            except OSError:
                pass  # the evidence still carries the bodies; this is the belt

    def chunks(self) -> list[tuple[int, str]]:
        """The (chunk, reply) pairs this run has checkpointed so far, in arrival order.

        Line-by-line, not whole-file: a kill can leave a truncated trailing line, and one
        torn line must not discard the earlier checkpointed chunks it follows."""
        rows: list[tuple[int, str]] = []
        try:
            lines = self.path.read_text("utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                rows.append((int(row["chunk"]), row["reply"]))
            except (ValueError, KeyError, TypeError):
                continue  # a torn trailing line loses only itself
        return rows

    def findings(self) -> list[dict[str, Any]]:
        """Every finding already checkpointed here, as ``chunk_findings`` dicts.

        Chunk number and digest are set exactly as ``ReviewResult.evidence`` sets them, so a
        killed review's partial findings merge with a later round unchanged."""
        out: list[dict[str, Any]] = []
        for chunk, reply in self.chunks():
            for f in RV.parse(reply)[0]:
                f.chunk = chunk
                f.digest = RV.finding_digest(f.severity, f.location, f.title, f.detail)
                out.append(
                    {
                        "chunk": chunk,
                        "digest": f.digest,
                        "severity": f.severity,
                        "title": f.title,
                        "location": f.location,
                        "detail": f.detail[: RV.FINDING_DETAIL_KEPT],
                    }
                )
        return out

    def evidence(self) -> dict[str, Any]:

        if not self._started:
            return {}
        try:
            digest = content_digest(self.path.read_bytes(), length=16)
        except OSError:
            return {}
        # Repo-relative: the event log is committed, an absolute path is one machine's.
        return {"output_file": str(self.path.relative_to(self.repo)), "output_digest": digest}


#: The reply files of the review(s) running in THIS process, in creation order. A review
#: runs in one process -- the CLI, or the MCP worker a review tool is offloaded to -- so a
#: process-global list is enough; `review()` clears it (`_begin_run`) at entry, so
#: `_terminated` (bug B982d306cc8) checkpoints only the run that is being killed, never a
#: sibling or an earlier completed review's files.
_ACTIVE: list[_ReplyFile] = []


def _begin_run() -> None:
    """Forget every reply file of prior runs in this process: the next run starts fresh."""
    _ACTIVE.clear()


def active_files(item: str, gate: str) -> list[_ReplyFile]:
    """The reply files of THIS process's run for ``item.gate`` that already have a chunk:
    the checkpoint a killed review reads back (bug B982d306cc8)."""
    return [f for f in _ACTIVE if f.item == item and f.gate == gate and f.chunks()]
