"""`GitLab`, the forge adapter reached through ``glab`` — a second `Forge` adapter.

Kept in its own module so `ddflow.adapters.forge` stays under the 20 KB ceiling. The
port (`Forge`) and the registry (`FORGES`) live in `ddflow.adapters.forge`; this module
holds only `GitLab`, and importing it registers it under its `name` ("gitlab").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .forge import (
    GITLAB,
    THREAD_BODY_MAX,
    ForgeUnavailable,
    PRInfo,
    Thread,
    _check,
    _clip,
    _json,
    _run,
)

_GL_PIPELINE = {
    "success": "passing",
    "failed": "failing",
    "canceled": "failing",
    "running": "pending",
    "pending": "pending",
    "created": "pending",
    "waiting_for_resource": "pending",
    "preparing": "pending",
    "scheduled": "pending",
    "manual": "pending",
}


class GitLab:
    name = GITLAB

    def __init__(self, repo: Path):
        self.repo = repo

    def _api(self, *args: str, what: str) -> Any:
        return _json(_run(self.repo, ["glab", "api", *args]), what)

    def _info(self, d: dict[str, Any], approved: bool) -> PRInfo:
        state = {"opened": "open", "locked": "open"}.get(d.get("state", ""), d.get("state", ""))
        status = d.get("detailed_merge_status") or ""
        if status == "requested_changes":
            review = "changes_requested"
        elif approved:
            review = "approved"
        else:
            review = "pending"
        pipeline = (d.get("head_pipeline") or {}).get("status") or ""
        return PRInfo(
            number=int(d.get("iid") or 0),
            url=d.get("web_url", ""),
            state=state or "open",
            review=review,
            checks=_GL_PIPELINE.get(pipeline, ""),
            base=d.get("target_branch", ""),
            head=d.get("source_branch", ""),
            head_sha=d.get("sha") or "",
            merge_sha=d.get("merge_commit_sha") or d.get("squash_commit_sha") or "",
            draft=bool(d.get("draft")),
        )

    def find(self, head: str) -> PRInfo | None:
        rows = self._api(
            "-X",
            "GET",
            "projects/:id/merge_requests",
            "-f",
            f"source_branch={head}",
            "-f",
            "state=all",
            what=f"list merge requests for {head}",
        )
        if not rows:
            return None
        rows.sort(key=lambda r: (r.get("state") != "opened", -int(r["iid"])))
        return self.view(int(rows[0]["iid"]))

    def view(self, number: int) -> PRInfo:
        d = self._api("-X", "GET", f"projects/:id/merge_requests/{number}", what=f"view !{number}")
        ap = self._api(
            "-X",
            "GET",
            f"projects/:id/merge_requests/{number}/approvals",
            what=f"approvals !{number}",
        )
        # `approved` alone is true on a project with no approval rules at all -- "nobody
        # needed to approve" is not "somebody approved". A named approver is required.
        approved = bool(ap and ap.get("approved") and ap.get("approved_by"))
        info = self._info(d, approved)
        if info.review == "changes_requested":
            notes = self._api(
                "-X",
                "GET",
                f"projects/:id/merge_requests/{number}/notes",
                "-f",
                "sort=asc",
                what=f"notes !{number}",
            )
            info.feedback = _clip(
                [
                    f"{(n.get('position') or {}).get('new_path', '')}"
                    f"{':' + str((n.get('position') or {}).get('new_line')) if (n.get('position') or {}).get('new_line') else ''} "
                    f"{(n.get('author') or {}).get('username', '?')}: {n.get('body', '')}"
                    for n in notes or []
                    if not n.get("system")
                ]
            )
        return info

    def create(self, *, head, base, title, body, draft, labels, reviewers) -> PRInfo:
        argv = ["glab", "mr", "create", "--source-branch", head, "--target-branch", base]
        argv += ["--title", title, "--description", body, "--yes"]
        if draft:
            argv.append("--draft")
        if labels:
            argv += ["--label", ",".join(labels)]
        if reviewers:
            argv += ["--reviewer", ",".join(reviewers)]
        _check(_run(self.repo, argv), f"glab mr create --source-branch {head}")
        found = self.find(head)
        if found is None:
            raise ForgeUnavailable(
                f"glab mr create reported success but no request exists for {head}"
            )
        return found

    def merge(self, number: int, *, strategy: str, head_sha: str, auto: bool) -> str:
        args = ["-X", "PUT", f"projects/:id/merge_requests/{number}/merge"]
        args += ["-f", f"squash={'true' if strategy == 'squash' else 'false'}"]
        if head_sha:
            args += ["-f", f"sha={head_sha}"]
        if auto:
            # `auto_merge` is the current name; `merge_when_pipeline_succeeds` the one
            # older instances know. GitLab ignores a parameter it does not recognise.
            args += ["-f", "auto_merge=true", "-f", "merge_when_pipeline_succeeds=true"]
        d = self._api(*args, what=f"merge !{number}")
        return (d or {}).get("web_url", "") if isinstance(d, dict) else ""

    def threads(self, number: int) -> list[Thread]:
        rows = self._api(
            "-X",
            "GET",
            f"projects/:id/merge_requests/{number}/discussions",
            "-f",
            "per_page=100",
            what=f"discussions of !{number}",
        )
        out = []
        for d in rows or []:
            notes = [n for n in d.get("notes") or [] if not n.get("system")]
            if not notes or not any(n.get("resolvable") for n in notes):
                continue  # a plain comment is not a thread that can be resolved
            first = notes[0]
            pos = first.get("position") or {}
            resolvable = [n for n in notes if n.get("resolvable")]
            out.append(
                Thread(
                    id=str(d.get("id", "")),
                    path=pos.get("new_path") or pos.get("old_path") or "",
                    line=int(pos.get("new_line") or pos.get("old_line") or 0),
                    resolved=all(n.get("resolved") for n in resolvable),
                    author=(first.get("author") or {}).get("username", ""),
                    body=(first.get("body") or "").strip()[:THREAD_BODY_MAX],
                    replies=len(notes) - 1,
                )
            )
        return out

    def reply(self, number: int, thread_id: str, body: str) -> None:
        self._api(
            "-X",
            "POST",
            f"projects/:id/merge_requests/{number}/discussions/{thread_id}/notes",
            "-f",
            f"body={body}",
            what=f"reply on discussion {thread_id} of !{number}",
        )

    def resolve(self, number: int, thread_id: str) -> None:
        self._api(
            "-X",
            "PUT",
            f"projects/:id/merge_requests/{number}/discussions/{thread_id}",
            "-f",
            "resolved=true",
            what=f"resolve discussion {thread_id} of !{number}",
        )

    def set_base(self, number: int, base: str) -> None:
        self._api(
            "-X",
            "PUT",
            f"projects/:id/merge_requests/{number}",
            "-f",
            f"target_branch={base}",
            what=f"retarget !{number}",
        )
