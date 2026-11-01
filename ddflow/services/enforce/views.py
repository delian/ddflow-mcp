"""The generated-view / rule-file / exported-document commit check and its message helpers."""

from __future__ import annotations

import shlex
from pathlib import Path

from ...config import Config
from ...core.model import fold
from ...core.outcome import FAIL, OK, Verdict
from ...infra.log import EventLog
from ...views.markdown import GENERATED_PREFIX, VIEWS, render_views, view_difference
from .. import docsync
from ..enforce_committed import committed_log
from ..export import frame as F
from ..export import ops as X
from ..export import region_check as RC
from ..export import registry as R
from ..export import write as XW
from ..export.query import ExportError
from ..guidance import ruleview as RV
from ..shared_files import doc_exclude
from .hooks import MAX_LISTED_PATHS
from .leasecheck import _commit_scope, _merged_in
from .staged import (
    _UNKNOWN_STAGED,
    _index_tree,
    _lf,
    _out_hint,
    _rel,
    _unstaged_under,
    staged_bytes,
    staged_paths,
)


def _staged_noun(staged: dict, exports: dict) -> str:
    """What the refusal calls the staged generated files."""
    return (
        "generated view or document"
        if staged and exports
        else "generated view"
        if staged
        else "generated document"
        if exports
        else "rule file"
    )


def _unrecorded_log_lines(
    repo: Path,
    log: EventLog,
    tree: Path | None,
    noun: str,
    staged: dict,
    exports: dict,
    rules: dict,
) -> tuple[list[str], bool]:
    """(why the event log a staged generated file must agree with is not fully staged --
    empty when it is --, whether git said so: False when it could not tell)."""
    probe = _unstaged_under(repo, log.dir, tree)
    if probe.failed:
        return [
            f"ddflow: a {noun} is staged, but git could not report the state of "
            f"the event log ({_rel(repo, log.dir)}) it must agree with.",
            "",
            "Refusing rather than guessing: an unreadable log state is not a clean one.",
            "Check `git status`; a locked or damaged index is the usual cause.",
        ], False
    if not probe.paths:
        return [], True
    # Each case gets the command that CLEARS it. A plain `git add` stages nothing for
    # an ignored file and exits 0, so the one remedy for every case left an ignored
    # shard refused forever with identical output (roborev on 8b167e9, reproduced).
    remedy = []
    if probe.ignored:
        remedy += [
            "These are GITIGNORED, so a plain `git add` skips them. Force them in, or",
            "stop ignoring shards (a committed log with some shards ignored cannot",
            "match a view rendered from all of them):",
            "    git add -f " + " ".join(shlex.quote(p) for p in probe.ignored),
        ]
    if set(probe.paths) - set(probe.ignored):
        remedy += [f"    git add {shlex.quote(_rel(repo, log.dir))}"]
    return [
        f"ddflow: a {noun} is staged, but the event log it is rendered from "
        f"has {len(probe.paths)} change(s) the commit does not record:",
        "",
        *(
            f"  {p}" + ("   (gitignored)" if p in probe.ignored else "")
            for p in probe.paths[:MAX_LISTED_PATHS]
        ),
        "",
        "A committed view must agree with the log committed beside it. Stage both:",
        *remedy,
        *(["    ddflow render" + _out_hint(sorted(staged))] if staged else []),
        *(
            [f"    ddflow export {d} --update" for d in sorted({d for d, _ in exports.values()})]
            if exports
            else []
        ),
        "    git add " + " ".join(shlex.quote(p) for p in sorted({**staged, **exports, **rules})),
    ], True


def _committed_log_if_unrecorded(
    repo: Path, log: EventLog, tree: Path | None, exports: dict
) -> EventLog | None:
    """The log as the commit's INDEX holds it, to check the staged files against instead
    of the shards on disk -- or None when it cannot be used (the caller then refuses).

    The shards on disk carry what other agents append while this commit is made; those
    events are not part of the commit and cannot make a view disagree with the log
    committed beside it (bug B10725b2c5e). An exported document is regenerated from the
    live log by `ddflow export` itself, so a commit staging one keeps the old refusal
    (DX-export-committed-log)."""
    if exports:
        return None
    return committed_log(tree or _index_tree(repo), _rel(repo, log.dir), log.dir)  # type: ignore[return-value]


def _disagreements(
    repo: Path, log: EventLog, fresh_cfg: Config, staged: dict, exports: dict, rules: dict
) -> tuple[list[str], list[str]]:
    """(error lines, disagreement lines) of the staged rule files, views and exports
    against what the log regenerates now. An export error ends the check at once."""
    lines: list[str] = []
    if rules:
        lines += _wrong_rule_files(repo, log, fresh_cfg, rules)
    if staged:
        lines += [*([""] if lines else []), *_wrong_views(log, fresh_cfg, staged)]
    if exports:
        err, bad = _wrong_exports(repo, fresh_cfg, exports)
        if err:
            return [*lines, *([""] if lines else []), err], []
        if bad:
            lines += [
                *([""] if lines else []),
                f"ddflow: {len(bad)} staged exported document(s) differ from what "
                "`ddflow export` regenerates now:",
                "",
                *(f"  {p}  ({why})" for p, _d, why in bad),
                "",
                "An exported document is regenerated from the log, never edited: either it was",
                "changed by hand, or the log moved after it was exported. Regenerate and stage:",
                *(f"    ddflow export {d} --update" for d in sorted({d for _p, d, _w in bad})),
                "    git add " + " ".join(shlex.quote(p) for p, _d, _w in bad),
            ]
    return [], lines


def check_views(repo: Path, cfg: Config | None = None, *, agent: str = "") -> Verdict:
    """(exit_code, message) for B18: a staged generated view must be byte-identical to
    what the log regenerates now.

    `docs/ddflow/` is SELF_MANAGED, so the lease check waves views through -- rightly,
    since rendering needs no claim -- and until this nothing checked them at all. A view
    that disagrees with the log is worse than none: it is the page people read INSTEAD
    of the log. It goes wrong two ways, both caught here: hand-edited, or stale because
    the queue moved after `ddflow render`.

    A file counts as a view when its name is in `VIEWS` AND its staged content carries
    the GENERATED marker -- so `render --out elsewhere` is still checked, while a
    project's own `QUEUE.md`, or a document that merely quotes the marker, is not.
    Fires only when a view is STAGED -- or when git cannot report the staged set at all,
    since "could not tell whether a view is staged" is not "no view is staged". A commit
    that stages no view is never blocked because the queue moved, which would teach
    everyone to bypass the hook.

    The same rule covers an EXPORTED document (B-export-enforce, D-export): a staged file
    whose first line is a `ddflow:generated doc=<kind>` header of a known kind must be
    what a fresh `ddflow export` of that kind renders (the header's ddflow version is not
    compared; the body and kind are). A hand-written file that only holds a marker REGION
    has no such first line, so it is not a generated file and is left to `export --check`;
    an append-mode log (`last=` in its header) grows by design and is skipped.
    """

    cfg = cfg or Config.load(repo)
    mode = cfg.enforce.generated_views
    if mode == "off":
        return Verdict(OK, "")
    names = {name for name, _ in VIEWS}
    # Resolved once: the same tree for the listing, each staged view's bytes and the log.
    tree = _index_tree(repo)
    listed = staged_paths(repo, tree=tree)
    if listed is None:
        return _verdict(mode, [_UNKNOWN_STAGED])
    listed = _commit_scope(tree, listed, cfg)
    configured = {p for _d, p, _m in cfg.export.targets()}
    staged, exports = _staged_generated(repo, listed, names, tree, configured)
    rules = _staged_rule_files(repo, listed, tree)
    regions = _edited_regions(repo, listed, tree, configured)
    if regions:
        return _verdict(mode, regions)
    if not staged and not exports and not rules:
        return Verdict(OK, "")
    noun = _staged_noun(staged, exports)

    log = EventLog(repo, agent or cfg.agent.id or "", log_cfg=cfg.log)
    # The view is committed WITH a log, and must agree with THAT log -- not with the
    # one on disk. Staging a view rendered from events that are not themselves staged
    # would commit a page describing work its own commit does not record (roborev on
    # 7216f5e, reproduced). When the shards on disk are all staged, the log on disk IS
    # the committed log. When some are not (another agent appending), the staged files
    # are compared with the shards as the INDEX holds them; a view that disagrees with
    # those, or an index git cannot read, is refused.
    probe_lines, probed = _unrecorded_log_lines(repo, log, tree, noun, staged, exports, rules)
    # Config from the FILES, as `ddflow render` writes with: env overrides belong to
    # whoever typed `git commit`, not to the view.
    fresh_cfg = Config.load(repo, env={})
    if probe_lines:
        # Shards the commit does not record: events another agent is writing right now
        # are no reason to refuse, so compare with the log as committed. A view that
        # disagrees with THAT (rendered from events not staged) is refused as before.
        committed = _committed_log_if_unrecorded(repo, log, tree, exports)
        if committed is None or not probed:
            return _verdict(mode, probe_lines)
        err, lines = _disagreements(repo, committed, fresh_cfg, staged, exports, rules)
        return _verdict(mode, probe_lines) if err or lines else Verdict(OK, "")
    err, lines = _disagreements(repo, log, fresh_cfg, staged, exports, rules)
    if err:
        return _verdict(mode, err)
    if not lines:
        return Verdict(OK, "")
    return _verdict(mode, lines)


def _staged_generated(
    repo: Path, listed: list[str], view_names: set[str], tree: Path, configured: set[str]
) -> tuple[dict[str, bytes], dict[str, tuple[str, str]]]:
    """The staged views ``{path: bytes}`` and exported documents ``{path: (kind, text)}``."""

    staged: dict[str, bytes] = {}
    exports: dict[str, tuple[str, str]] = {}
    for p in listed:
        if Path(p).name not in view_names:
            found = _staged_export(repo, p, tree, p in configured)
            if found:
                exports[p] = found
            continue
        data = staged_bytes(repo, p, tree=tree)
        if data is not None and data.startswith(GENERATED_PREFIX.encode("utf-8")):
            staged[p] = data
    return staged, exports


def _edited_regions(repo: Path, listed: list[str], tree: Path, configured: set[str]) -> list[str]:
    """Message lines for staged files holding a ddflow region whose body no longer matches
    the digest in its begin marker (B-tpl-refresh): empty when none. The check needs no
    log, only the staged text; a file whose markers cannot be read is named too."""
    bad: list[tuple[str, str]] = []
    for p in listed:
        if p not in configured and not p.lower().endswith((".md", ".markdown", ".txt")):
            continue
        data = staged_bytes(repo, p, tree=tree)
        if data is None or b"ddflow:begin" not in data:
            continue
        text = _lf(data).decode("utf-8", errors="replace")
        why = RC.problem(text)
        if why:
            bad.append((p, why))
        bad += [(p, f"region {name} was edited by hand") for name in RC.edited(text)]
    if not bad:
        return []
    return [
        f"ddflow: {len(bad)} staged ddflow region(s) do not match their recorded digest:",
        "",
        *(f"  {p}  ({why})" for p, why in bad),
        "",
        "A region is regenerated from its template, never edited. Regenerate and stage:",
        "    ddflow export <doc> --update",
        "    git add " + " ".join(shlex.quote(p) for p in sorted({p for p, _ in bad})),
    ]


def _staged_rule_files(repo: Path, listed: list[str], tree: Path) -> dict[str, bytes]:
    """The staged rule files ``{path: bytes}``: ``.ddflow/rules/<id>.toml``, which the log
    renders (D-unify 7). A deletion is not listed (`staged_paths` is ACMR)."""
    prefix = RV.files(repo).directory.relative_to(repo).as_posix() + "/"
    found: dict[str, bytes] = {}
    for p in listed:
        if p.startswith(prefix) and p.endswith(".toml") and "/" not in p[len(prefix) :]:
            data = staged_bytes(repo, p, tree=tree)
            if data is not None:
                found[p] = data
    return found


def _wrong_rule_files(
    repo: Path, log: EventLog, cfg: Config, staged: dict[str, bytes]
) -> list[str]:
    """Message lines for the staged rule files the log does not say (empty: all fine): a file
    with no record, or whose content differs from its live record -- a hand edit, which
    ``ddflow rule sync`` records. A retired rule and a file that does not load are not
    this check's (``doctor`` reports the latter)."""
    st = fold(log.read_all(), strict=False)
    wrong: list[tuple[str, str]] = []
    for p in sorted(staged):
        why = RV.disagreement(cfg, st, Path(p).stem, staged[p].decode("utf-8", errors="replace"))
        if why:
            wrong.append((p, why))
    if not wrong:
        return []
    return [
        f"ddflow: {len(wrong)} staged rule file(s) are not what the event log says:",
        "",
        *(f"  {p}  ({why})" for p, why in wrong),
        "",
        "A rule file is a view of the rule's record in the log. Record the edit and stage",
        "the log with it:",
        "    ddflow rule sync",
        "    git add " + " ".join(shlex.quote(p) for p, _w in wrong) + " " + _rel(repo, log.dir),
    ]


def _wrong_views(log: EventLog, cfg: Config, staged: dict[str, bytes]) -> list[str]:
    """Message lines for the staged views that differ from a fresh render (empty: all fine).

    The BODY and what it says about itself are compared, not the bytes of the first line
    (D-compat-json-views): a view stamped by another release, or not stamped at all, is
    fine while its body is what the log renders now."""

    want = render_views(fold(log.read_all(), strict=False), cfg)
    wrong: list[tuple[str, str]] = []
    for p in sorted(staged):
        try:
            have = _lf(staged[p]).decode("utf-8")
        except UnicodeDecodeError:
            wrong.append((p, "not valid UTF-8"))
            continue
        why = view_difference(have, want[Path(p).name])
        if why:
            wrong.append((p, why))
    if not wrong:
        return []
    labels = {
        "stale": "",
        "edited": "edited by hand",
        "newer": "written at a newer format level",
    }
    paths = [p for p, w in wrong if w != "newer"]
    newer = len(paths) != len(wrong)
    return [
        f"ddflow: {len(wrong)} staged generated view(s) differ from what the event log "
        "regenerates now:",
        "",
        *(f"  {p}" + (f"  ({labels.get(w, w)})" if labels.get(w, w) else "") for p, w in wrong),
        "",
        *(
            [
                "A view is regenerated from the log, never edited: either it was changed by hand,",
                "or the queue moved after it was rendered. Regenerate it and stage the result:",
                "    ddflow render" + _out_hint(paths),
                "    git add " + " ".join(shlex.quote(p) for p in paths),
            ]
            if paths
            else []
        ),
        *(
            [
                "A view written at a newer format level is not regenerated by this ddflow:",
                "upgrade ddflow first.",
            ]
            if newer
            else []
        ),
    ]


def _staged_export(
    repo: Path, path: str, tree: Path, configured: bool = False
) -> tuple[str, str] | None:
    """``(doc kind, staged text)`` when the INDEX copy of ``path`` is a generated export
    document of a known kind (its FIRST line is the `ddflow:generated` header), else None.

    Only text-ish names (and any CONFIGURED target, whatever its suffix) are read: every
    staged file would otherwise cost a `git show`.
    A file that merely quotes the marker further down, or holds a marker region, is not one.
    """

    if not configured and not path.lower().endswith((".md", ".markdown", ".txt")):
        return None
    data = staged_bytes(repo, path, tree=tree)
    if data is None or not data.startswith(b"<!-- ddflow:generated doc="):
        return None
    try:
        text = _lf(data).decode("utf-8")
    except UnicodeDecodeError:
        return None
    head, _body = F.split(text)
    if head is None or "last" in head.extra or head.doc not in R.names():
        return None
    return head.doc, text


def _wrong_exports(
    repo: Path, cfg: Config, exports: dict[str, tuple[str, str]]
) -> tuple[str, list[tuple[str, str, str]]]:
    """``(error, [(path, doc, why)])``: the staged exports that are not a fresh export.

    ``error`` is non-empty when the comparison could not run (unreadable log, a template
    failure): that is reported, and under 'block' refuses, never read as fresh.
    """

    bad: list[tuple[str, str, str]] = []
    try:
        q = X.load(repo, cfg)
        for p in sorted(exports):
            doc, text = exports[p]
            _head, body = F.split(text)
            why = ""
            if F.hand_edited(text):
                why = "hand-edited: its body no longer matches its digest"
            try:
                XW.safe_target(repo, p)
                spec = X.spec_for(cfg, doc, path=p, mode="whole")
                fresh = X.render(repo, cfg, q, spec, max_bytes=0)
            except ExportError as exc:
                if exc.code == X.EXIT_UNAVAILABLE:
                    raise
                bad.append((p, doc, f"cannot be regenerated: {exc}"))
                continue
            _fh, fresh_body = F.split(fresh)
            if not why and fresh_body != body:
                why = "stale: the log moved since it was exported"
            if why:
                bad.append((p, doc, why))
    except ExportError as exc:
        return (
            "ddflow: a staged exported document could not be compared with a fresh export "
            f"({exc}). An unchecked document is not a clean one.",
            [],
        )
    return "", bad


def _verdict(mode: str, lines: list[str], knob: str = "generated_views") -> Verdict:
    msg = "\n".join([*lines, "", f'Policy is [enforce].{knob} = "{mode}" in .ddflow/config.toml.'])
    if mode == "warn":
        return Verdict(OK, msg + '\n\n(warning only; set the policy to "block" to refuse)')
    return Verdict(FAIL, msg)


def check_docs(repo: Path, cfg: Config | None = None) -> Verdict:
    """(exit_code, message) for B17: a commit that removes or renames an identifier, a
    file or a default must not leave a doc line naming the old one.

    The detection is `services/docsync.py`; this is only the policy around it, the same
    shape as `check_views`. "Git could not tell" is never a pass: it reports, and under
    'block' refuses, exactly like an unreadable staged set.
    """

    cfg = cfg or Config.load(repo)
    mode = cfg.enforce.stale_docs
    if mode == "off":
        return Verdict(OK, "")
    # The committing tree: the diff is this commit's, against that tree's own HEAD.

    tree = _index_tree(repo)
    diff = None
    if _merged_in(tree):
        # A merge removes what the MERGED PARENT removed; only the paths the branch itself
        # changed are this commit's to answer for.
        listed = staged_paths(repo, tree=tree)
        own = None if listed is None else _commit_scope(tree, listed, cfg)
        if own is not None and own != listed:
            if not own:
                return Verdict(OK, "")
            diff = docsync.staged_diff(tree, own)
    hits = docsync.stale_mentions(tree, cfg.enforce.doc_globs, doc_exclude(cfg), diff=diff)
    if hits is None:
        return _verdict(
            mode,
            [
                "ddflow: git could not report this commit's diff, or search the docs it may",
                "have left stale, so the doc-sync check could not run.",
                "",
                "An unchecked commit is not a clean one: under 'block' this refuses, and",
                "under 'warn' it is reported here rather than passed in silence.",
            ],
            "stale_docs",
        )
    if not hits:
        return Verdict(OK, "")
    names = sorted({h.name for h in hits})
    return _verdict(
        mode,
        [
            f"ddflow: this commit removes {len(names)} name(s) that {len(hits)} doc line(s) "
            "still mention:",
            "",
            *(f"  {h.path}:{h.line}  {h.name}" for h in hits[:MAX_LISTED_PATHS]),
            *(
                [f"  ... and {len(hits) - MAX_LISTED_PATHS} more"]
                if len(hits) > MAX_LISTED_PATHS
                else []
            ),
            "",
            "A page naming a removed identifier or an old default is read and trusted.",
            "Update those lines in this commit. A page whose job is to remember old names",
            "(a changelog, a backlog) belongs in [enforce].doc_exclude.",
        ],
        "stale_docs",
    )
