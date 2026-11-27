#!/usr/bin/env python3
"""Read-only upstream issue observer & triage digest generator (stdlib-only Python).

Queries GitHub REST API (with full Link-header pagination) for every tracked public
issue in `audits/*/*/*/tracking.json` and updates factual observed tracker fields
(`observed`, `latest_upstream_release`, `last_checked_at`, and machine-owned
pre-verification states such as `open`, `triaged`, `pr_open`, `fix_claimed`).

Hard guardrails:
1. NEVER marks an issue `verified_main`, `verified_shipped`, `partial_fix`,
   `wontfix`, `closed_unfixed`, or `regressed`.
2. On ANY incomplete API fetch (including linked PR HTTP 429/5xx), preserves prior
   observations, refuses to advance `last_checked_at`, reports partial failure, and
   exits non-zero.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from build_index import AUDITS_DIR, ROOT, compute_wait_days

GH_ISSUE_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)(?:[/?#].*)?$")
GH_PR_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)(?:[/?#].*)?$")
LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')

MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
HUMAN_ADJUDICATED_STATES = {
    "acknowledged",
    "partial_fix",
    "verified_main",
    "verified_shipped",
    "wontfix",
    "closed_unfixed",
    "regressed",
}


def resolve_github_token(token_env: str = "GITHUB_TOKEN") -> str | None:
    for env_name in (token_env, "GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(env_name, "").strip()
        if val:
            return val
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return None


def _github_request(url: str, token: str | None = None) -> tuple[Any, str | None]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "trust-no-readme-sync-issues/1.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        link_header = resp.headers.get("Link")
        next_url: str | None = None
        if link_header:
            m = LINK_NEXT_RE.search(link_header)
            if m:
                next_url = m.group(1)
        return data, next_url


def github_get_json(api_path: str, token: str | None = None, paginate: bool = False) -> Any:
    base = api_path if api_path.startswith("https://") else f"https://api.github.com/{api_path.lstrip('/')}"
    if paginate and "per_page=" not in base:
        sep = "&" if "?" in base else "?"
        base = f"{base}{sep}per_page=100"

    data, next_url = _github_request(base, token=token)
    if not paginate or not isinstance(data, list):
        return data

    items = list(data)
    pages = 1
    while next_url:
        if pages >= 50:
            raise RuntimeError(f"Pagination limit (50 pages) exceeded for {base}")
        page_data, next_url = _github_request(next_url, token=token)
        if not isinstance(page_data, list):
            raise ValueError(f"Expected JSON list on page {pages + 1} for {base}")
        items.extend(page_data)
        pages += 1
    return items


def _classify_pr_author(author_login: str | None, author_association: str | None, official_bots: set[str]) -> str:
    if (author_association or "").upper() in MAINTAINER_ASSOCIATIONS or (author_login or "") in official_bots:
        return "maintainer"
    if (author_association or "").upper() == "CONTRIBUTOR":
        return "contributor"
    return "community"


def _parse_iso_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    s = value.strip()
    try:
        if s.endswith("Z"):
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if len(s) == 10:
            d = dt.date.fromisoformat(s)
            return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
        parsed = dt.datetime.fromisoformat(s)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _is_official_actor(login: str | None, association: str | None, official_bots: set[str]) -> bool:
    if login and login in official_bots:
        return True
    return (association or "").upper() in MAINTAINER_ASSOCIATIONS


def _ensure_dict(parent: dict[str, Any], key: str, defaults: dict[str, Any]) -> dict[str, Any]:
    val = parent.get(key)
    if not isinstance(val, dict):
        val = dict(defaults)
        parent[key] = val
    else:
        for dk, dv in defaults.items():
            val.setdefault(dk, dv)
    return val


def _call_fetcher(fetcher: Any, api_path: str, paginate: bool = False) -> Any:
    try:
        return fetcher(api_path, paginate=paginate)
    except TypeError:
        # Backward-compatible support for single-arg test fetchers
        return fetcher(api_path)


def sync_project_tracking_data(
    tracking: dict[str, Any],
    fetcher: Any,
    checked_date: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Return (updated_tracking, alerts, change_notes) using an injected JSON fetcher."""
    updated = json.loads(json.dumps(tracking))
    alerts: list[dict[str, Any]] = []
    changes: list[str] = []

    repo = updated["repo"]
    forge = updated.get("forge", "github")
    official_bots = set(updated.get("official_bots") or [])

    if forge != "github":
        return updated, alerts, changes

    had_api_error = False

    # Fetch latest upstream release (404 is valid for repos with no GitHub Releases)
    try:
        rel = _call_fetcher(fetcher, f"repos/{repo}/releases/latest", paginate=False)
        if not isinstance(rel, dict):
            raise ValueError("Unexpected non-object releases/latest response")
        if rel.get("tag_name"):
            new_rel = {
                "tag": str(rel["tag_name"]),
                "published_at": str(rel.get("published_at") or rel.get("created_at") or checked_date),
                "url": rel.get("html_url"),
            }
            if updated.get("latest_upstream_release") != new_rel:
                changes.append(f"{repo}: latest release -> {new_rel['tag']}")
            updated["latest_upstream_release"] = new_rel
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            had_api_error = True
            alerts.append({"repo": repo, "item_id": None, "kind": "api_error", "message": f"releases/latest HTTP {exc.code}"})
    except Exception as exc:  # noqa: BLE001
        had_api_error = True
        alerts.append({"repo": repo, "item_id": None, "kind": "api_error", "message": f"releases/latest failed: {exc}"})

    latest_rel = updated.get("latest_upstream_release")
    latest_rel_dt = _parse_iso_dt(latest_rel.get("published_at")) if isinstance(latest_rel, dict) else None

    for item in updated.get("items", []):
        m = GH_ISSUE_RE.match(item.get("url", ""))
        if not m:
            continue
        issue_repo, issue_num = m.group(1), m.group(2)
        item_id = item["id"]
        old_status = item["status"]

        try:
            issue_data = _call_fetcher(fetcher, f"repos/{issue_repo}/issues/{issue_num}", paginate=False)
            comments_data = _call_fetcher(fetcher, f"repos/{issue_repo}/issues/{issue_num}/comments", paginate=True)
            timeline_data = _call_fetcher(fetcher, f"repos/{issue_repo}/issues/{issue_num}/timeline", paginate=True)
            if not isinstance(issue_data, dict) or not isinstance(comments_data, list) or not isinstance(timeline_data, list):
                raise ValueError("Unexpected non-object/non-list GitHub API response payload")
        except Exception as exc:  # noqa: BLE001
            had_api_error = True
            alerts.append({"repo": repo, "item_id": item_id, "kind": "api_error", "message": f"Failed fetching {issue_repo}#{issue_num}: {exc}"})
            continue

        labels = sorted(str(lb.get("name")) for lb in issue_data.get("labels", []) if isinstance(lb, dict) and lb.get("name"))
        gh_state = issue_data.get("state")
        state_reason = issue_data.get("state_reason")
        closed_at = issue_data.get("closed_at")

        # Identify first maintainer or official-bot comment
        first_official_reply_at: str | None = None
        first_official_actor: str | None = None
        for cm in comments_data if isinstance(comments_data, list) else []:
            user_login = (cm.get("user") or {}).get("login")
            assoc = cm.get("author_association")
            if _is_official_actor(user_login, assoc, official_bots):
                first_official_reply_at = cm.get("created_at")
                first_official_actor = user_login
                break

        # Collect same-repo linked PRs, latest reopen timestamp, and closing/referencing commit SHAs from full timeline
        pr_refs: dict[tuple[str, int], dict[str, Any]] = {}
        closing_commit: str | None = None
        reopened_at_dt: dt.datetime | None = None

        for ev in timeline_data if isinstance(timeline_data, list) else []:
            ev_type = ev.get("event")
            if ev_type == "reopened" and ev.get("created_at"):
                rdt = _parse_iso_dt(str(ev["created_at"]))
                if rdt is not None and (reopened_at_dt is None or rdt > reopened_at_dt):
                    reopened_at_dt = rdt
            if ev_type in {"closed", "referenced"} and ev.get("commit_id"):
                cid = str(ev["commit_id"])
                if re.fullmatch(r"[0-9a-f]{40}", cid):
                    closing_commit = cid
            if ev_type == "cross-referenced":
                src = (ev.get("source") or {}).get("issue") or {}
                pr_html = src.get("html_url") or ""
                pm = GH_PR_RE.match(pr_html)
                if pm:
                    pr_repo, pr_num_str = pm.group(1), pm.group(2)
                    if pr_repo.lower() != issue_repo.lower():
                        continue
                    pr_num = int(pr_num_str)
                    pr_refs[(pr_repo, pr_num)] = {"repo": pr_repo, "number": pr_num, "url": pr_html}

        linked_prs: list[dict[str, Any]] = []
        merged_prs: list[dict[str, Any]] = []
        open_pr_info: dict[str, Any] | None = None
        pr_fetch_failed = False

        for (pr_repo, pr_num), _ in sorted(pr_refs.items()):
            try:
                pr_data = _call_fetcher(fetcher, f"repos/{pr_repo}/pulls/{pr_num}", paginate=False)
                if not isinstance(pr_data, dict):
                    raise ValueError("Unexpected non-object pull request response")
            except Exception as exc:  # noqa: BLE001
                pr_fetch_failed = True
                had_api_error = True
                alerts.append(
                    {
                        "repo": repo,
                        "item_id": item_id,
                        "kind": "api_error",
                        "message": f"Failed fetching linked PR {pr_repo}#{pr_num}: {exc}; preserving prior observation.",
                    }
                )
                break
            pr_author = (pr_data.get("user") or {}).get("login")
            pr_assoc = pr_data.get("author_association")
            pr_merged = bool(pr_data.get("merged"))
            merge_sha = pr_data.get("merge_commit_sha") if pr_merged else None
            if merge_sha and not re.fullmatch(r"[0-9a-f]{40}", str(merge_sha)):
                merge_sha = None
            rec = {
                "number": pr_num,
                "url": pr_data.get("html_url") or f"https://github.com/{pr_repo}/pull/{pr_num}",
                "state": pr_data.get("state") or ("closed" if pr_merged else "open"),
                "merged": pr_merged,
                "merge_commit_sha": merge_sha,
                "author_association": pr_assoc,
            }
            linked_prs.append(rec)
            if pr_merged:
                merged_prs.append(
                    {
                        **rec,
                        "merged_at": pr_data.get("merged_at"),
                        "author_type": _classify_pr_author(pr_author, pr_assoc, official_bots),
                    }
                )
            elif rec["state"] == "open" and open_pr_info is None:
                open_pr_info = {
                    **rec,
                    "author_type": _classify_pr_author(pr_author, pr_assoc, official_bots),
                }

        # Never clobber prior observations or transition states on incomplete fetches
        if pr_fetch_failed:
            continue

        new_observed = {
            "github_state": gh_state if gh_state in {"open", "closed"} else None,
            "state_reason": state_reason if state_reason in {"completed", "not_planned", "reopened"} else None,
            "labels": labels,
            "closed_at": closed_at,
            "linked_prs": linked_prs,
        }
        if item.get("observed") != new_observed:
            changes.append(f"{repo} {item['ref']}: observed state updated ({gh_state}/{state_reason}, {len(linked_prs)} PRs)")
        item["observed"] = new_observed

        # Normalize nullable maintainer_response & remediation safely
        mr = _ensure_dict(
            item,
            "maintainer_response",
            {
                "replied": False,
                "first_reply_at": None,
                "first_decision_at": None,
                "actor": None,
                "summary_en": None,
                "summary_zh": None,
            },
        )
        if first_official_reply_at and not mr.get("replied"):
            mr["replied"] = True
            mr["first_reply_at"] = mr.get("first_reply_at") or first_official_reply_at
            mr["actor"] = mr.get("actor") or first_official_actor
            alerts.append(
                {
                    "repo": repo,
                    "item_id": item_id,
                    "kind": "review_maintainer_reply",
                    "message": f"{repo} {item['ref']}: official reply by {first_official_actor} at {first_official_reply_at}",
                }
            )

        rem = _ensure_dict(
            item,
            "remediation",
            {
                "pr_url": None,
                "pr_author_type": None,
                "fix_commit": None,
                "merged_at": None,
                "fix_release": None,
                "verified_at": None,
                "verified_snapshot": None,
                "verification_summary_en": None,
                "verification_summary_zh": None,
            },
        )

        # Recompute machine-owned states from complete observations (never auto-verify!)
        reopened_open = gh_state == "open" and state_reason == "reopened"
        if reopened_open:
            eligible_merged = []
            if reopened_at_dt is not None:
                for pr in merged_prs:
                    mdt = _parse_iso_dt(pr.get("merged_at"))
                    if mdt is not None and mdt > reopened_at_dt:
                        eligible_merged.append(pr)
        else:
            eligible_merged = merged_prs

        effective_merged_pr: dict[str, Any] | None = None
        if eligible_merged:
            effective_merged_pr = max(
                eligible_merged,
                key=lambda pr: (
                    _parse_iso_dt(pr.get("merged_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                    int(pr["number"]),
                ),
            )

        if old_status == "acknowledged":
            if effective_merged_pr or (gh_state == "closed" and state_reason == "completed"):
                item["status"] = "fix_claimed"
                changes.append(f"{repo} {item['ref']}: status acknowledged -> fix_claimed")
                if effective_merged_pr:
                    rem["pr_url"] = effective_merged_pr["url"]
                    rem["pr_author_type"] = effective_merged_pr["author_type"]
                    rem["fix_commit"] = effective_merged_pr["merge_commit_sha"] or closing_commit
                    rem["merged_at"] = effective_merged_pr["merged_at"] or closed_at
                elif closing_commit:
                    rem["pr_url"] = None
                    rem["pr_author_type"] = None
                    rem["fix_commit"] = closing_commit
                    rem["merged_at"] = closed_at
            elif gh_state == "closed" and state_reason == "not_planned":
                alerts.append(
                    {
                        "repo": repo,
                        "item_id": item_id,
                        "kind": "review_not_planned",
                        "message": f"{repo} {item['ref']}: closed as not_planned; review for wontfix with evidence URL",
                    }
                )
        elif old_status not in HUMAN_ADJUDICATED_STATES:
            # Machine-owned states: open, triaged, pr_open, fix_claimed
            if effective_merged_pr or (gh_state == "closed" and state_reason == "completed"):
                if old_status != "fix_claimed":
                    item["status"] = "fix_claimed"
                    changes.append(f"{repo} {item['ref']}: status {old_status} -> fix_claimed")
                if effective_merged_pr:
                    rem["pr_url"] = effective_merged_pr["url"]
                    rem["pr_author_type"] = effective_merged_pr["author_type"]
                    rem["fix_commit"] = effective_merged_pr["merge_commit_sha"] or closing_commit
                    rem["merged_at"] = effective_merged_pr["merged_at"] or closed_at
                elif closing_commit:
                    rem["pr_url"] = None
                    rem["pr_author_type"] = None
                    rem["fix_commit"] = closing_commit
                    rem["merged_at"] = closed_at
            elif gh_state == "closed" and state_reason == "not_planned":
                alerts.append(
                    {
                        "repo": repo,
                        "item_id": item_id,
                        "kind": "review_not_planned",
                        "message": f"{repo} {item['ref']}: closed as not_planned; review for wontfix with evidence URL",
                    }
                )
            elif gh_state == "open":
                # Recompute cleanly if a PR closed unmerged or a fix_claimed issue was reopened
                if open_pr_info:
                    new_st = "pr_open"
                    rem["pr_url"] = open_pr_info["url"]
                    rem["pr_author_type"] = open_pr_info["author_type"]
                    if old_status == "fix_claimed":
                        rem["fix_commit"] = None
                        rem["merged_at"] = None
                else:
                    new_st = "triaged" if labels else "open"
                    if old_status in {"pr_open", "fix_claimed"}:
                        rem["pr_url"] = None
                        rem["pr_author_type"] = None
                        rem["fix_commit"] = None
                        rem["merged_at"] = None
                if new_st != old_status:
                    item["status"] = new_st
                    changes.append(f"{repo} {item['ref']}: status {old_status} -> {new_st}")

        # Emit actionable review alerts
        cur_status = item["status"]
        if cur_status in {"verified_main", "verified_shipped"} and gh_state == "open":
            alerts.append(
                {
                    "repo": repo,
                    "item_id": item_id,
                    "kind": "review_reopened",
                    "message": f"{repo} {item['ref']}: previously verified issue is currently open on GitHub; review for regression.",
                }
            )
        if cur_status == "fix_claimed":
            alerts.append(
                {
                    "repo": repo,
                    "item_id": item_id,
                    "kind": "verify_fix",
                    "message": (
                        f"{repo} {item['ref']}: upstream claimed fix "
                        f"(commit={rem.get('fix_commit') or 'unknown'}, pr={rem.get('pr_url') or 'none'}); "
                        "inspect code diff before marking verified_main / partial_fix / closed_unfixed."
                    ),
                }
            )
        elif cur_status == "verified_main" and latest_rel_dt is not None:
            merged_dt = _parse_iso_dt(rem.get("merged_at"))
            if merged_dt and latest_rel_dt > merged_dt:
                alerts.append(
                    {
                        "repo": repo,
                        "item_id": item_id,
                        "kind": "check_release",
                        "message": (
                            f"{repo} {item['ref']}: upstream released {latest_rel['tag']} ({latest_rel['published_at']}) "
                            f"after fix merge; verify if {rem.get('fix_commit', '')[:8]} shipped to promote to verified_shipped."
                        ),
                    }
                )
        elif cur_status in {"open", "pr_open"} and not mr.get("replied"):
            wait_days = compute_wait_days(item["filed_at"], checked_date)
            if wait_days >= 30:
                alerts.append(
                    {
                        "repo": repo,
                        "item_id": item_id,
                        "kind": "silent_30d",
                        "message": f"{repo} {item['ref']}: {wait_days}d without maintainer reply.",
                    }
                )
            elif wait_days >= 14:
                alerts.append(
                    {
                        "repo": repo,
                        "item_id": item_id,
                        "kind": "silent_14d",
                        "message": f"{repo} {item['ref']}: {wait_days}d without maintainer reply.",
                    }
                )

    if not had_api_error:
        updated["last_checked_at"] = checked_date
    return updated, alerts, changes


def discover_tracking_files(audits_dir: Path = AUDITS_DIR, repo_filter: str | None = None) -> list[Path]:
    if not audits_dir.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(audits_dir.rglob("tracking.json")):
        rel = path.relative_to(audits_dir)
        if len(rel.parts) != 4:
            continue
        _, owner, repo_name, _ = rel.parts
        slug = f"{owner}/{repo_name}"
        if repo_filter and slug.lower() != repo_filter.lower():
            continue
        out.append(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync observed GitHub issue states into tracking.json (never auto-verifies fixes).")
    ap.add_argument("--write", action="store_true", help="Write updated tracking.json files (default is dry-run)")
    ap.add_argument("--repo", default=None, help="Optional filter for a single owner/name repository")
    ap.add_argument("--date", default=None, help="Override checked date YYYY-MM-DD (defaults to UTC today)")
    ap.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    ap.add_argument("--token-env", default="GITHUB_TOKEN", help="Env var holding GitHub API token")
    args = ap.parse_args()

    checked_date = args.date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    token = resolve_github_token(args.token_env)
    repo_filter: str | None = None
    if args.repo is not None:
        repo_filter = args.repo.strip()
        if not repo_filter:
            msg = "Invalid empty --repo filter; omit --repo to check all tracked repositories"
            if args.format == "json":
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "partial_failure": True,
                            "checked_at": checked_date,
                            "write": args.write,
                            "repo_filter": repo_filter,
                            "files_checked": 0,
                            "files_changed": [],
                            "changes": [],
                            "alerts": [{"repo": "", "item_id": None, "kind": "api_error", "message": msg}],
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                print(f"ERROR: {msg}", file=sys.stderr)
            return 1

    files = discover_tracking_files(AUDITS_DIR, repo_filter=repo_filter)
    if repo_filter is not None and not files:
        msg = f"No tracking.json found matching --repo {repo_filter!r}"
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "ok": False,
                        "partial_failure": True,
                        "checked_at": checked_date,
                        "write": args.write,
                        "repo_filter": repo_filter,
                        "files_checked": 0,
                        "files_changed": [],
                        "changes": [],
                        "alerts": [{"repo": repo_filter, "item_id": None, "kind": "api_error", "message": msg}],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    all_alerts: list[dict[str, Any]] = []
    all_changes: list[str] = []
    updated_files: list[str] = []

    def fetcher(api_path: str, paginate: bool = False) -> Any:
        return github_get_json(api_path, token=token, paginate=paginate)

    for path in files:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        new_data, alerts, changes = sync_project_tracking_data(data, fetcher=fetcher, checked_date=checked_date)
        all_alerts.extend(alerts)
        all_changes.extend(changes)
        rendered = json.dumps(new_data, indent=2, ensure_ascii=False) + "\n"
        if rendered != raw_text:
            updated_files.append(path.relative_to(ROOT).as_posix())
            if args.write:
                path.write_text(rendered, encoding="utf-8")

    partial_failure = any(a.get("kind") == "api_error" for a in all_alerts)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "ok": not partial_failure,
                    "partial_failure": partial_failure,
                    "checked_at": checked_date,
                    "write": args.write,
                    "repo_filter": repo_filter,
                    "files_checked": len(files),
                    "files_changed": updated_files,
                    "changes": all_changes,
                    "alerts": all_alerts,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1 if partial_failure else 0

    mode = "WROTE" if args.write else "DRY-RUN"
    print(f"[{mode}] Checked {len(files)} tracking file(s) as of {checked_date}; {len(updated_files)} changed.")
    for c in all_changes:
        print(f"  - {c}")
    if all_alerts:
        print("\nAction / Review Alerts:")
        for a in all_alerts:
            print(f"  [{a['kind']}] {a['message']}")
    return 1 if partial_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
