#!/usr/bin/env python3
"""Publish an idempotent rolling GitHub triage digest issue from sync_issues.py JSON (stdlib-only).

Design invariants:
1. Read-only against upstream repos; writes only to the local repo's GitHub Issues API
   (`permissions: contents: read, issues: write`) and never pushes to `main`.
2. Single rolling triage issue owned strictly by the configured trusted actor
   (`github-actions[bot]` in GitHub Actions) carrying an HTML marker
   (`<!-- trust-no-readme:sync-digest -->`) and content hash
   (`<!-- trust-no-readme:sync-hash:<16-hex> -->`) to prevent duplicate issue spam and
   external issue-hijacking.
3. Never creates, edits, or closes the global rolling issue on `partial_failure`,
   `ok: false`, `files_checked == 0`, scoped `repo_filter`, stale non-default checkouts,
   or `dry_run`.
4. Sanitizes all untrusted upstream strings (control chars, HTML angle brackets,
   Markdown links, mentions, raw URLs, `www.` autolinks, and `#123` refs) and truncates
   strictly on whole-line boundaries so code spans are never severed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DIGEST_MARKER = "<!-- trust-no-readme:sync-digest -->"
HASH_MARKER_RE = re.compile(r"<!-- trust-no-readme:sync-hash:([0-9a-f]{16}) -->")
COMMENT_MARKER_PREFIX = "<!-- trust-no-readme:sync-comment:"
LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
REF_OR_URL_RE = re.compile(
    r"(https?://[^\s<>()]+|(?<![A-Za-z0-9_./:-])www\.[^\s<>()]+|(?<![A-Za-z0-9_./:-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+|(?<![A-Za-z0-9_./:&;-])#\d+)"
)
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.-])@([A-Za-z0-9])")

ISSUE_TITLE = "[Upstream Sync] Actionable upstream updates detected"
ISSUE_LABEL = "upstream-sync"
ISSUE_LABEL_COLOR = "0e8a16"
ISSUE_LABEL_DESC = "Automated read-only upstream remediation sync digest"
DEFAULT_BOT_ACTOR = "github-actions[bot]"
MAX_ISSUE_BODY_CHARS = 60000

ALERT_PRIORITY: list[tuple[str, str]] = [
    ("verify_fix", "Fix Claimed (Inspect Diff Before Verifying)"),
    ("check_release", "New Upstream Release (Check for Shipped Promotion)"),
    ("review_reopened", "Previously Verified Issue Reopened (Check Regression)"),
    ("review_not_planned", "Closed as Not Planned (Review for Wontfix)"),
    ("review_maintainer_reply", "New Official Maintainer / Bot Reply"),
    ("silent_30d", "30+ Days Without Maintainer Reply"),
    ("silent_14d", "14+ Days Without Maintainer Reply"),
]
ALERT_PRIORITY_INDEX = {kind: idx for idx, (kind, _) in enumerate(ALERT_PRIORITY)}
SILENT_ALERT_KINDS = {"silent_14d", "silent_30d"}


def _wrap_ref_or_url(match: re.Match[str]) -> str:
    token = match.group(0)
    core = token.rstrip(".,:")
    suffix = token[len(core) :]
    return f"`{core}`{suffix}" if core else token


def sanitize_md_line(text: Any, max_len: int = 500) -> str:
    """Sanitize untrusted upstream text for safe GitHub Markdown without pings, links, or cross-ref spam."""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()
    if len(s) > max_len:
        s = s[: max_len - 3].rstrip() + "..."
    # Replace backticks before wrapping URLs/refs so inline code spans cannot be broken
    s = s.replace("`", "'")
    # Escape HTML entities, backslashes, and Markdown brackets so neither HTML/marker spoofing nor escaped backticks survive
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("\\", "&#92;").replace("[", "&#91;").replace("]", "&#93;")
    # Prevent accidental GitHub @mentions
    s = MENTION_RE.sub("@\u200b\\1", s)
    # Wrap raw URLs, www. autolinks, and issue/PR refs in inline code spans to avoid cross-ref spam
    s = REF_OR_URL_RE.sub(_wrap_ref_or_url, s)
    return s


def validate_sync_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("Sync report must be a JSON object")
    if "ok" not in report or not isinstance(report["ok"], bool):
        raise ValueError("Sync report `ok` must be an explicit boolean")
    if "partial_failure" not in report or not isinstance(report["partial_failure"], bool):
        raise ValueError("Sync report `partial_failure` must be an explicit boolean")
    if "repo_filter" not in report:
        raise ValueError("Sync report must explicitly include `repo_filter` (null for global runs)")
    repo_filter = report["repo_filter"]
    if repo_filter is not None:
        if not isinstance(repo_filter, str) or not repo_filter.strip():
            raise ValueError("Sync report `repo_filter` must be null or a non-empty owner/repo string")
        repo_filter = repo_filter.strip()
    changes = report.get("changes")
    alerts = report.get("alerts")
    if not isinstance(changes, list) or not all(isinstance(c, str) for c in changes):
        raise ValueError("Sync report `changes` must be a list of strings")
    if not isinstance(alerts, list) or not all(isinstance(a, dict) for a in alerts):
        raise ValueError("Sync report `alerts` must be a list of objects")
    checked_at = report.get("checked_at")
    if not isinstance(checked_at, str) or not checked_at.strip():
        raise ValueError("Sync report `checked_at` must be a non-empty string")
    files_checked = report.get("files_checked")
    if isinstance(files_checked, bool) or not isinstance(files_checked, int) or files_checked < 0:
        raise ValueError("Sync report `files_checked` must be a non-negative integer")
    return {
        "ok": report["ok"],
        "partial_failure": report["partial_failure"],
        "checked_at": checked_at.strip(),
        "repo_filter": repo_filter,
        "files_checked": files_checked,
        "files_changed": [str(x) for x in (report.get("files_changed") or []) if isinstance(x, str)],
        "changes": list(changes),
        "alerts": list(alerts),
    }


def split_alerts(alerts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actionable: list[dict[str, Any]] = []
    api_errors: list[dict[str, Any]] = []
    for alert in alerts:
        if alert.get("kind") == "api_error":
            api_errors.append(alert)
        else:
            actionable.append(alert)
    return actionable, api_errors


def compute_digest_hash(changes: list[str], actionable_alerts: list[dict[str, Any]]) -> str:
    """Compute stable hash over actionable alerts & changes (ignoring date and silent day-count churn)."""
    norm_alerts: list[list[str]] = []
    for a in actionable_alerts:
        kind = str(a.get("kind") or "")
        repo = str(a.get("repo") or "")
        item_id = str(a.get("item_id") or "")
        # Silence alerts hash by tier only so day 15 -> day 22 stays deduplicated until day 30
        msg = "" if kind in SILENT_ALERT_KINDS else str(a.get("message") or "")
        norm_alerts.append([kind, repo, item_id, msg])
    payload = {
        "changes": sorted(set(changes)),
        "alerts": sorted(norm_alerts),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _truncate_lines_safely(lines: list[str], max_chars: int = MAX_ISSUE_BODY_CHARS) -> str:
    """Truncate strictly on whole-line boundaries so inline code spans and fenced blocks are never severed."""
    full = "\n".join(lines) + "\n"
    if len(full) <= max_chars:
        return full
    notice = "\n_(Digest truncated to fit GitHub Issue size limit; run `python3 tools/sync_issues.py --format json` locally for full output.)_\n"
    if len(notice) >= max_chars:
        return "Digest truncated.\n"[:max_chars]
    budget = max_chars - len(notice)
    kept: list[str] = []
    used = 0
    in_fence = False
    for line in lines:
        add_len = len(line) + 1
        if used + add_len > budget:
            break
        kept.append(line)
        used += add_len
        if line.strip().startswith("```"):
            in_fence = not in_fence
    if in_fence:
        # Remove trailing unclosed code block lines cleanly
        while kept and not kept[-1].strip().startswith("```"):
            kept.pop()
        if kept and kept[-1].strip().startswith("```"):
            kept.pop()
    return "\n".join(kept).rstrip() + "\n" + notice


def render_digest_markdown(
    report: dict[str, Any],
    digest_hash: str | None = None,
    include_markers: bool = True,
    include_api_errors: bool = True,
    max_chars: int = MAX_ISSUE_BODY_CHARS,
) -> str:
    actionable_alerts, api_errors = split_alerts(report["alerts"])
    shown_api_errors = api_errors if include_api_errors else []
    changes = report["changes"]
    checked_at = sanitize_md_line(report["checked_at"], max_len=40)
    is_partial = bool((not report["ok"]) or report["partial_failure"] or api_errors)

    lines: list[str] = []
    if include_markers:
        lines.append(DIGEST_MARKER)
        if digest_hash:
            lines.append(f"<!-- trust-no-readme:sync-hash:{digest_hash} -->")
        lines.append("")

    lines.append("## Upstream Issue Observer Digest")
    lines.append("")
    scope_note = f" (scoped filter: `{sanitize_md_line(report['repo_filter'], max_len=120)}`)" if report["repo_filter"] else ""
    lines.append(
        f"- **Checked (UTC):** `{checked_at}` · **Tracking files checked:** `{report['files_checked']}`{scope_note}"
    )
    if include_api_errors:
        lines.append(
            f"- **Actionable alerts:** `{len(actionable_alerts)}` · **Observed tracker changes:** `{len(changes)}` · **API errors:** `{len(api_errors)}`"
        )
    else:
        lines.append(
            f"- **Actionable alerts:** `{len(actionable_alerts)}` · **Observed tracker changes:** `{len(changes)}`"
        )
    lines.append("")

    if include_api_errors and is_partial:
        lines.append(
            "> **Partial Failure Warning:** One or more GitHub API requests failed during this run. "
            "The global rolling triage issue was intentionally left untouched so incomplete results cannot drop or close pending items."
        )
        lines.append("")

    if shown_api_errors:
        lines.append("### API Errors (Retry Recommended)")
        lines.append("")
        for err in shown_api_errors:
            lines.append(f"- [`api_error`] {sanitize_md_line(err.get('message') or '')}")
        lines.append("")

    if not actionable_alerts and not changes and not shown_api_errors:
        if report["files_checked"] == 0:
            lines.append("No tracking files were checked; global rolling triage issue was left untouched.")
        else:
            lines.append("All tracked upstream issues and releases match `tracking.json` with zero pending review alerts.")
        return _truncate_lines_safely(lines, max_chars=max_chars)

    if actionable_alerts:
        lines.append("### Action / Review Alerts")
        lines.append("")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for alert in actionable_alerts:
            grouped.setdefault(str(alert.get("kind") or "other"), []).append(alert)

        ordered_kinds = sorted(grouped.keys(), key=lambda k: (ALERT_PRIORITY_INDEX.get(k, 99), k))
        label_map = dict(ALERT_PRIORITY)
        for kind in ordered_kinds:
            heading = label_map.get(kind, f"Other (`{sanitize_md_line(kind, max_len=60)}`)")
            lines.append(f"#### {heading}")
            for alert in sorted(
                grouped[kind],
                key=lambda a: (str(a.get("repo") or ""), str(a.get("item_id") or ""), str(a.get("message") or "")),
            ):
                msg = sanitize_md_line(alert.get("message") or "")
                lines.append(f"- [`{sanitize_md_line(kind, max_len=40)}`] {msg}")
            lines.append("")

    if changes:
        lines.append("### Observed Tracker State Changes")
        lines.append("")
        for ch in changes:
            lines.append(f"- {sanitize_md_line(ch)}")
        lines.append("")

    if actionable_alerts or changes:
        lines.extend(
            [
                "### How to Apply & Verify Locally",
                "",
                "The scheduled observer is strictly read-only on upstream repositories and never pushes to `main` or auto-verifies fixes.",
                "Inspect the upstream commit diff before promoting any `fix_claimed` item to `verified_main`, `partial_fix`, or `verified_shipped`:",
                "",
                "```bash",
                "python3 tools/sync_issues.py --write",
                "python3 tools/build_index.py",
                "python3 tools/validate.py --verify-upstream --format json",
                "```",
            ]
        )

    return _truncate_lines_safely(lines, max_chars=max_chars)


def _github_api_request(
    method: str,
    api_path: str,
    token: str,
    payload: dict[str, Any] | None = None,
    paginate: bool = False,
) -> Any:
    base = api_path if api_path.startswith("https://") else f"https://api.github.com/{api_path.lstrip('/')}"
    if method == "GET" and paginate and "per_page=" not in base:
        sep = "&" if "?" in base else "?"
        base = f"{base}{sep}per_page=100"

    def _one(url: str) -> tuple[Any, str | None]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "trust-no-readme-sync-digest/1.1",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {token}",
        }
        body_bytes: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw.strip() else {}
            link = resp.headers.get("Link")
            next_url: str | None = None
            if link:
                m = LINK_NEXT_RE.search(link)
                if m:
                    next_url = m.group(1)
            return data, next_url

    data, next_url = _one(base)
    if method != "GET" or not paginate:
        return data
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON list from paginated GET {base}")
    items = list(data)
    pages = 1
    while next_url:
        if pages >= 50:
            raise RuntimeError(f"Pagination limit (50 pages) exceeded for {base}")
        page_data, next_url = _one(next_url)
        if not isinstance(page_data, list):
            raise ValueError(f"Expected JSON list on page {pages + 1} for {base}")
        items.extend(page_data)
        pages += 1
    return items


def find_rolling_digest_issue(
    issues: list[dict[str, Any]],
    trusted_actors: set[str] | None = None,
) -> dict[str, Any] | None:
    """Find the canonical open rolling digest issue owned strictly by trusted_actors."""
    allowed = trusted_actors or {DEFAULT_BOT_ACTOR}
    matches: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        if issue.get("state") != "open":
            continue
        user = issue.get("user") if isinstance(issue.get("user"), dict) else {}
        if user.get("login") not in allowed:
            continue
        body = issue.get("body") or ""
        if DIGEST_MARKER not in body:
            continue
        num = issue.get("number")
        if isinstance(num, int) and not isinstance(num, bool) and num > 0:
            matches.append(issue)
    if not matches:
        return None
    return min(matches, key=lambda it: int(it["number"]))


def extract_digest_hash(body: str) -> str | None:
    m = HASH_MARKER_RE.search(body or "")
    return m.group(1) if m else None


def _label_exists(repo: str, api_call: Callable[..., Any]) -> bool:
    quoted = urllib.parse.quote(ISSUE_LABEL, safe="")
    try:
        res = api_call("GET", f"repos/{repo}/labels/{quoted}")
        return isinstance(res, dict) and str(res.get("name") or "").lower() == ISSUE_LABEL.lower()
    except Exception:  # noqa: BLE001
        return False


def _ensure_label_best_effort(repo: str, api_call: Callable[..., Any]) -> bool:
    if _label_exists(repo, api_call):
        return True
    try:
        api_call(
            "POST",
            f"repos/{repo}/labels",
            payload={"name": ISSUE_LABEL, "color": ISSUE_LABEL_COLOR, "description": ISSUE_LABEL_DESC},
        )
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 422:
            # Verify label actually exists rather than treating generic 422 as existence
            return _label_exists(repo, api_call)
        return False
    except Exception:  # noqa: BLE001
        return False


def _verify_live_default_branch(
    repo: str,
    git_ref: str | None,
    head_sha: str | None,
    api_call: Callable[..., Any],
) -> tuple[bool, str, str]:
    """Return (is_live_tip, default_branch, reason)."""
    repo_meta = api_call("GET", f"repos/{repo}")
    if not isinstance(repo_meta, dict) or not isinstance(repo_meta.get("default_branch"), str) or not repo_meta["default_branch"].strip():
        raise ValueError(f"Could not determine default branch for `{repo}`")
    default_branch = repo_meta["default_branch"].strip()
    expected_ref = f"refs/heads/{default_branch}"
    if not git_ref or git_ref.strip() != expected_ref:
        return False, default_branch, f"ref `{git_ref or 'unknown'}` does not match `{expected_ref}`"
    if not head_sha or not SHA40_RE.fullmatch(head_sha.strip()):
        return False, default_branch, f"checkout SHA `{head_sha or 'unknown'}` is not a valid 40-char SHA"
    quoted_branch = urllib.parse.quote(default_branch, safe="/")
    ref_meta = api_call("GET", f"repos/{repo}/git/ref/heads/{quoted_branch}")
    live_sha = (ref_meta.get("object") or {}).get("sha") if isinstance(ref_meta, dict) else None
    if not isinstance(live_sha, str) or not SHA40_RE.fullmatch(live_sha):
        raise ValueError(f"Could not resolve live tip SHA for `{repo}` branch `{default_branch}`")
    if head_sha.strip() != live_sha:
        return False, default_branch, f"checkout SHA `{head_sha[:8]}` is stale vs live `{default_branch}` tip `{live_sha[:8]}`"
    return True, default_branch, ""


def _comment_marker_exists(
    repo: str,
    issue_num: int,
    marker: str,
    trusted_actors: set[str],
    api_call: Callable[..., Any],
) -> bool:
    comments = api_call("GET", f"repos/{repo}/issues/{issue_num}/comments", paginate=True)
    if not isinstance(comments, list):
        raise ValueError(f"Unexpected non-list response when listing comments on {repo}#{issue_num}")
    for cm in comments:
        if not isinstance(cm, dict):
            continue
        user = cm.get("user") if isinstance(cm.get("user"), dict) else {}
        if user.get("login") in trusted_actors and marker in (cm.get("body") or ""):
            return True
    return False


def publish_sync_digest(
    raw_report: Any,
    repo: str | None,
    api_call: Callable[..., Any] | None = None,
    dry_run: bool = False,
    actor_login: str = DEFAULT_BOT_ACTOR,
    require_default_branch: bool = False,
    git_ref: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """Reconcile the single rolling GitHub triage issue against a validated sync report."""
    report = validate_sync_report(raw_report)
    actionable_alerts, api_errors = split_alerts(report["alerts"])
    changes = report["changes"]
    has_actionable = bool(changes or actionable_alerts)
    is_partial = bool((not report["ok"]) or report["partial_failure"] or api_errors)
    digest_hash = compute_digest_hash(changes, actionable_alerts) if has_actionable else None
    summary_md = render_digest_markdown(report, digest_hash=digest_hash, include_markers=False, include_api_errors=True)

    if report["repo_filter"]:
        return {
            "action": "noop_scoped_filter",
            "issue_number": None,
            "digest_hash": digest_hash,
            "has_actionable": has_actionable,
            "partial_failure": is_partial,
            "summary_markdown": summary_md,
        }

    if dry_run:
        return {
            "action": "dry_run",
            "issue_number": None,
            "digest_hash": digest_hash,
            "has_actionable": has_actionable,
            "partial_failure": is_partial,
            "summary_markdown": summary_md,
        }

    # Any partial or failed sync run NEVER creates, edits, or closes the global rolling issue
    if is_partial:
        return {
            "action": "noop_partial_failure",
            "issue_number": None,
            "digest_hash": digest_hash,
            "has_actionable": has_actionable,
            "partial_failure": True,
            "summary_markdown": summary_md,
        }

    # Zero tracking files checked cannot prove resolution
    if report["files_checked"] == 0:
        return {
            "action": "noop_empty_scope",
            "issue_number": None,
            "digest_hash": None,
            "has_actionable": False,
            "partial_failure": False,
            "summary_markdown": summary_md,
        }

    if not repo or not REPO_SLUG_RE.fullmatch(repo):
        raise ValueError(f"Invalid target GitHub repository slug: {repo!r}")
    if api_call is None:
        raise ValueError("api_call is required when dry_run is False")
    actor_clean = actor_login.strip()
    if not actor_clean:
        raise ValueError("actor_login must be a non-empty GitHub login")
    trusted_actors = {actor_clean}

    default_branch = "main"
    if require_default_branch:
        is_live_tip, default_branch, stale_reason = _verify_live_default_branch(repo, git_ref, head_sha, api_call)
        if not is_live_tip:
            stale_note = (
                f"\n> **Stale / Non-Default Checkout Guard:** Skipped updating the global rolling triage issue "
                f"because {sanitize_md_line(stale_reason)}.\n"
            )
            return {
                "action": "noop_stale_ref",
                "issue_number": None,
                "digest_hash": digest_hash,
                "has_actionable": has_actionable,
                "partial_failure": False,
                "summary_markdown": summary_md + stale_note,
            }

    open_issues = api_call("GET", f"repos/{repo}/issues?state=open", paginate=True)
    if not isinstance(open_issues, list):
        raise ValueError("Unexpected non-list response when listing open issues")
    existing = find_rolling_digest_issue(open_issues, trusted_actors=trusted_actors)

    # Case 1: clean complete run with zero changes & zero alerts -> close stale rolling issue (PATCH first, then comment)
    if not has_actionable:
        if existing is None:
            return {
                "action": "noop_clean",
                "issue_number": None,
                "digest_hash": None,
                "has_actionable": False,
                "partial_failure": False,
                "summary_markdown": summary_md,
            }
        num = int(existing["number"])
        api_call(
            "PATCH",
            f"repos/{repo}/issues/{num}",
            payload={"state": "closed", "state_reason": "completed"},
        )
        api_call(
            "POST",
            f"repos/{repo}/issues/{num}/comments",
            payload={
                "body": (
                    f"All tracked upstream changes and review alerts are resolved on `{sanitize_md_line(default_branch, max_len=60)}` "
                    f"as of `{sanitize_md_line(report['checked_at'], max_len=40)}`. Closing this rolling sync digest."
                )
            },
        )
        return {
            "action": "closed_resolved",
            "issue_number": num,
            "digest_hash": None,
            "has_actionable": False,
            "partial_failure": False,
            "summary_markdown": summary_md,
        }

    body_md = render_digest_markdown(report, digest_hash=digest_hash, include_markers=True, include_api_errors=False)

    # Case 2: actionable items exist and no rolling issue is open -> create one (with unlabeled fallback on 422)
    if existing is None:
        create_payload: dict[str, Any] = {"title": ISSUE_TITLE, "body": body_md}
        used_label = _ensure_label_best_effort(repo, api_call)
        if used_label:
            create_payload["labels"] = [ISSUE_LABEL]
        try:
            created = api_call("POST", f"repos/{repo}/issues", payload=create_payload)
        except urllib.error.HTTPError as exc:
            if used_label and exc.code == 422:
                fallback_payload = {"title": ISSUE_TITLE, "body": body_md}
                created = api_call("POST", f"repos/{repo}/issues", payload=fallback_payload)
            else:
                raise
        created_num = created.get("number") if isinstance(created, dict) else None
        return {
            "action": "created",
            "issue_number": created_num,
            "digest_hash": digest_hash,
            "has_actionable": True,
            "partial_failure": False,
            "summary_markdown": summary_md,
        }

    # Case 3: rolling issue exists -> update only if digest hash changed (idempotent comment-marker before PATCH)
    num = int(existing["number"])
    prev_hash = extract_digest_hash(existing.get("body") or "")
    if prev_hash == digest_hash:
        return {
            "action": "unchanged",
            "issue_number": num,
            "digest_hash": digest_hash,
            "has_actionable": True,
            "partial_failure": False,
            "summary_markdown": summary_md,
        }

    comment_marker = f"{COMMENT_MARKER_PREFIX}{digest_hash} -->"
    if not _comment_marker_exists(repo, num, comment_marker, trusted_actors, api_call):
        api_call(
            "POST",
            f"repos/{repo}/issues/{num}/comments",
            payload={
                "body": (
                    f"{comment_marker}\n"
                    f"Updated rolling upstream sync digest as of `{sanitize_md_line(report['checked_at'], max_len=40)}`: "
                    f"`{len(actionable_alerts)}` alert(s) and `{len(changes)}` observed tracker change(s). "
                    "See the updated issue description above for the full checklist."
                )
            },
        )
    api_call("PATCH", f"repos/{repo}/issues/{num}", payload={"title": ISSUE_TITLE, "body": body_md})
    return {
        "action": "updated",
        "issue_number": num,
        "digest_hash": digest_hash,
        "has_actionable": True,
        "partial_failure": False,
        "summary_markdown": summary_md,
    }


def _append_step_summary(summary_path: str | None, markdown: str) -> None:
    if not summary_path:
        return
    p = Path(summary_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(markdown)
        if not markdown.endswith("\n"):
            fh.write("\n")


def resolve_verified_actor_login(
    requested_actor: str | None,
    in_github_actions: bool,
    api_call: Callable[..., Any],
) -> str:
    """Resolve and verify the GitHub login matching the active token so created issues stay rediscoverable."""
    if in_github_actions and (requested_actor is None or requested_actor.strip() == DEFAULT_BOT_ACTOR):
        return DEFAULT_BOT_ACTOR
    user_meta = api_call("GET", "user")
    actual_login = user_meta.get("login") if isinstance(user_meta, dict) else None
    if not isinstance(actual_login, str) or not actual_login.strip():
        raise ValueError("Could not verify authenticated GitHub token owner via GET /user")
    actual_clean = actual_login.strip()
    if requested_actor is not None and requested_actor.strip() and requested_actor.strip() != actual_clean:
        raise ValueError(
            f"--actor-login `{requested_actor.strip()}` does not match authenticated GitHub token owner `{actual_clean}`"
        )
    return actual_clean


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish idempotent rolling GitHub triage digest issue from sync_issues.py JSON.")
    ap.add_argument("--input", required=True, help="Path to JSON output from tools/sync_issues.py --format json")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="Target owner/repo for triage digest issue")
    ap.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable containing GitHub token")
    ap.add_argument(
        "--actor-login",
        default=None,
        help="Optional GitHub login expected for the active token (verified via GET /user outside GitHub Actions; defaults to github-actions[bot] inside Actions)",
    )
    ap.add_argument(
        "--require-default-branch",
        action="store_true",
        help="Refuse to mutate the global rolling issue unless --git-ref and --head-sha match the repo's live default branch tip",
    )
    ap.add_argument("--git-ref", default=os.environ.get("GITHUB_REF", ""), help="Checked-out Git ref (e.g. refs/heads/main)")
    ap.add_argument("--head-sha", default=os.environ.get("GITHUB_SHA", ""), help="Checked-out 40-char commit SHA")
    ap.add_argument("--dry-run", action="store_true", help="Format summary without calling GitHub Issues API")
    ap.add_argument("--summary-file", default=os.environ.get("GITHUB_STEP_SUMMARY", ""), help="Optional path to append Markdown summary")
    args = ap.parse_args()

    in_path = Path(args.input)
    try:
        raw_text = in_path.read_text(encoding="utf-8")
        if not raw_text.strip():
            raise ValueError(f"Sync report file `{in_path}` is empty")
        raw_report = json.loads(raw_text)
        report = validate_sync_report(raw_report)
    except Exception as exc:  # noqa: BLE001
        err_md = f"## Upstream Issue Observer Failed\n\nCould not parse sync report JSON: `{sanitize_md_line(exc)}`\n"
        _append_step_summary(args.summary_file, err_md)
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1

    actionable_alerts, api_errors = split_alerts(report["alerts"])
    is_partial = bool((not report["ok"]) or report["partial_failure"] or api_errors)
    needs_api = not (args.dry_run or report["repo_filter"] or is_partial or report["files_checked"] == 0)

    api_call: Callable[..., Any] | None = None
    actor_login = DEFAULT_BOT_ACTOR
    if needs_api:
        token = os.environ.get(args.token_env, "").strip()
        if not token:
            err_md = f"## Upstream Issue Observer Failed\n\nMissing GitHub token in `{sanitize_md_line(args.token_env)}`.\n"
            _append_step_summary(args.summary_file, err_md)
            print(json.dumps({"ok": False, "error": f"Missing token in {args.token_env}"}, indent=2))
            return 1

        def _call(method: str, api_path: str, payload: dict[str, Any] | None = None, paginate: bool = False) -> Any:
            return _github_api_request(method, api_path, token=token, payload=payload, paginate=paginate)

        api_call = _call
        try:
            actor_login = resolve_verified_actor_login(
                args.actor_login,
                in_github_actions=(os.environ.get("GITHUB_ACTIONS") == "true"),
                api_call=api_call,
            )
        except Exception as exc:  # noqa: BLE001
            err_md = f"## Upstream Issue Observer Failed\n\nToken actor verification failed: `{sanitize_md_line(exc)}`\n"
            _append_step_summary(args.summary_file, err_md)
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1

    try:
        result = publish_sync_digest(
            report,
            repo=args.repo.strip() or None,
            api_call=api_call,
            dry_run=args.dry_run,
            actor_login=actor_login,
            require_default_branch=args.require_default_branch,
            git_ref=args.git_ref.strip() or None,
            head_sha=args.head_sha.strip() or None,
        )
    except Exception as exc:  # noqa: BLE001
        err_md = f"## Upstream Issue Observer Failed\n\nDigest publish error: `{sanitize_md_line(exc)}`\n"
        _append_step_summary(args.summary_file, err_md)
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1

    _append_step_summary(args.summary_file, result["summary_markdown"])
    out = {k: v for k, v in result.items() if k != "summary_markdown"}
    out["ok"] = True
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
