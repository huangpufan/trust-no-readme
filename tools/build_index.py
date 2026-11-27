#!/usr/bin/env python3
"""Generate data/catalog.json, per-project READMEs, and root README impact & catalog tables.

Single sources of truth:
1. Immutable point-in-time audit snapshots:
   `audits/<forge>/<owner>/<repo>/YYYY-MM-DD-<shortsha>.md` (and optional `.zh-CN.md`).
2. Mutable per-project upstream tracking ledgers:
   `audits/<forge>/<owner>/<repo>/tracking.json` (with graceful fallback to legacy
   frontmatter `upstream_issues` when no `tracking.json` exists).

100% deterministic (never reads wall-clock time; wait durations are computed strictly
from `last_checked_at - filed_at`). Stdlib-only Python 3.11+. Run `--check` in CI.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDITS_DIR = ROOT / "audits"
CATALOG_PATH = ROOT / "data" / "catalog.json"
README_EN = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"

IMPACT_BEGIN = "<!-- IMPACT:BEGIN -->"
IMPACT_END = "<!-- IMPACT:END -->"
CATALOG_BEGIN = "<!-- CATALOG:BEGIN -->"
CATALOG_END = "<!-- CATALOG:END -->"
SNAPSHOT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([0-9a-f]{7,12})\.md$")
TRANSLATION_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([0-9a-f]{7,12})\.(zh-CN)\.md$")
LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
ISSUE_NUM_RE = re.compile(r"/(?:issues|pull|merge_requests)/(\d+)(?:[/?#]|$)")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

HONESTY_RANK = {"H0": 0, "H1": 1, "H2": 2, "H3": 3, "H4": 4}
RISK_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}
VERDICT_RANK = {"Adopt": 0, "Adopt with Config": 1, "Isolate": 2, "Avoid": 3}

# Legacy frontmatter `fixed` has no verified fix_commit proof -> treat as unverified claim only.
LEGACY_STATE_MAP = {
    "open": ("audit_filed", "open"),
    "acknowledged": ("audit_filed", "acknowledged"),
    "fixed": ("audit_filed", "fix_claimed"),
    "wontfix": ("audit_filed", "wontfix"),
    "existing": ("pre_existing", "open"),
}

MAX_README_IMPACT_ROWS = 8


def escape_md_cell(text: Any) -> str:
    """Escape dynamic text for safe single-line Markdown table cell rendering."""
    if text is None:
        return ""
    s = str(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace("|", "\\|")


def escape_md_link_text(text: Any) -> str:
    """Escape text used inside Markdown `[label](url)` link labels (escape `\\` before `|`, `[`, `]`)."""
    if text is None:
        return ""
    s = str(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def safe_md_url(url: Any) -> str:
    """Encode characters that break Markdown inline link destinations."""
    if not url:
        return ""
    s = str(url).strip()
    return s.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("|", "%7C")


def parse_scalar(val: str) -> Any:
    s = val.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        inner = s[1:-1]
        if s.startswith('"'):
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    if s == "true":
        return True
    if s == "false":
        return False
    if s == "null":
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part) for part in inner.split(",")]
    return s


def parse_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("Missing opening YAML frontmatter delimiter '---'")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("Missing closing YAML frontmatter delimiter '---'")

    fm: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw in lines[1:end_idx]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lm = LIST_ITEM_RE.match(raw)
        if lm and current_list_key is not None:
            fm[current_list_key].append(parse_scalar(lm.group(1)))
            continue
        if ":" not in raw:
            raise ValueError(f"Invalid frontmatter line: {raw}")
        k, v = raw.split(":", 1)
        key = k.strip()
        val_str = v.strip()
        if val_str == "":
            fm[key] = []
            current_list_key = key
        else:
            fm[key] = parse_scalar(val_str)
            current_list_key = None

    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    return fm, body


def forge_base_url(forge: str) -> str:
    if forge == "github":
        return "https://github.com"
    if forge == "gitlab":
        return "https://gitlab.com"
    return "https://codeberg.org"


def parse_companion_repos(entries: list[str] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in entries or []:
        if "=" not in raw or "@" not in raw or ":" not in raw:
            continue
        alias, rest = raw.split("=", 1)
        forge, slug_sha = rest.split(":", 1)
        slug, sha = slug_sha.rsplit("@", 1)
        out.append({"alias": alias, "forge": forge, "repo": slug, "sha": sha})
    return out


def parse_upstream_issues(entries: list[str] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in entries or []:
        parts = str(raw).split("|", 2)
        if len(parts) != 3:
            continue
        state, url, label = [p.strip() for p in parts]
        m = ISSUE_NUM_RE.search(url)
        ref = f"#{m.group(1)}" if m else "issue"
        out.append({"state": state, "url": url, "label": label, "ref": ref})
    return out


def parse_iso_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    s = str(value).strip()
    if len(s) >= 10:
        try:
            return dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if "T" not in s:
        return None
    try:
        if s.endswith("Z"):
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def compute_wait_days(filed_at: str | None, last_checked_at: str | None) -> int:
    d_filed = parse_iso_date(filed_at)
    d_checked = parse_iso_date(last_checked_at)
    if d_filed is None or d_checked is None:
        return 0
    return max(0, (d_checked - d_filed).days)


def format_turnaround(start_iso: str | None, end_iso: str | None, lang: str = "en") -> str | None:
    dt1 = parse_iso_datetime(start_iso)
    dt2 = parse_iso_datetime(end_iso)
    if dt1 is not None and dt2 is not None:
        secs = max(0.0, (dt2 - dt1).total_seconds())
        if secs < 3600:
            mins = max(1, int(round(secs / 60.0)))
            return f"{mins}分钟" if lang == "zh" else f"{mins}m"
        hours = secs / 3600.0
        if hours < 48.0:
            h_str = f"{hours:.1f}".rstrip("0").rstrip(".")
            return f"{h_str}小时" if lang == "zh" else f"{h_str}h"
        days = int(round(hours / 24.0))
        return f"{days}天" if lang == "zh" else f"{days}d"

    d1 = parse_iso_date(start_iso)
    d2 = parse_iso_date(end_iso)
    if d1 is not None and d2 is not None:
        days = max(0, (d2 - d1).days)
        if days == 0:
            return "<1天" if lang == "zh" else "<1d"
        return f"{days}天" if lang == "zh" else f"{days}d"
    return None


def _extract_pr_ref(url: str | None) -> str | None:
    if not url:
        return None
    m = ISSUE_NUM_RE.search(url)
    return f"#{m.group(1)}" if m else None


def _project_public_private_disclosure(pd: dict[str, Any]) -> dict[str, Any]:
    """Emit ONLY allowlisted public fields for private disclosures; standardize embargoed copy."""
    status = pd.get("status")
    win = int(pd.get("window_days") or 90)
    if status == "embargoed":
        return {
            "id": pd.get("id"),
            "snapshot": pd.get("snapshot"),
            "reported_at": pd.get("reported_at"),
            "window_days": win,
            "status": "embargoed",
            "summary_en": f"1 withheld security finding under {win}-day coordinated private disclosure (details omitted).",
            "summary_zh": f"1 项处于 {win} 天负责任私密披露期的暂隐安全问题（细节暂隐）。",
        }
    return {
        "id": pd.get("id"),
        "snapshot": pd.get("snapshot"),
        "reported_at": pd.get("reported_at"),
        "window_days": win,
        "status": status,
        "advisory_url": pd.get("advisory_url"),
        "fix_commit": pd.get("fix_commit"),
        "fix_release": pd.get("fix_release"),
        "disclosed_at": pd.get("disclosed_at"),
        "summary_en": pd.get("summary_en"),
        "summary_zh": pd.get("summary_zh"),
    }


def _project_public_item(it: dict[str, Any]) -> dict[str, Any]:
    """Emit ONLY allowlisted public schema fields for tracked items."""
    mr_in = it.get("maintainer_response") if isinstance(it.get("maintainer_response"), dict) else {}
    rem_in = it.get("remediation") if isinstance(it.get("remediation"), dict) else {}
    obs_in = it.get("observed") if isinstance(it.get("observed"), dict) else {}
    events_out = [
        {
            "date": ev.get("date"),
            "kind": ev.get("kind"),
            "url": ev.get("url"),
            "commit": ev.get("commit"),
            "summary_en": ev.get("summary_en"),
            "summary_zh": ev.get("summary_zh"),
        }
        for ev in (it.get("events") or [])
        if isinstance(ev, dict)
    ]
    return {
        "id": it.get("id"),
        "origin": it.get("origin", "audit_filed"),
        "snapshot": it.get("snapshot"),
        "finding_ids": list(it.get("finding_ids") or []),
        "resolved_findings": list(it.get("resolved_findings") or []),
        "url": it.get("url"),
        "ref": it.get("ref"),
        "title_en": it.get("title_en"),
        "title_zh": it.get("title_zh"),
        "latent_days": it.get("latent_days"),
        "filed_at": it.get("filed_at"),
        "status": it.get("status", "open"),
        "maintainer_response": {
            "replied": bool(mr_in.get("replied", False)),
            "first_reply_at": mr_in.get("first_reply_at"),
            "first_decision_at": mr_in.get("first_decision_at"),
            "actor": mr_in.get("actor"),
            "summary_en": mr_in.get("summary_en"),
            "summary_zh": mr_in.get("summary_zh"),
        },
        "remediation": {
            "pr_url": rem_in.get("pr_url"),
            "pr_author_type": rem_in.get("pr_author_type"),
            "fix_commit": rem_in.get("fix_commit"),
            "merged_at": rem_in.get("merged_at"),
            "fix_release": rem_in.get("fix_release"),
            "verified_at": rem_in.get("verified_at"),
            "verified_snapshot": rem_in.get("verified_snapshot"),
            "verification_summary_en": rem_in.get("verification_summary_en"),
            "verification_summary_zh": rem_in.get("verification_summary_zh"),
        },
        "resolution_evidence_url": it.get("resolution_evidence_url"),
        "observed": {
            "github_state": obs_in.get("github_state"),
            "state_reason": obs_in.get("state_reason"),
            "labels": list(obs_in.get("labels") or []),
            "closed_at": obs_in.get("closed_at"),
            "linked_prs": list(obs_in.get("linked_prs") or []),
        },
        "events": events_out,
    }


def load_project_tracking(
    proj_dir: Path,
    forge: str,
    slug: str,
    latest_snap: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    tpath = proj_dir / "tracking.json"
    if tpath.is_file():
        data = json.loads(tpath.read_text(encoding="utf-8"))
        return True, data

    # Legacy fallback from latest snapshot frontmatter
    lfm = latest_snap["frontmatter"]
    audited_at = str(lfm.get("audited_at", ""))
    items: list[dict[str, Any]] = []
    for idx, iss in enumerate(parse_upstream_issues(lfm.get("upstream_issues")), start=1):
        origin, status = LEGACY_STATE_MAP.get(iss["state"], ("audit_filed", "open"))
        items.append(
            {
                "id": f"legacy-{idx}",
                "origin": origin,
                "snapshot": latest_snap["filename"],
                "finding_ids": [],
                "resolved_findings": [],
                "url": iss["url"],
                "ref": iss["ref"],
                "title_en": iss["label"],
                "title_zh": iss["label"],
                "latent_days": None,
                "filed_at": audited_at,
                "status": status,
                "maintainer_response": {"replied": status in {"acknowledged", "fix_claimed", "wontfix"}},
                "remediation": {},
                "resolution_evidence_url": None,
                "observed": {},
                "events": [],
            }
        )
    return False, {
        "schema_version": 1,
        "repo": slug,
        "forge": forge,
        "official_bots": [],
        "last_checked_at": audited_at,
        "latest_upstream_release": None,
        "items": items,
        "private_disclosures": [],
    }


def compute_peak_history(snap_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute initial & worst-case historical peak across all immutable snapshots without fabricating pairs."""
    latest = snap_records[0]
    oldest = snap_records[-1]

    def h_rank(s: dict[str, Any]) -> int:
        return HONESTY_RANK.get(str(s.get("honesty")), -1)

    def r_rank(s: dict[str, Any]) -> int:
        return RISK_RANK.get(str(s.get("risk")), -1)

    def v_rank(s: dict[str, Any]) -> int:
        return VERDICT_RANK.get(str(s.get("verdict")), -1)

    chronological = list(reversed(snap_records))
    peak_h_snap = max(chronological, key=lambda s: (h_rank(s), r_rank(s)))
    peak_r_snap = max(chronological, key=lambda s: (r_rank(s), h_rank(s)))
    peak_combined = max(chronological, key=lambda s: (h_rank(s), r_rank(s), v_rank(s)))

    improved_from_peak = (
        h_rank(latest) < h_rank(peak_h_snap)
        or r_rank(latest) < r_rank(peak_r_snap)
        or v_rank(latest) < v_rank(peak_combined)
    )
    peak_axes_split = peak_h_snap["file"] != peak_r_snap["file"]
    return {
        "initial_audited_at": oldest["audited_at"],
        "initial_release": oldest["release"],
        "initial_short_sha": oldest["short_sha"],
        "initial_honesty": oldest["honesty"],
        "initial_risk": oldest["risk"],
        "initial_verdict": oldest["verdict"],
        "peak_honesty": peak_h_snap["honesty"],
        "peak_honesty_release": peak_h_snap["release"],
        "peak_honesty_short_sha": peak_h_snap["short_sha"],
        "peak_honesty_file": peak_h_snap["file"],
        "peak_risk": peak_r_snap["risk"],
        "peak_risk_release": peak_r_snap["release"],
        "peak_risk_short_sha": peak_r_snap["short_sha"],
        "peak_risk_file": peak_r_snap["file"],
        "peak_axes_split": peak_axes_split,
        "peak_combined_honesty": peak_combined["honesty"],
        "peak_combined_risk": peak_combined["risk"],
        "peak_release": peak_combined["release"],
        "peak_short_sha": peak_combined["short_sha"],
        "peak_file": peak_combined["file"],
        "improved_from_peak": improved_from_peak,
    }


def format_peak_note(hist: dict[str, Any], lang: str, relative_to_project: bool = False) -> str:
    """Render an accurate peak summary that never assigns two different snapshots' axes to one release."""
    if not hist["peak_axes_split"]:
        rev_label = escape_md_cell(hist["peak_release"]) if hist.get("peak_release") else hist["peak_short_sha"]
        if relative_to_project:
            sha_link = f"[`{hist['peak_short_sha']}`](./{Path(hist['peak_file']).name})"
            rel_prefix = f"`{escape_md_cell(hist['peak_release'])}` / " if hist.get("peak_release") else ""
            return f"`{hist['peak_honesty']} / {hist['peak_risk']}` ({rel_prefix}{sha_link})"
        if lang == "zh":
            return f"_(历史峰值 `{hist['peak_honesty']}/{hist['peak_risk']}` @ {rev_label})_"
        return f"_(peak `{hist['peak_honesty']}/{hist['peak_risk']}` @ {rev_label})_"

    # Peak H and Peak R occurred on different snapshots: display each axis with its own snapshot!
    h_rev = escape_md_cell(hist["peak_honesty_release"]) if hist.get("peak_honesty_release") else hist["peak_honesty_short_sha"]
    r_rev = escape_md_cell(hist["peak_risk_release"]) if hist.get("peak_risk_release") else hist["peak_risk_short_sha"]
    if relative_to_project:
        h_link = f"[`{hist['peak_honesty_short_sha']}`](./{Path(hist['peak_honesty_file']).name})"
        r_link = f"[`{hist['peak_risk_short_sha']}`](./{Path(hist['peak_risk_file']).name})"
        h_pre = f"`{escape_md_cell(hist['peak_honesty_release'])}` / " if hist.get("peak_honesty_release") else ""
        r_pre = f"`{escape_md_cell(hist['peak_risk_release'])}` / " if hist.get("peak_risk_release") else ""
        return f"`{hist['peak_honesty']}` ({h_pre}{h_link}) · `{hist['peak_risk']}` ({r_pre}{r_link})"
    if lang == "zh":
        return f"_(历史峰值 `{hist['peak_honesty']}` @ {h_rev}、`{hist['peak_risk']}` @ {r_rev})_"
    return f"_(peak `{hist['peak_honesty']}` @ {h_rev}, `{hist['peak_risk']}` @ {r_rev})_"


def derive_remediation_posture(items: list[dict[str, Any]], last_checked_at: str | None) -> dict[str, Any]:
    counts: dict[str, int] = {
        "total": len(items),
        "audit_filed": 0,
        "pre_existing": 0,
        "upstream_proactive": 0,
        "verified_shipped": 0,
        "verified_main": 0,
        "partial_fix": 0,
        "fix_claimed": 0,
        "acknowledged": 0,
        "pr_open_maintainer": 0,
        "pr_open_external": 0,
        "triaged": 0,
        "open": 0,
        "wontfix": 0,
        "closed_unfixed": 0,
        "regressed": 0,
        "silent_14d_plus": 0,
        "silent_30d_plus": 0,
        "maintainer_replied": 0,
    }
    if not items:
        return {
            "code": "no_issues",
            "label_en": "No Public Issues",
            "label_zh": "无公开提单",
            "max_silent_days": 0,
            "counts": counts,
        }

    unreplied_open_count = 0
    max_silent_days = 0
    for it in items:
        origin = it.get("origin", "audit_filed")
        if origin in counts:
            counts[origin] += 1
        st = it.get("status", "open")
        mr = it.get("maintainer_response") or {}
        rem = it.get("remediation") or {}
        replied = bool(mr.get("replied"))
        if replied:
            counts["maintainer_replied"] += 1

        if st == "pr_open":
            if rem.get("pr_author_type") == "maintainer":
                counts["pr_open_maintainer"] += 1
            else:
                counts["pr_open_external"] += 1
        elif st in counts:
            counts[st] += 1

        if st in {"open", "pr_open"} and not replied:
            unreplied_open_count += 1
            wd = compute_wait_days(it.get("filed_at"), last_checked_at)
            max_silent_days = max(max_silent_days, wd)
            if wd >= 30:
                counts["silent_30d_plus"] += 1
            elif wd >= 14:
                counts["silent_14d_plus"] += 1

    verified_total = counts["verified_shipped"] + counts["verified_main"]
    positive_action = (
        verified_total
        + counts["partial_fix"]
        + counts["acknowledged"]
        + counts["pr_open_maintainer"]
    )
    declined_total = counts["wontfix"] + counts["closed_unfixed"]

    if counts["regressed"] > 0:
        code, en, zh = "regressed", "Regression Detected", "修复后回归"
    elif counts["verified_shipped"] == counts["total"]:
        code, en, zh = "remediated_shipped", "Remediated (Shipped)", "已修复发版"
    elif verified_total == counts["total"]:
        code, en, zh = "fixed_on_main", "Fixed on Main (Unreleased)", "主干已全修·待发版"
    elif declined_total > 0 and positive_action > 0:
        code, en, zh = "mixed_response", "Mixed Response", "部分修复·部分拒绝"
    elif positive_action > 0 and counts["silent_30d_plus"] > 0:
        code, en, zh = "mixed_response", "Mixed Response", "部分跟进·部分静默"
    elif declined_total > 0 and positive_action == 0:
        code, en, zh = "declined_unfixed", "Declined / Unfixed", "拒绝修复/未修关闭"
    elif positive_action > 0:
        code, en, zh = "active_remediation", "Active Remediation", "积极修复中"
    elif counts["fix_claimed"] > 0:
        code, en, zh = "pending_verification", "Fix Claimed (Unverified)", "上游称已修·待验真"
    elif unreplied_open_count == counts["total"] and counts["silent_30d_plus"] > 0:
        code = "no_response_30d"
        en = f"No Response ({max_silent_days}d)"
        zh = f"静默未回应 ({max_silent_days}天)"
    elif unreplied_open_count == counts["total"] and counts["silent_14d_plus"] > 0:
        code = "no_response_14d"
        en = f"No Response ({max_silent_days}d)"
        zh = f"静默未回应 ({max_silent_days}天)"
    elif counts["pr_open_external"] > 0 and counts["maintainer_replied"] == 0:
        code, en, zh = "community_prs_only", "Community PRs Only", "仅社区 PR·维护者未回复"
    else:
        code, en, zh = "triaged_open", "Triaged / Open", "已分诊/跟进中"

    return {
        "code": code,
        "label_en": en,
        "label_zh": zh,
        "max_silent_days": max_silent_days,
        "counts": counts,
    }


def discover_snapshots(audits_dir: Path = AUDITS_DIR, root_dir: Path = ROOT) -> dict[tuple[str, str], list[dict[str, Any]]]:
    projects: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    if not audits_dir.is_dir():
        return projects
    for path in sorted(audits_dir.rglob("*.md")):
        if path.name in {"README.md", "responses.md"}:
            continue
        rel = path.relative_to(audits_dir)
        if len(rel.parts) != 4:
            continue
        forge, owner, repo_name, filename = rel.parts
        m = SNAPSHOT_RE.match(filename)
        if not m:
            continue
        fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        translations: dict[str, str] = {}
        zh_sibling = path.with_name(f"{m.group(1)}-{m.group(2)}.zh-CN.md")
        if zh_sibling.is_file():
            translations["zh-CN"] = zh_sibling.relative_to(root_dir).as_posix()
        projects[(forge, f"{owner}/{repo_name}")].append(
            {
                "file": path.relative_to(root_dir).as_posix(),
                "filename": filename,
                "date_from_name": m.group(1),
                "short_sha_from_name": m.group(2),
                "frontmatter": fm,
                "translations": translations,
            }
        )
    for key in projects:
        projects[key].sort(
            key=lambda s: (
                str(s["frontmatter"].get("audited_at", s["date_from_name"])),
                s["filename"],
            ),
            reverse=True,
        )
    return projects


def build_catalog_data(
    projects: dict[tuple[str, str], list[dict[str, Any]]],
    audits_dir: Path = AUDITS_DIR,
) -> dict[str, Any]:
    project_entries: list[dict[str, Any]] = []
    by_tag: dict[str, list[str]] = collections.defaultdict(list)
    verified_fixes: list[dict[str, Any]] = []

    impact_summary: dict[str, int] = {
        "projects_audited": 0,
        "snapshots_verified": 0,
        "audit_filed_issues": 0,
        "pre_existing_tracked": 0,
        "private_cvd_embargoed": 0,
        "private_cvd_fixed": 0,
        "verified_fixes_total": 0,
        "verified_fixes_shipped": 0,
        "verified_fixes_main": 0,
        "partial_fixes": 0,
        "acknowledged_or_in_progress": 0,
        "declined_or_unfixed": 0,
        "silent_14d_plus": 0,
        "silent_30d_plus": 0,
    }

    for (forge, slug), snaps in sorted(projects.items(), key=lambda kv: kv[0][1].lower()):
        latest = snaps[0]
        lfm = latest["frontmatter"]
        tags = [str(t) for t in lfm.get("tags", [])]
        for t in tags:
            by_tag[t].append(slug)

        proj_dir = audits_dir / forge / slug
        has_responses = (proj_dir / "responses.md").is_file()
        has_tracking, tracking = load_project_tracking(proj_dir, forge, slug, latest)

        snap_records = []
        for idx, s in enumerate(snaps):
            fm = s["frontmatter"]
            eff_status = fm.get("status", "active")
            if idx > 0 and eff_status == "active":
                eff_status = "superseded"
            snap_records.append(
                {
                    "file": s["file"],
                    "translations": s["translations"],
                    "sha": fm.get("sha"),
                    "short_sha": str(fm.get("sha", ""))[:8],
                    "release": fm.get("release"),
                    "audited_at": fm.get("audited_at"),
                    "honesty": fm.get("honesty"),
                    "h3_scope": fm.get("h3_scope"),
                    "risk": fm.get("risk"),
                    "verdict": fm.get("verdict"),
                    "evidence_max": fm.get("evidence_max"),
                    "coverage": fm.get("coverage"),
                    "verification": fm.get("verification"),
                    "git_depth": fm.get("git_depth"),
                    "disclosure": fm.get("disclosure"),
                    "status": eff_status,
                    "companion_repos": parse_companion_repos(fm.get("companion_repos")),
                    "upstream_issues": parse_upstream_issues(fm.get("upstream_issues")),
                }
            )

        history = compute_peak_history(snap_records)
        snap_release_by_name = {Path(s["file"]).name: s.get("release") for s in snap_records}
        items = [_project_public_item(it) for it in (tracking.get("items") or []) if isinstance(it, dict)]
        pds = [_project_public_private_disclosure(pd) for pd in (tracking.get("private_disclosures") or []) if isinstance(pd, dict)]
        last_checked_at = tracking.get("last_checked_at")
        latest_upstream_release = tracking.get("latest_upstream_release")
        posture = derive_remediation_posture(items, last_checked_at)
        upstream_url = f"{forge_base_url(forge)}/{slug}"
        project_readme = f"audits/{forge}/{slug}/README.md"

        # Aggregate impact counters & verified fixes showcase (strictly requiring commit proof!)
        for it in items:
            origin = it.get("origin", "audit_filed")
            st = it.get("status", "open")
            mr = it.get("maintainer_response") or {}
            rem = it.get("remediation") or {}
            if origin == "pre_existing":
                impact_summary["pre_existing_tracked"] += 1
                continue
            if origin == "audit_filed":
                impact_summary["audit_filed_issues"] += 1
                fix_commit = rem.get("fix_commit")
                has_commit_proof = isinstance(fix_commit, str) and bool(SHA40_RE.fullmatch(fix_commit))
                fids_list = list(it.get("finding_ids") or [])
                resolved_list = list(it.get("resolved_findings") or [])
                all_findings_resolved = bool(fids_list) and set(resolved_list) == set(fids_list)
                if st == "verified_shipped" and has_commit_proof and rem.get("fix_release") and all_findings_resolved:
                    impact_summary["verified_fixes_shipped"] += 1
                    impact_summary["verified_fixes_total"] += 1
                elif st == "verified_main" and has_commit_proof and all_findings_resolved:
                    impact_summary["verified_fixes_main"] += 1
                    impact_summary["verified_fixes_total"] += 1
                elif st == "partial_fix":
                    impact_summary["partial_fixes"] += 1
                elif st in {"acknowledged", "fix_claimed"} or (st == "pr_open" and rem.get("pr_author_type") == "maintainer"):
                    impact_summary["acknowledged_or_in_progress"] += 1
                elif st in {"wontfix", "closed_unfixed"}:
                    impact_summary["declined_or_unfixed"] += 1
                if st in {"open", "pr_open"} and not mr.get("replied"):
                    wd = compute_wait_days(it.get("filed_at"), last_checked_at)
                    if wd >= 30:
                        impact_summary["silent_30d_plus"] += 1
                    elif wd >= 14:
                        impact_summary["silent_14d_plus"] += 1

                if has_commit_proof and all_findings_resolved and (
                    st == "verified_main" or (st == "verified_shipped" and rem.get("fix_release"))
                ):
                    merged_or_verified = rem.get("merged_at") or rem.get("verified_at")
                    verified_fixes.append(
                        {
                            "kind": "audit_filed",
                            "forge": forge,
                            "repo": slug,
                            "upstream_url": upstream_url,
                            "project_readme": project_readme,
                            "ref": it.get("ref"),
                            "url": it.get("url"),
                            "finding_ids": resolved_list,
                            "resolved_findings": resolved_list,
                            "title_en": it.get("title_en"),
                            "title_zh": it.get("title_zh"),
                            "latent_days": it.get("latent_days"),
                            "filed_at": it.get("filed_at"),
                            "merged_at": rem.get("merged_at"),
                            "turnaround_en": format_turnaround(it.get("filed_at"), merged_or_verified, "en"),
                            "turnaround_zh": format_turnaround(it.get("filed_at"), merged_or_verified, "zh"),
                            "status": st,
                            "pr_url": rem.get("pr_url"),
                            "pr_ref": _extract_pr_ref(rem.get("pr_url")),
                            "fix_commit": fix_commit,
                            "fix_short_sha": fix_commit[:8],
                            "fix_release": rem.get("fix_release"),
                            "audited_release": snap_release_by_name.get(str(it.get("snapshot") or ""), lfm.get("release")),
                            "verified_at": rem.get("verified_at"),
                            "verified_snapshot": rem.get("verified_snapshot"),
                            "verification_summary_en": rem.get("verification_summary_en"),
                            "verification_summary_zh": rem.get("verification_summary_zh"),
                        }
                    )

        for pd in pds:
            pst = pd.get("status")
            if pst == "embargoed":
                impact_summary["private_cvd_embargoed"] += 1
            elif pst == "disclosed_fixed":
                fix_commit = pd.get("fix_commit")
                has_commit_proof = isinstance(fix_commit, str) and bool(SHA40_RE.fullmatch(fix_commit))
                if not has_commit_proof:
                    continue
                impact_summary["private_cvd_fixed"] += 1
                impact_summary["verified_fixes_total"] += 1
                is_shipped = bool(pd.get("fix_release"))
                if is_shipped:
                    impact_summary["verified_fixes_shipped"] += 1
                else:
                    impact_summary["verified_fixes_main"] += 1
                verified_fixes.append(
                    {
                        "kind": "private_cvd",
                        "forge": forge,
                        "repo": slug,
                        "upstream_url": upstream_url,
                        "project_readme": project_readme,
                        "ref": pd.get("id"),
                        "url": pd.get("advisory_url") or upstream_url,
                        "finding_ids": [],
                        "resolved_findings": [],
                        "title_en": pd.get("summary_en"),
                        "title_zh": pd.get("summary_zh"),
                        "latent_days": None,
                        "filed_at": pd.get("reported_at"),
                        "merged_at": pd.get("disclosed_at"),
                        "turnaround_en": format_turnaround(pd.get("reported_at"), pd.get("disclosed_at"), "en"),
                        "turnaround_zh": format_turnaround(pd.get("reported_at"), pd.get("disclosed_at"), "zh"),
                        "status": "verified_shipped" if is_shipped else "verified_main",
                        "pr_url": pd.get("advisory_url"),
                        "pr_ref": None,
                        "fix_commit": fix_commit,
                        "fix_short_sha": fix_commit[:8],
                        "fix_release": pd.get("fix_release"),
                        "audited_release": snap_release_by_name.get(str(pd.get("snapshot") or ""), lfm.get("release")),
                        "verified_at": pd.get("disclosed_at"),
                        "verified_snapshot": None,
                        "verification_summary_en": pd.get("summary_en"),
                        "verification_summary_zh": pd.get("summary_zh"),
                    }
                )

        compat_issues = [
            {
                "state": "existing" if (it.get("origin") == "pre_existing" and it.get("status") == "open") else it.get("status", "open"),
                "url": it.get("url", ""),
                "label": it.get("title_en", ""),
                "ref": it.get("ref", "issue"),
                "origin": it.get("origin", "audit_filed"),
                "status": it.get("status", "open"),
            }
            for it in items
        ]

        project_entries.append(
            {
                "forge": forge,
                "repo": slug,
                "upstream_url": upstream_url,
                "project_readme": project_readme,
                "latest_report": latest["file"],
                "latest_translations": latest["translations"],
                "latest_sha": lfm.get("sha"),
                "latest_short_sha": str(lfm.get("sha", ""))[:8],
                "release": lfm.get("release"),
                "audited_at": lfm.get("audited_at"),
                "category": lfm.get("category"),
                "honesty": lfm.get("honesty"),
                "h3_scope": lfm.get("h3_scope"),
                "risk": lfm.get("risk"),
                "verdict": lfm.get("verdict"),
                "evidence_max": lfm.get("evidence_max"),
                "coverage": lfm.get("coverage"),
                "verification": lfm.get("verification"),
                "disclosure": lfm.get("disclosure"),
                "status": lfm.get("status"),
                "headline_en": lfm.get("headline_en"),
                "headline_zh": lfm.get("headline_zh"),
                "companion_repos": parse_companion_repos(fm_get := lfm.get("companion_repos")),
                "upstream_issues": compat_issues,
                "tags": tags,
                "has_responses": has_responses,
                "has_tracking": has_tracking,
                "history": history,
                "remediation": {
                    "last_checked_at": last_checked_at,
                    "latest_upstream_release": latest_upstream_release,
                    "posture": {
                        "code": posture["code"],
                        "label_en": posture["label_en"],
                        "label_zh": posture["label_zh"],
                        "max_silent_days": posture["max_silent_days"],
                    },
                    "counts": posture["counts"],
                    "items": items,
                    "private_disclosures": pds,
                },
                "snapshot_count": len(snap_records),
                "snapshots": snap_records,
            }
        )
        del fm_get

    project_entries.sort(key=lambda p: (str(p.get("audited_at", "")), str(p.get("repo", "")).lower()), reverse=True)
    verified_fixes.sort(
        key=lambda vf: (str(vf.get("verified_at") or ""), str(vf.get("merged_at") or ""), str(vf.get("repo") or "").lower()),
        reverse=True,
    )
    impact_summary["projects_audited"] = len(project_entries)
    impact_summary["snapshots_verified"] = sum(p["snapshot_count"] for p in project_entries)

    return {
        "schema_version": 3,
        "project_count": len(project_entries),
        "snapshot_count": impact_summary["snapshots_verified"],
        "impact_summary": impact_summary,
        "verified_fixes": verified_fixes,
        "by_tag": {k: sorted(v) for k, v in sorted(by_tag.items())},
        "projects": project_entries,
    }


def format_issue_badge(item: dict[str, Any], last_checked_at: str | None, lang: str) -> str:
    ref = escape_md_link_text(item.get("ref", "issue"))
    url = safe_md_url(item.get("url", ""))
    st = item.get("status", "open")
    origin = item.get("origin", "audit_filed")
    mr = item.get("maintainer_response") or {}
    rem = item.get("remediation") or {}
    link = f"[{ref}]({url})"

    def _wrap(icon: str, note: str, use_muted_icon_for_existing: bool = True) -> str:
        if origin == "pre_existing":
            prefix = "已有 · " if lang == "zh" else "existing · "
            eff_icon = "·" if use_muted_icon_for_existing else icon
            return f"{eff_icon} {link} ({prefix}{note})"
        if origin == "upstream_proactive":
            prefix = "上游主动 · " if lang == "zh" else "proactive · "
            return f"{icon} {link} ({prefix}{note})"
        return f"{icon} {link} ({note})"

    if st == "verified_shipped":
        rel = escape_md_cell(rem.get("fix_release")) if rem.get("fix_release") else ""
        note = (f"已发版 {rel}" if rel else "已发版") if lang == "zh" else (f"shipped {rel}" if rel else "shipped")
        return _wrap("✅", note, use_muted_icon_for_existing=False)
    if st == "verified_main":
        note = "主干已验真" if lang == "zh" else "verified: main"
        return _wrap("✓", note, use_muted_icon_for_existing=False)
    if st == "partial_fix":
        note = "部分修复" if lang == "zh" else "partial fix"
        return _wrap("◐", note, use_muted_icon_for_existing=False)
    if st == "fix_claimed":
        note = "待验真" if lang == "zh" else "fix claimed"
        return _wrap("⏳", note)
    if st == "acknowledged":
        note = "已确认待修" if lang == "zh" else "accepted"
        return _wrap("◐", note)
    if st == "pr_open":
        pr_ref = _extract_pr_ref(rem.get("pr_url"))
        if rem.get("pr_author_type") == "maintainer":
            note = (f"官方 PR {pr_ref}" if pr_ref else "官方 PR") if lang == "zh" else (f"maintainer PR {pr_ref}" if pr_ref else "maintainer PR")
            return _wrap("◐", note)
        note = (f"社区 PR {pr_ref}" if pr_ref else "社区 PR") if lang == "zh" else (f"ext PR {pr_ref}" if pr_ref else "ext PR")
        return _wrap("○", note)
    if st == "triaged":
        note = "已分诊" if lang == "zh" else "triaged"
        return _wrap("○", note)
    if st == "wontfix":
        note = "拒绝修复" if lang == "zh" else "wontfix"
        return _wrap("✗", note, use_muted_icon_for_existing=False)
    if st == "closed_unfixed":
        note = "未修关闭" if lang == "zh" else "closed unfixed"
        return _wrap("✗", note, use_muted_icon_for_existing=False)
    if st == "regressed":
        note = "修复后回归" if lang == "zh" else "regressed"
        return _wrap("⚠", note, use_muted_icon_for_existing=False)

    # status == "open"
    wd = compute_wait_days(item.get("filed_at"), last_checked_at)
    if not mr.get("replied") and wd >= 14:
        note = f"静默 {wd}天" if lang == "zh" else f"silent {wd}d"
        return _wrap("⏸", note)
    return _wrap("○", "open")


def format_upstream_response_cell(proj: dict[str, Any], lang: str) -> str:
    rem_block = proj["remediation"]
    items = rem_block["items"]
    if not items:
        return "待提交" if lang == "zh" else "Pending"

    last_checked_at = rem_block.get("last_checked_at")
    lines: list[str] = []
    if proj.get("has_tracking"):
        posture_label = rem_block["posture"]["label_zh"] if lang == "zh" else rem_block["posture"]["label_en"]
        lines.append(f"**{escape_md_cell(posture_label)}**")
    for it in items:
        lines.append(format_issue_badge(it, last_checked_at, lang))
    return "<br>".join(lines)


def format_report_links(
    report_rel: str,
    translations: dict[str, str],
    lang: str,
    relative_to_project: bool = False,
    snapshot_count: int = 1,
    project_readme: str | None = None,
) -> str:
    en_href = f"./{Path(report_rel).name}" if relative_to_project else f"./{report_rel}"
    en_label = "EN" if translations else ("EN 报告" if lang == "zh" else "Report")
    links = [f"[{en_label}]({safe_md_url(en_href)})"]
    if "zh-CN" in translations:
        zh_href = f"./{Path(translations['zh-CN']).name}" if relative_to_project else f"./{translations['zh-CN']}"
        links.append(f"[中文]({safe_md_url(zh_href)})")
    out = " · ".join(links)
    if not relative_to_project and snapshot_count > 1 and project_readme:
        hist_label = f"历史 ({snapshot_count})" if lang == "zh" else f"History ({snapshot_count})"
        out += f"<br>[{hist_label}](./{safe_md_url(project_readme)})"
    return out


def render_impact_block(catalog: dict[str, Any], lang: str) -> str:
    s = catalog["impact_summary"]
    fixes = catalog["verified_fixes"]

    if lang == "zh":
        extra_bits: list[str] = []
        if s["pre_existing_tracked"]:
            extra_bits.append(f"另追踪已有 Issue {s['pre_existing_tracked']} 个")
        if s["private_cvd_embargoed"]:
            extra_bits.append(f"私密披露期 {s['private_cvd_embargoed']} 项")
        extra_note = f"（{'、'.join(extra_bits)}）" if extra_bits else ""
        lines = [
            (
                f"已审计 **{s['projects_audited']}** 个项目（**{s['snapshots_verified']}** 份 SHA 快照） · "
                f"主动提交上游 Issue **{s['audit_filed_issues']}** 个{extra_note} · "
                f"**{s['verified_fixes_total']}** 项已验证修复（主干已合 `{s['verified_fixes_main']}` / 已发版 `{s['verified_fixes_shipped']}`） · "
                f"**{s['acknowledged_or_in_progress']}** 项已确认待修 · "
                f"**{s['declined_or_unfixed']}** 项拒绝或未修关闭"
            )
        ]
        if not fixes:
            lines.extend(["", "_暂未产生已通过源码验真的上游修复；所有已提交 Issue 的实时状态见下方总表。_"])
        else:
            lines.extend([
                "",
                "### 已验证修复成果（独立监督推动）",
                "",
                "| 项目 | 推动修复的 Issue 与缺陷 | 潜伏期 → 修复耗时 | 修复证明与发版状态 | 验真记录 |",
                "|---|---|---|---|---|",
            ])
            for vf in fixes[:MAX_README_IMPACT_ROWS]:
                fids = "/".join(vf["finding_ids"])
                ref_s = escape_md_link_text(vf["ref"])
                issue_cell = f"[{ref_s}]({safe_md_url(vf['url'])})" + (f" · `{fids}`" if fids else "") + f"<br>{escape_md_cell(vf['title_zh'])}"
                if vf.get("latent_days") is not None and vf.get("turnaround_zh"):
                    time_cell = f"存在 `{vf['latent_days']}` 天 → **{vf['turnaround_zh']}** 合入"
                elif vf.get("turnaround_zh"):
                    time_cell = f"**{vf['turnaround_zh']}**"
                else:
                    time_cell = "无"
                proof_parts: list[str] = []
                if vf.get("fix_commit"):
                    proof_parts.append(f"Commit [`{vf['fix_short_sha']}`]({safe_md_url(vf['upstream_url'])}/commit/{vf['fix_commit']})")
                if vf.get("pr_url") and vf.get("pr_ref"):
                    proof_parts.append(f"PR [{escape_md_link_text(vf['pr_ref'])}]({safe_md_url(vf['pr_url'])})")
                proof_head = " · ".join(proof_parts)
                if vf["status"] == "verified_shipped":
                    ship_line = f"**已发版 `{escape_md_cell(vf['fix_release'])}`**"
                else:
                    aud_rel = f"`{escape_md_cell(vf['audited_release'])}` 仍受影响" if vf.get("audited_release") else "尚未发版"
                    ship_line = f"**主干已合（待发版，{aud_rel}）**"
                proof_cell = f"{proof_head}<br>{ship_line}" if proof_head else ship_line
                ver_cell = f"`{vf['verified_at']}` 源码验真"
                if vf.get("verified_snapshot"):
                    ver_cell += f"<br>[复审快照](./audits/{vf['forge']}/{vf['repo']}/{safe_md_url(vf['verified_snapshot'])})"
                lines.append(
                    f"| [`{escape_md_link_text(vf['repo'])}`](./{safe_md_url(vf['project_readme'])}) | {issue_cell} | {time_cell} | {proof_cell} | {ver_cell} |"
                )
        lines.extend([
            "",
            "> **历史保留口径**：已验证修复记录的是独立监督推动的真实改进与上游改错意愿，**绝不抹除或改写旧快照**；项目复审更新最新评级后，历史峰值评级与原快照仍永久保留并列展示。所有修复均需源码验真且由 CI 机械核验上游 commit 存在与祖先关系。",
        ])
        return "\n".join(lines)

    # English
    extra_bits_en: list[str] = []
    if s["pre_existing_tracked"]:
        extra_bits_en.append(f"+{s['pre_existing_tracked']} pre-existing tracked")
    if s["private_cvd_embargoed"]:
        extra_bits_en.append(f"{s['private_cvd_embargoed']} private CVD")
    extra_note_en = f" ({', '.join(extra_bits_en)})" if extra_bits_en else ""
    lines_en = [
        (
            f"**{s['projects_audited']}** project(s) audited (**{s['snapshots_verified']}** pinned snapshots) · "
            f"**{s['audit_filed_issues']}** upstream issue(s) filed{extra_note_en} · "
            f"**{s['verified_fixes_total']}** verified fix(es) (`{s['verified_fixes_main']}` merged on `main`, `{s['verified_fixes_shipped']}` shipped) · "
            f"**{s['acknowledged_or_in_progress']}** maintainer-accepted · "
            f"**{s['declined_or_unfixed']}** declined or closed unfixed"
        )
    ]
    if not fixes:
        lines_en.extend(["", "_No upstream fixes have passed independent code verification yet. Live status for every filed issue is tracked below._"])
    else:
        lines_en.extend([
            "",
            "### Verified Fixes Driven by Independent Audits",
            "",
            "| Project | Issue & Finding Fixed | Latency -> Turnaround | Fix Proof & Release Status | Verification |",
            "|---|---|---|---|---|",
        ])
        for vf in fixes[:MAX_README_IMPACT_ROWS]:
            fids = "/".join(vf["finding_ids"])
            ref_s = escape_md_link_text(vf["ref"])
            issue_cell = f"[{ref_s}]({safe_md_url(vf['url'])})" + (f" · `{fids}`" if fids else "") + f"<br>{escape_md_cell(vf['title_en'])}"
            if vf.get("latent_days") is not None and vf.get("turnaround_en"):
                time_cell = f"`{vf['latent_days']}d` latent -> **{vf['turnaround_en']}** to merge"
            elif vf.get("turnaround_en"):
                time_cell = f"**{vf['turnaround_en']}**"
            else:
                time_cell = "n/a"
            proof_parts_en: list[str] = []
            if vf.get("fix_commit"):
                proof_parts_en.append(f"Commit [`{vf['fix_short_sha']}`]({safe_md_url(vf['upstream_url'])}/commit/{vf['fix_commit']})")
            if vf.get("pr_url") and vf.get("pr_ref"):
                proof_parts_en.append(f"PR [{escape_md_link_text(vf['pr_ref'])}]({safe_md_url(vf['pr_url'])})")
            proof_head_en = " · ".join(proof_parts_en)
            if vf["status"] == "verified_shipped":
                ship_line_en = f"**Shipped in `{escape_md_cell(vf['fix_release'])}`**"
            else:
                aud_rel_en = f"`{escape_md_cell(vf['audited_release'])}` still affected" if vf.get("audited_release") else "unreleased"
                ship_line_en = f"**Merged on `main` (unreleased; {aud_rel_en})**"
            proof_cell_en = f"{proof_head_en}<br>{ship_line_en}" if proof_head_en else ship_line_en
            ver_cell_en = f"Verified `{vf['verified_at']}`"
            if vf.get("verified_snapshot"):
                ver_cell_en += f"<br>[Re-Audit](./audits/{vf['forge']}/{vf['repo']}/{safe_md_url(vf['verified_snapshot'])})"
            lines_en.append(
                f"| [`{escape_md_link_text(vf['repo'])}`](./{safe_md_url(vf['project_readme'])}) | {issue_cell} | {time_cell} | {proof_cell_en} | {ver_cell_en} |"
            )
    lines_en.extend([
        "",
        "> **History Preservation Rule:** A verified fix credits responsive upstream engineering and independent oversight; it never erases or overwrites prior audit snapshots. When a re-audit updates a project's current rating, its historical peak and superseded snapshot remain permanently archived and linked alongside it. Every claimed fix commit and ancestry relationship is mechanically verified against upstream Git in CI.",
    ])
    return "\n".join(lines_en)


def render_project_readme(proj: dict[str, Any]) -> str:
    slug = proj["repo"]
    rev = f"`{escape_md_cell(proj['release'])}` (`{proj['latest_short_sha']}`)" if proj.get("release") else f"`{proj['latest_short_sha']}`"
    hist = proj["history"]
    rem_block = proj["remediation"]
    posture = rem_block["posture"]

    lines = [
        "<!-- AUTO-GENERATED by tools/build_index.py (DO NOT HAND-EDIT) -->",
        "",
        f"# {slug}",
        "",
        f"- **Upstream:** [{proj['upstream_url']}]({safe_md_url(proj['upstream_url'])})",
        f"- **Latest Audited Revision:** {rev} · **Audited:** {proj['audited_at']}",
        f"- **Current Rating:** `{proj['honesty']}` (`h3_scope={proj['h3_scope']}`) / `{proj['risk']}` · **Evidence:** `{proj['evidence_max']}` · **Verdict:** **`{proj['verdict']}`**",
    ]
    if hist["improved_from_peak"] or proj["snapshot_count"] > 1:
        peak_str = format_peak_note(hist, "en", relative_to_project=True)
        lines.append(f"- **Historical Peak:** {peak_str} · *Historical snapshots remain permanently archived.*")
    if proj.get("has_tracking"):
        rel_info = rem_block.get("latest_upstream_release")
        rel_suffix = (
            f" · **Latest Upstream Release:** `{escape_md_cell(rel_info['tag'])}` ({str(rel_info['published_at'])[:10]})"
            if rel_info
            else ""
        )
        lines.append(
            f"- **Upstream Response Posture:** **{escape_md_cell(posture['label_en'])}** ({escape_md_cell(posture['label_zh'])}) · "
            f"**Last Checked:** `{rem_block['last_checked_at']}`{rel_suffix}"
        )
    lines.extend([
        f"- **Category:** `{proj['category']}` · **Tags:** {', '.join(f'`{t}`' for t in proj['tags']) or 'None'}",
        f"- **Headline (EN):** {escape_md_cell(proj['headline_en'])}",
        f"- **核心摘要 (ZH):** {escape_md_cell(proj['headline_zh'])}",
    ])
    if proj["has_responses"]:
        lines.append("- **Official Maintainer Responses:** [responses.md](./responses.md)")
    if proj.get("has_tracking"):
        lines.append("- **Machine-Readable Tracking Ledger:** [tracking.json](./tracking.json)")

    items = rem_block["items"]
    if items:
        lines.extend([
            "",
            "## Upstream Remediation & Issue Tracking",
            "",
            "| Issue | Origin | Findings | Status | Response / Turnaround | Fix Commit & Release | Notes |",
            "|---|---|---|---|---|---|---|",
        ])
        for it in items:
            mr = it.get("maintainer_response") or {}
            rem = it.get("remediation") or {}
            fids = ", ".join(f"`{f}`" for f in (it.get("finding_ids") or [])) or "n/a"
            status_cell = f"`{it.get('status')}`"
            if it.get("status") == "partial_fix" and it.get("resolved_findings"):
                status_cell += f" (resolved: {', '.join(it['resolved_findings'])})"

            resp_parts: list[str] = []
            if mr.get("replied"):
                reply_dur = format_turnaround(it.get("filed_at"), mr.get("first_reply_at"), "en")
                actor = f"`{escape_md_cell(mr['actor'])}`" if mr.get("actor") else "maintainer"
                resp_parts.append(f"Replied by {actor}" + (f" ({reply_dur})" if reply_dur else ""))
            else:
                wd = compute_wait_days(it.get("filed_at"), rem_block.get("last_checked_at"))
                resp_parts.append(f"No maintainer reply ({wd}d as of {rem_block.get('last_checked_at')})")
            if rem.get("merged_at"):
                merge_dur = format_turnaround(it.get("filed_at"), rem.get("merged_at"), "en")
                if merge_dur:
                    resp_parts.append(f"Merged in **{merge_dur}**")
            resp_cell = "<br>".join(resp_parts)

            fix_parts: list[str] = []
            if rem.get("pr_url"):
                pr_ref = _extract_pr_ref(rem["pr_url"]) or "PR"
                tag = f" ({rem['pr_author_type']})" if rem.get("pr_author_type") else ""
                fix_parts.append(f"[PR {escape_md_cell(pr_ref)}]({safe_md_url(rem['pr_url'])}){tag}")
            if rem.get("fix_commit"):
                fix_parts.append(f"[`{rem['fix_commit'][:8]}`]({safe_md_url(proj['upstream_url'])}/commit/{rem['fix_commit']})")
            if rem.get("fix_release"):
                fix_parts.append(f"Shipped `{escape_md_cell(rem['fix_release'])}`")
            elif it.get("status") == "verified_main":
                fix_parts.append("Unreleased (`main` only)")
            elif it.get("resolution_evidence_url"):
                fix_parts.append(f"[Evidence]({safe_md_url(it['resolution_evidence_url'])})")
            fix_cell = "<br>".join(fix_parts) if fix_parts else "None yet"

            note = escape_md_cell(rem.get("verification_summary_en") or mr.get("summary_en") or it.get("title_en") or "")
            lines.append(
                f"| [{escape_md_link_text(it['ref'])}]({safe_md_url(it['url'])}) | `{it.get('origin')}` | {fids} | {status_cell} | {resp_cell} | {fix_cell} | {note} |"
            )

    pds = rem_block.get("private_disclosures") or []
    if pds:
        lines.extend([
            "",
            "## Private Coordinated Disclosures",
            "",
            "| ID | Reported | Window | Status | Summary |",
            "|---|---|---|---|---|",
        ])
        for pd in pds:
            lines.append(
                f"| `{escape_md_cell(pd['id'])}` | `{pd['reported_at']}` | {pd['window_days']}d | `{pd['status']}` | {escape_md_cell(pd['summary_en'])} |"
            )

    all_events: list[tuple[str, str, dict[str, Any]]] = []
    for it in items:
        for ev in it.get("events") or []:
            all_events.append((str(ev.get("date", "")), str(it.get("ref", "")), ev))
    if all_events:
        lines.extend(["", "## Upstream Timeline", ""])
        for date_str, ref, ev in sorted(all_events, key=lambda x: (x[0], x[1]), reverse=True):
            anchor = ""
            if ev.get("commit"):
                anchor = f" ([`{ev['commit'][:8]}`]({safe_md_url(proj['upstream_url'])}/commit/{ev['commit']}))"
            elif ev.get("url"):
                anchor = f" ([link]({safe_md_url(ev['url'])}))"
            lines.append(f"- `{date_str}` · {escape_md_cell(ref)} · `{ev['kind']}`: {escape_md_cell(ev['summary_en'])}{anchor}")

    lines.extend([
        "",
        "## Audit Snapshots (Newest First)",
        "",
        "| Date | Revision | H / R | Evidence | Verdict | Status | Report |",
        "|---|---|---|---|---|---|---|",
    ])
    for s in proj["snapshots"]:
        sha_link = f"[`{s['short_sha']}`]({safe_md_url(proj['upstream_url'])}/tree/{s['sha']})"
        rev_cell = f"`{escape_md_cell(s['release'])}` · {sha_link}" if s.get("release") else sha_link
        rep_cell = format_report_links(s["file"], s["translations"], "en", relative_to_project=True)
        lines.append(
            f"| {s['audited_at']} | {rev_cell} | `{s['honesty']} / {s['risk']}` | `{s['evidence_max']}` | "
            f"**`{s['verdict']}`** | `{s['status']}` | {rep_cell} |"
        )
    return "\n".join(lines) + "\n"


def render_root_table(catalog: dict[str, Any], lang: str) -> str:
    projects = catalog["projects"]
    if not projects:
        if lang == "zh":
            return "_暂未上线正式公开报告。每个项目在完成全量源码核查、沙箱复现与负责任披露分诊后，逐一同步上线。_"
        return "_No public audits published yet. Audits are added after source verification and coordinated disclosure review._"

    if lang == "zh":
        lines = [
            f"当前追踪 **{catalog['project_count']}** 个开源项目（共 **{catalog['snapshot_count']}** 份固定 SHA 快照）：",
            "",
            "| 项目 | 最新版本 | 当前评级 vs 历史峰值 | 核心发现 | 上游响应与 Issue | 报告 |",
            "|---|---|---|---|---|---|",
        ]
        for p in projects:
            rev = f"`{escape_md_cell(p['release'])}`<br>`{p['latest_short_sha']}`" if p.get("release") else f"`{p['latest_short_sha']}`"
            rating = f"`{p['honesty']} / {p['risk']}` · `{p['evidence_max']}`<br>**`{p['verdict']}`**"
            hist = p["history"]
            if hist["improved_from_peak"]:
                rating += f"<br>{format_peak_note(hist, 'zh')}"
            issues = format_upstream_response_cell(p, "zh")
            rep = format_report_links(
                p["latest_report"],
                p["latest_translations"],
                "zh",
                snapshot_count=p["snapshot_count"],
                project_readme=p["project_readme"],
            )
            lines.append(
                f"| [`{escape_md_cell(p['repo'])}`](./{safe_md_url(p['project_readme'])}) | {rev} | {rating} | {escape_md_cell(p['headline_zh'])} | {issues} | {rep} |"
            )
        return "\n".join(lines)

    lines = [
        f"Currently tracking **{catalog['project_count']}** project(s) across **{catalog['snapshot_count']}** SHA-pinned snapshot(s):",
        "",
        "| Project | Latest Rev | Current vs. Peak | Key Finding | Upstream Response & Issues | Report |",
        "|---|---|---|---|---|---|",
    ]
    for p in projects:
        rev = f"`{escape_md_cell(p['release'])}`<br>`{p['latest_short_sha']}`" if p.get("release") else f"`{p['latest_short_sha']}`"
        rating = f"`{p['honesty']} / {p['risk']}` · `{p['evidence_max']}`<br>**`{p['verdict']}`**"
        hist = p["history"]
        if hist["improved_from_peak"]:
            rating += f"<br>{format_peak_note(hist, 'en')}"
        issues = format_upstream_response_cell(p, "en")
        rep = format_report_links(
            p["latest_report"],
            p["latest_translations"],
            "en",
            snapshot_count=p["snapshot_count"],
            project_readme=p["project_readme"],
        )
        lines.append(
            f"| [`{escape_md_cell(p['repo'])}`](./{safe_md_url(p['project_readme'])}) | {rev} | {rating} | {escape_md_cell(p['headline_en'])} | {issues} | {rep} |"
        )
    return "\n".join(lines)


def _find_single_marker_span(text: str, begin_marker: str, end_marker: str) -> tuple[int, int]:
    begin_count = text.count(begin_marker)
    end_count = text.count(end_marker)
    if begin_count != 1 or end_count != 1:
        raise ValueError(f"Expected exactly 1 pair of {begin_marker} ... {end_marker} (found {begin_count} begin, {end_count} end)")
    b_idx = text.index(begin_marker)
    e_idx = text.index(end_marker)
    if e_idx < b_idx:
        raise ValueError(f"Malformed marker order for {begin_marker} ... {end_marker}")
    return b_idx, e_idx + len(end_marker)


def replace_marked_block(text: str, begin_marker: str, end_marker: str, block: str, required: bool = True) -> str:
    begin_count = text.count(begin_marker)
    end_count = text.count(end_marker)
    if begin_count == 0 and end_count == 0:
        if required:
            raise ValueError(f"Missing markers {begin_marker} ... {end_marker}")
        return text
    _find_single_marker_span(text, begin_marker, end_marker)
    pattern = re.compile(
        re.escape(begin_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )
    replacement = f"{begin_marker}\n{block}\n{end_marker}"
    return pattern.sub(lambda _m: replacement, text, count=1)


def render_root_readme(path: Path, catalog: dict[str, Any], lang: str) -> str:
    text = path.read_text(encoding="utf-8")
    imp_s, imp_e = _find_single_marker_span(text, IMPACT_BEGIN, IMPACT_END)
    cat_s, cat_e = _find_single_marker_span(text, CATALOG_BEGIN, CATALOG_END)
    if not (imp_e <= cat_s or cat_e <= imp_s):
        raise ValueError(f"Overlapping/nested IMPACT and CATALOG markers in {path}")
    text = replace_marked_block(text, IMPACT_BEGIN, IMPACT_END, render_impact_block(catalog, lang), required=True)
    text = replace_marked_block(text, CATALOG_BEGIN, CATALOG_END, render_root_table(catalog, lang), required=True)
    return text


def sync_indexes(root_dir: Path = ROOT, check_only: bool = False) -> tuple[dict[str, Any], list[str]]:
    audits_dir = root_dir / "audits"
    catalog_path = root_dir / "data" / "catalog.json"
    readme_en = root_dir / "README.md"
    readme_zh = root_dir / "README.zh-CN.md"

    projects = discover_snapshots(audits_dir=audits_dir, root_dir=root_dir)
    catalog = build_catalog_data(projects, audits_dir=audits_dir)

    desired: dict[Path, str] = {
        catalog_path: json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        readme_en: render_root_readme(readme_en, catalog, "en"),
        readme_zh: render_root_readme(readme_zh, catalog, "zh"),
    }
    for p in catalog["projects"]:
        desired[root_dir / p["project_readme"]] = render_project_readme(p)

    drifted: list[str] = []
    for path, content in desired.items():
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        if existing != content:
            drifted.append(path.relative_to(root_dir).as_posix())
            if not check_only:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    return catalog, drifted


def main() -> int:
    ap = argparse.ArgumentParser(description="Build or check generated indexes for trust-no-readme.")
    ap.add_argument("--check", action="store_true", help="Exit non-zero if any generated file is out of date")
    args = ap.parse_args()

    catalog, drifted = sync_indexes(root_dir=ROOT, check_only=args.check)
    if args.check:
        if drifted:
            print(f"ERROR: generated index drift in: {', '.join(drifted)}", file=sys.stderr)
            print("Run `python3 tools/build_index.py` to regenerate.", file=sys.stderr)
            return 1
        print(
            f"OK build_index --check: {catalog['project_count']} projects, {catalog['snapshot_count']} snapshots, "
            f"{catalog['impact_summary']['verified_fixes_total']} verified fix(es) in sync."
        )
        return 0

    print(
        f"OK build_index: {catalog['project_count']} projects, {catalog['snapshot_count']} snapshots, "
        f"{catalog['impact_summary']['verified_fixes_total']} verified fix(es) (updated {len(drifted)} file(s))."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
