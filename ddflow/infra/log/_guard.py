"""The version stamp and skew-guard half of ``EventLog`` (``_GuardMixin``)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from ...config import Config
from ...core import version as V
from ...core.events import (
    CAP_EVIDENCE_BLOBS,
    CAP_ID_TEMPLATE,
    CAP_LOG_REDACTION,
    CAPABILITIES_KIND,
    OLDER_MARK,
    SEEN_KIND,
    SKEW_OVERRIDDEN_KIND,
    Event,
    RecordedCapability,
    SkewRefused,
    StampFacts,
    capabilities_ahead,
    capabilities_used,
    capability_gap,
    capability_unrecorded,
    is_older,
    stamp_facts,
)
from .. import shardmeta as SMT
from ._agent import CLONE_ID_FILE, _clone_suffix
from ._parse import SNAPSHOT_ENV
from ._skew import (
    _warn_patch_skew_once,
    _warn_skew_once,
    _write_seen_marker,
    capability_message,
    install_kind,
    running_version,
    skew_message,
    version_known,
)


class _GuardMixin:
    # -- the version stamp and the skew guard -----------------------------------------
    def _shard_meta(self) -> SMT.ShardMeta | None:
        """The per-clone shard facts (`infra.shardmeta`), or None where no cache is kept
        (`[log].reuse_parsed` off, the `DDFLOW_SNAPSHOT=0` kill switch, a log built only to
        read another repository). Not tied to `[log].snapshot` (B9d5e21a5e9): these facts are
        what keeps a fresh process's append from parsing every shard, and a snapshot that is
        off does not change that."""
        if (
            not (self.log_cfg.reuse_parsed and self.cache_writes)
            or os.environ.get(SNAPSHOT_ENV) == "0"
        ):
            return None
        if self._meta is None:
            self._meta = SMT.ShardMeta(self.root)
        return self._meta

    def _stamp_events(self) -> Iterator[Event]:
        """The events the version guard folds, in any order, not de-duplicated. From the
        per-shard record where one is current, so an append does not parse the whole log
        (B-gap-log-scale); otherwise every shard's events: the stamp facts do not need
        order, and sorting the whole log on each append would cost more than the append."""
        meta = self._shard_meta()
        if meta is not None:
            yield from meta.facts(self.shards(), lambda p: self._read_shard(p)[0])[1]
            return
        for p in self.shards():
            yield from self._read_shard(p)[0]

    def _stamp_and_guard(self, kind: str = "") -> dict[str, Any]:
        """Refuse an older ddflow's write to a newer log, stamp this version on first use.

        Returns extra `data` for the event about to be written: the older-version mark when
        a session-scoped override is what lets it through. Raises `SkewRefused` otherwise.
        Caller holds the lock."""
        version, fmt = running_version(), V.format_level()
        # a level of 0 is "this code declares no format": never compared
        facts = stamp_facts(self._stamp_events(), self.agent_id, version, fmt or None)
        gap = capability_gap(facts.capabilities, kind) if kind else None
        if gap is not None and self._skew_policy() == "refuse" and facts.override is None:
            name, rec = gap
            raise SkewRefused(capability_message(name, rec.version, kind, version, rec.by))
        extra = self._judge_skew(facts, version, fmt, kind=kind)
        if not facts.seen_by_me and version_known(version):
            stamp: dict[str, Any] = {"version": version, "install": install_kind()}
            if fmt:
                stamp["format_level"] = fmt
            self._write(SEEN_KIND, "ddflow", stamp)
            _write_seen_marker(self.root, version)
        if kind:
            self._record_capabilities(kind, facts.capabilities)
        return extra

    def _active_capabilities(self) -> set[str]:
        """The capabilities the project's own settings make every write of their kinds use:
        the full log redaction profile always, id templates once any `[ids]` knob is not its
        default. Read from the config once per log object (a stale answer only delays the
        record to the next object; the guard never depends on it)."""
        if self._caps_active is None:
            active = {CAP_LOG_REDACTION}
            if self.log_cfg.evidence_blobs:
                active.add(CAP_EVIDENCE_BLOBS)
            try:
                ids = Config.load(self.root).ids
                if ids != type(ids)():
                    active.add(CAP_ID_TEMPLATE)
            except Exception:
                pass
            self._caps_active = active
        return self._caps_active

    def _record_capabilities(self, kind: str, recorded: dict[str, RecordedCapability]) -> None:
        """Record, before the write that is about to use it, each capability it uses that the
        log does not yet record in full (`ddflow.capabilities`). Caller holds the lock."""
        for name in capabilities_used(kind, self._active_capabilities()):
            cap = capability_unrecorded(recorded, name)
            # Only the release that ships a capability announces it: an older build (a
            # development branch against a live log) would block every older ddflow
            # (B27842cfecd). A branch tests a capability by running as the release.
            if cap is not None and not is_older(running_version(), cap.since):
                self._write(
                    CAPABILITIES_KIND,
                    "ddflow",
                    {"capability": cap.name, "kinds": sorted(cap.kinds), "version": cap.since},
                )

    def _judge_skew(
        self, facts: StampFacts, version: str, fmt: int, *, strict: bool = False, kind: str = ""
    ) -> dict[str, Any]:
        """Apply the skew policy to ``facts``: raise `SkewRefused`, warn, or let the write
        through. Returns the extra `data` for an event written under an older-ddflow mark.
        ``strict`` withholds the patch-skew relaxation (a write of tracked files re-renders
        text whose wording can differ between patch releases)."""
        extra: dict[str, Any] = {}
        if not facts.skewed:
            return extra
        policy = self._skew_policy()
        if (
            policy == "refuse"
            and facts.override is None
            and not strict
            and self._patch_skew(facts, version, fmt, kind)
        ):
            _warn_patch_skew_once(
                version, facts.highest, root=self.root, mode=self._skew_notice_mode()
            )
            extra[OLDER_MARK] = version
        elif policy == "refuse":
            if facts.override is None:
                raise SkewRefused(
                    skew_message(
                        version,
                        facts.highest,
                        facts.highest_by,
                        log_format=facts.highest_format,
                        format_level=fmt,
                        format_only=facts.format_skewed,
                        format_version=facts.format_version,
                        format_by=facts.format_by,
                    )
                )
            extra[OLDER_MARK] = version
        elif policy == "warn":
            _warn_skew_once(
                version,
                facts.highest,
                facts.highest_format,
                fmt,
                root=self.root,
                mode=self._skew_notice_mode(),
            )
        return extra

    def check_skew(self) -> None:
        """Under the default `refuse` policy and with no session override, raise `SkewRefused`
        when this ddflow is older than the log's highest stamp, a PATCH behind included
        (`warn` and `off` are the operator's own knob and let it through); writes nothing,
        not even the stamp. For a caller that changes tracked
        files without appending an event (`adopt`): the managed text it renders is worded
        per release, so an older ddflow must not overwrite a newer one's."""
        version, fmt = running_version(), V.format_level()
        facts = stamp_facts(self._stamp_events(), self.agent_id, version, fmt or None)
        self._judge_skew(facts, version, fmt, strict=True)

    @staticmethod
    def _patch_skew(facts: StampFacts, version: str, fmt: int, kind: str | None = None) -> bool:
        """The log is ahead by PATCH releases only: the same data format, the same minor, and
        no recorded capability this ddflow lacks FOR THE KIND being written (``kind`` None: for
        any kind, as an override must see it). Such a skew warns instead of refusing (decision
        D-mu-bugfix-answers); a write of a governed kind is refused for its capability alone
        (a direct conflict, D-compat 2), everything else still refuses."""
        gap = (
            capabilities_ahead(facts.capabilities)
            if kind is None
            else capability_gap(facts.capabilities, kind) is not None
        )
        return (
            V.patch_only_skew(version, facts.highest)
            and not (fmt and facts.highest_format > fmt)
            and not gap
        )

    def _skew_policy(self) -> str:
        """`[upgrade].skew`: refuse (default) | warn | off. Read only when a skew is actually
        found, so the common append never loads the config for it."""
        try:
            return str(Config.load(self.root).upgrade.skew)
        except Exception:
            return "refuse"

    def _skew_notice_mode(self) -> str:
        """`[upgrade].skew_notice`: once (default) | always | off."""
        try:
            return str(Config.load(self.root).upgrade.skew_notice)
        except Exception:
            return "once"

    def override_skew(self, reason: str) -> Event | None:
        """Record a session-scoped override: let this agent's open session write although
        the running ddflow is OLDER than the log's highest stamp (`skew.overridden`).

        Returns the event, or None when there is nothing to override: no skew, or a policy
        (`warn`, `off`) that never refuses. A reason is required otherwise: refusing once and
        then overriding silently would only be a slower way of not asking."""
        reason = (reason or "").strip()
        with self.transaction():
            version, fmt = running_version(), V.format_level()
            facts = stamp_facts(self._stamp_events(), self.agent_id, version, fmt or None)
            if not facts.skewed:
                return None
            if facts.override is not None:
                return facts.override
            if self._skew_policy() != "refuse":
                return None  # warn/off never refuse, so there is nothing to override
            if self._patch_skew(facts, version, fmt, None):
                return None  # a patch skew warns and never refuses
            if not reason:
                raise SkewRefused(
                    f"--allow-older-version needs --reason: say why this session may write "
                    f"with ddflow {version} to a log ddflow {facts.highest} has worked on "
                    f"(ask the operator first)."
                )
            return self._write(
                SKEW_OVERRIDDEN_KIND,
                facts.session or "ddflow",
                {
                    "running": version,
                    "log_version": facts.highest,
                    "session": facts.session,
                    "reason": reason,
                    # a log whose FORMAT is ahead is overridden against that format, so a
                    # later, higher format is refused again (`stamp_facts`)
                    **(
                        {"log_format": facts.highest_format}
                        if fmt and facts.highest_format > fmt
                        else {}
                    ),
                },
            )

    def _highest_lamport(self) -> int:
        """The highest clock value in ANY event of any shard.

        It used to read only each shard's last line, on the reasoning that a shard has
        one writer so its tail carries its maximum. Two clones sharing an agent id break
        that, and `merge=union` puts the other clone's suffix AFTER ours: the merged
        shard read [1, 2, 3, 4, 2], the next append got 3, and `update --title v5`
        folded before the v4 it was written after (bug B28589b8b26). Nothing on disk
        says a shard had two writers, so the tail cannot be trusted for any shard.

        Reading every event is affordable because `_read_shard` re-parses only what was
        appended since the last read: measured 7.5 ms per append at 20,000 events warm,
        against 0.04 ms for the tail read — and an append is almost always preceded by a
        fold that warmed the cache.
        """
        meta = self._shard_meta()
        if meta is not None:
            return meta.facts(self.shards(), lambda p: self._read_shard(p)[0])[0]
        return max((e.lamport for p in self.shards() for e in self._read_shard(p)[0]), default=0)

    def clone_id(self, *, create: bool = True) -> str:
        """This clone's id (`.ddflow/local/clone-id`), "" when the project is not adopted.
        A claim records it so two clones writing under one agent name are told apart
        (B36aa4c8ee5). ``create=False`` only reads: the file's creation time is what
        separates the claims made under the bare derived id from the suffixed ones
        (:func:`clone_suffix_since`), so it must not be created as a side effect of a
        claim made under that id."""
        if create:
            return _clone_suffix(self.root)
        try:
            return (self.root / CLONE_ID_FILE).read_text("utf-8").strip()
        except OSError:
            return ""

    def clock_regressions(self) -> dict[str, tuple[int, int, int]]:
        """Shards whose clock goes BACKWARDS in file order: name -> (event number, the
        value before, the value after), for the first decrease in each.

        One writer only ever increases its clock, so a decrease means two writers shared
        the shard -- two clones resolving to one agent id, merged with `merge=union`.
        The clock copes with it now; `doctor` still reports it as a PROBLEM, since a
        lease held under it is "mine" in both clones (claims record their clone, so the
        fold sees such a double claim as a contest).
        """
        out: dict[str, tuple[int, int, int]] = {}
        for p in self.shards():
            prev = 0
            for n, e in enumerate(self._read_shard(p)[0], 1):
                if e.lamport < prev:
                    out[p.name] = (n, prev, e.lamport)
                    break
                prev = e.lamport
        return out
