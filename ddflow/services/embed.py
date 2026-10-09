"""The Embedder: the one way ddflow turns texts into vectors (D-unify 2, B-uni-context-pack.3).

Every semantic feature (recall, rule and skill injection, the docs RAG) consumes THIS
interface; there is no second backend. There are two ways to be one, tried in order:

1. **A companion command**, `[rag].command`, run like every operator command
   (`services/cmdrunner.py`: a shell line, in the repository root, bounded by `[rag].timeout_s`).
   It reads one JSON object on stdin,
   ``{"texts": ["...", ...]}``, and prints one on stdout,
   ``{"model": "<model id>", "vectors": [[0.1, ...], ...]}``: one vector per text, in order,
   all the same length, every number finite. `model` names the model that made the
   vectors, so stored vectors are never compared with another model's.
2. **model2vec in process**, `[rag].model_dir`, when the ``ddflow[rag]`` extra is installed.
   The directory is local: a model is never downloaded at runtime.

Anything else -- nothing configured, the extra absent, a path that is not a directory, a
timeout, a non-zero exit, output that breaks the contract -- is `unavailable` with the
reason, never a partial answer and never a silent downgrade. `rerank` is the fallback in
one place: when the embedder is unavailable the BM25 order is kept and the answer SAYS so.

`model2vec` is imported here and nowhere else (the `extras-one-adapter` contract).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from .cmdrunner import CommandRunner, Declared, executable_missing

try:  # the [rag] extra; every caller works without it
    from model2vec import StaticModel as _StaticModel
except ImportError:  # pragma: no cover - depends on the environment
    _StaticModel = None

COMPANION = "companion"
MODEL2VEC = "model2vec"

#: How `rerank` names the order it returns.
SEMANTIC = "semantic"
BM25 = "bm25"


@dataclass(frozen=True)
class Embedding:
    """The answer to one `embed` call: vectors, or why there are none."""

    ok: bool
    vectors: tuple[tuple[float, ...], ...] = ()
    #: The model id the vectors came from (the companion's own, or the directory's name).
    model: str = ""
    backend: str = ""
    #: Why `ok` is False; "" otherwise.
    reason: str = ""

    @property
    def unavailable(self) -> bool:
        return not self.ok


def _unavailable(reason: str, backend: str = "") -> Embedding:
    return Embedding(False, backend=backend, reason=reason)


def extra_installed() -> bool:
    """Is the ``[rag]`` extra's embedding half (model2vec) installed?"""
    return _StaticModel is not None


def backend(cfg: Config) -> str:
    """Which backend `[rag]` selects: the companion, model2vec, or "" for none."""
    if cfg.rag.command.strip():
        return COMPANION
    if cfg.rag.model_dir.strip():
        return MODEL2VEC
    return ""


def embed(repo: Path, cfg: Config, texts: Sequence[str]) -> Embedding:
    """Embed `texts`, in order. Never raises for the backend's own trouble."""
    which = backend(cfg)
    if not which:
        return _unavailable("no embedder configured: set [rag].command or [rag].model_dir")
    if not texts:
        return Embedding(True, (), "", which)
    if which == COMPANION:
        return _companion(repo, cfg, list(texts))
    return _model2vec(cfg, list(texts))


def _companion(repo: Path, cfg: Config, texts: list[str]) -> Embedding:
    run = CommandRunner().run(
        Declared(cfg.rag.command, "rag.command"),
        cwd=repo,
        timeout_s=cfg.rag.timeout_s,
        stdin_text=json.dumps({"texts": texts}),
    )
    if not run.ran:
        return _unavailable(f"the embedder is unavailable: {run.reason}", COMPANION)
    if run.code != 0:
        tail = run.err.strip().splitlines()[-1:] or [""]
        return _unavailable(f"the embedder exited {run.code}: {tail[0][:200]}", COMPANION)
    return _parse(run.out, len(texts))


def _parse(stdout: str, want: int) -> Embedding:
    """The companion's output, held to the contract."""
    try:
        body = json.loads(stdout)
    except ValueError:
        return _unavailable("the embedder did not print JSON", COMPANION)
    if not isinstance(body, dict):
        return _unavailable("the embedder's JSON is not an object", COMPANION)
    model, raw = body.get("model"), body.get("vectors")
    if not isinstance(model, str) or not model:
        return _unavailable("the embedder named no model id", COMPANION)
    if not isinstance(raw, list) or len(raw) != want:
        got = len(raw) if isinstance(raw, list) else "none"
        return _unavailable(f"the embedder returned {got} vectors for {want} texts", COMPANION)
    return _checked([_vector(v) for v in raw], model, COMPANION)


def _checked(rows: list[tuple[float, ...] | None], model: str, backend_: str) -> Embedding:
    """Vectors held to the one contract, whichever backend made them: finite numbers, not
    empty, all the same length."""
    if any(r is None for r in rows):
        return _unavailable("the embedder returned a vector that is not finite numbers", backend_)
    vectors = tuple(r for r in rows if r is not None)
    if len({len(v) for v in vectors}) != 1 or not vectors[0]:
        return _unavailable("the embedder's vectors are empty or of different lengths", backend_)
    return Embedding(True, vectors, model, backend_)


def _vector(raw: Any) -> tuple[float, ...] | None:
    if not isinstance(raw, list):
        return None
    out: list[float] = []
    for x in raw:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        try:
            value = float(x)  # an integer too large for a float is OverflowError, not a number
        except OverflowError:
            return None
        if not math.isfinite(value):
            return None
        out.append(value)
    return tuple(out)


def _model2vec(cfg: Config, texts: list[str]) -> Embedding:
    if _StaticModel is None:
        return _unavailable("the ddflow[rag] extra (model2vec) is not installed", MODEL2VEC)
    path = Path(cfg.rag.model_dir).expanduser()
    if not path.is_dir():
        # from_pretrained would otherwise treat the path as a hub name and download it.
        return _unavailable(
            f"[rag].model_dir {cfg.rag.model_dir!r} is not a directory (a model is never downloaded)",
            MODEL2VEC,
        )
    try:
        encoded = _StaticModel.from_pretrained(str(path)).encode(texts)
        rows = [_vector([float(x) for x in row]) for row in encoded]
    except Exception as exc:  # the model's own trouble: report it, do not crash a recall
        return _unavailable(f"model2vec failed: {exc}", MODEL2VEC)
    # The resolved path, not the directory's name: two models may share a name.
    return _checked(rows, str(path.resolve()), MODEL2VEC)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, 0.0 when either vector has no length."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass(frozen=True)
class Ranked:
    """An order over candidates, and which kind of order it is."""

    #: Indexes into the candidates, best first.
    order: tuple[int, ...]
    mode: str
    #: What a reader should be told when the order is not the semantic one; "" otherwise.
    note: str = ""
    model: str = ""


def rerank(repo: Path, cfg: Config, query: str, texts: Sequence[str]) -> Ranked:
    """Order `texts` (given in BM25 order) by closeness to `query`, or keep the BM25 order.

    Semantic when the embedder answers; otherwise the BM25 order stays and `note` says
    that and why. Ties keep the BM25 order, so the same inputs always give the same order.
    """
    keep = tuple(range(len(texts)))
    if not texts:
        return Ranked(keep, BM25)
    got = embed(repo, cfg, [query, *texts])
    if got.unavailable:
        return Ranked(keep, BM25, f"semantic ranking unavailable ({got.reason}); BM25 order kept")
    q, rest = got.vectors[0], got.vectors[1:]
    scored = sorted(keep, key=lambda i: (-cosine(q, rest[i]), i))
    return Ranked(tuple(scored), SEMANTIC, "", got.model)


def doctor_notes(cfg: Config) -> list[str]:
    """The doctor line for the ``[rag]`` extra: what is configured and whether it can run.

    It never runs the companion (that spawns a process); `embed` finds out for real.
    """
    which = backend(cfg)
    if which == COMPANION:
        if missing := executable_missing(cfg.rag.command):
            return [
                f"embedder: [rag].command runs {missing!r}, which is not installed; "
                "recall is BM25 only"
            ]
        return ["embedder: a companion command is configured (doctor does not run it)"]
    if which == MODEL2VEC:
        if not extra_installed():
            return [
                "embedder: [rag].model_dir is set but the ddflow[rag] extra (model2vec) is "
                "not installed; recall is BM25 only"
            ]
        if not Path(cfg.rag.model_dir).expanduser().is_dir():
            return [f"embedder: [rag].model_dir {cfg.rag.model_dir!r} is not a directory"]
        return [f"embedder: model2vec model at {cfg.rag.model_dir!r} configured"]
    extra = "installed" if extra_installed() else "not installed"
    return [
        f"embedder: none configured (ddflow[rag] extra {extra}); semantic ranking is off, "
        "recall is BM25 only"
    ]
