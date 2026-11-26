#!/usr/bin/env python3
"""Generate data/catalog.json, per-project READMEs, and root README catalog tables.

Single source of truth: YAML frontmatter inside canonical English snapshots
`audits/<forge>/<owner>/<repo>/YYYY-MM-DD-<shortsha>.md` (optional sibling
`YYYY-MM-DD-<shortsha>.zh-CN.md` translations are automatically linked).
Stdlib-only Python (zero external dependencies). Run with `--check` in CI.
"""

from __future__ import annotations

import argparse
import collections
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

CATALOG_BEGIN = "<!-- CATALOG:BEGIN -->"
CATALOG_END = "<!-- CATALOG:END -->"
SNAPSHOT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([0-9a-f]{7,12})\.md$")
TRANSLATION_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([0-9a-f]{7,12})\.(zh-CN)\.md$")
LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
ISSUE_NUM_RE = re.compile(r"/(?:issues|pull|merge_requests)/(\d+)(?:[/?#]|$)")


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

    for (forge, slug), snaps in sorted(projects.items(), key=lambda kv: kv[0][1].lower()):
        latest = snaps[0]
        lfm = latest["frontmatter"]
        tags = [str(t) for t in lfm.get("tags", [])]
        for t in tags:
            by_tag[t].append(slug)

        proj_dir = audits_dir / forge / slug
        has_responses = (proj_dir / "responses.md").is_file()

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

        project_entries.append(
            {
                "forge": forge,
                "repo": slug,
                "upstream_url": f"{forge_base_url(forge)}/{slug}",
                "project_readme": f"audits/{forge}/{slug}/README.md",
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
                "companion_repos": parse_companion_repos(lfm.get("companion_repos")),
                "upstream_issues": parse_upstream_issues(lfm.get("upstream_issues")),
                "tags": tags,
                "has_responses": has_responses,
                "snapshot_count": len(snap_records),
                "snapshots": snap_records,
            }
        )

    project_entries.sort(key=lambda p: (str(p.get("audited_at", "")), str(p.get("repo", "")).lower()), reverse=True)
    return {
        "schema_version": 2,
        "project_count": len(project_entries),
        "snapshot_count": sum(p["snapshot_count"] for p in project_entries),
        "by_tag": {k: sorted(v) for k, v in sorted(by_tag.items())},
        "projects": project_entries,
    }


def format_upstream_issues_cell(issues: list[dict[str, str]], lang: str) -> str:
    if not issues:
        return "待提交" if lang == "zh" else "Pending"
    parts = [f"[{i['ref']}]({i['url']}) ({i['state']})" for i in issues]
    return "<br>".join(parts)


def format_report_links(report_rel: str, translations: dict[str, str], lang: str, relative_to_project: bool = False) -> str:
    en_href = f"./{Path(report_rel).name}" if relative_to_project else f"./{report_rel}"
    en_label = "EN" if translations else ("EN 报告" if lang == "zh" else "Report")
    links = [f"[{en_label}]({en_href})"]
    if "zh-CN" in translations:
        zh_href = f"./{Path(translations['zh-CN']).name}" if relative_to_project else f"./{translations['zh-CN']}"
        links.append(f"[中文]({zh_href})")
    return " · ".join(links)


def render_project_readme(proj: dict[str, Any]) -> str:
    slug = proj["repo"]
    rev = f"`{proj['release']}` (`{proj['latest_short_sha']}`)" if proj.get("release") else f"`{proj['latest_short_sha']}`"
    lines = [
        "<!-- AUTO-GENERATED by tools/build_index.py (DO NOT HAND-EDIT) -->",
        "",
        f"# {slug}",
        "",
        f"- **Upstream:** [{proj['upstream_url']}]({proj['upstream_url']})",
        f"- **Latest Revision:** {rev} · **Audited:** {proj['audited_at']}",
        f"- **Rating:** `{proj['honesty']}` (`h3_scope={proj['h3_scope']}`) / `{proj['risk']}` · **Evidence:** `{proj['evidence_max']}` · **Verdict:** **`{proj['verdict']}`**",
        f"- **Category:** `{proj['category']}` · **Tags:** {', '.join(f'`{t}`' for t in proj['tags']) or 'None'}",
        f"- **Headline (EN):** {proj['headline_en']}",
        f"- **核心摘要 (ZH):** {proj['headline_zh']}",
    ]
    if proj["has_responses"]:
        lines.append("- **Official Maintainer Responses:** [responses.md](./responses.md)")

    if proj["upstream_issues"]:
        lines.extend(["", "## Upstream Issues Tracked", ""])
        for iss in proj["upstream_issues"]:
            lines.append(f"- [{iss['ref']}]({iss['url']}) (`{iss['state']}`): {iss['label']}")

    lines.extend([
        "",
        "## Audit Snapshots (Newest First)",
        "",
        "| Date | Revision | H / R | Evidence | Verdict | Status | Report |",
        "|---|---|---|---|---|---|---|",
    ])
    for s in proj["snapshots"]:
        sha_link = f"[`{s['short_sha']}`]({proj['upstream_url']}/tree/{s['sha']})"
        rev_cell = f"`{s['release']}` · {sha_link}" if s.get("release") else sha_link
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
            "| 项目 | 版本 / SHA | 评级与结论 | 核心发现 | 上游 Issue | 报告 |",
            "|---|---|---|---|---|---|",
        ]
        for p in projects:
            rev = f"`{p['release']}`<br>`{p['latest_short_sha']}`" if p.get("release") else f"`{p['latest_short_sha']}`"
            rating = f"`{p['honesty']} / {p['risk']}` · `{p['evidence_max']}`<br>**`{p['verdict']}`**"
            issues = format_upstream_issues_cell(p["upstream_issues"], "zh")
            rep = format_report_links(p["latest_report"], p["latest_translations"], "zh")
            lines.append(
                f"| [`{p['repo']}`](./{p['project_readme']}) | {rev} | {rating} | {p['headline_zh']} | {issues} | {rep} |"
            )
        return "\n".join(lines)

    lines = [
        f"Currently tracking **{catalog['project_count']}** project(s) across **{catalog['snapshot_count']}** SHA-pinned snapshot(s):",
        "",
        "| Project | Revision | Rating & Verdict | Key Finding | Upstream Issues | Report |",
        "|---|---|---|---|---|---|",
    ]
    for p in projects:
        rev = f"`{p['release']}`<br>`{p['latest_short_sha']}`" if p.get("release") else f"`{p['latest_short_sha']}`"
        rating = f"`{p['honesty']} / {p['risk']}` · `{p['evidence_max']}`<br>**`{p['verdict']}`**"
        issues = format_upstream_issues_cell(p["upstream_issues"], "en")
        rep = format_report_links(p["latest_report"], p["latest_translations"], "en")
        lines.append(
            f"| [`{p['repo']}`](./{p['project_readme']}) | {rev} | {rating} | {p['headline_en']} | {issues} | {rep} |"
        )
    return "\n".join(lines)


def replace_catalog_block(path: Path, block: str) -> str:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(CATALOG_BEGIN) + r".*?" + re.escape(CATALOG_END),
        re.DOTALL,
    )
    if not pattern.search(text):
        raise ValueError(f"Missing catalog markers in {path}")
    return pattern.sub(f"{CATALOG_BEGIN}\n{block}\n{CATALOG_END}", text, count=1)


def sync_indexes(root_dir: Path = ROOT, check_only: bool = False) -> tuple[dict[str, Any], list[str]]:
    audits_dir = root_dir / "audits"
    catalog_path = root_dir / "data" / "catalog.json"
    readme_en = root_dir / "README.md"
    readme_zh = root_dir / "README.zh-CN.md"

    projects = discover_snapshots(audits_dir=audits_dir, root_dir=root_dir)
    catalog = build_catalog_data(projects, audits_dir=audits_dir)

    desired: dict[Path, str] = {
        catalog_path: json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        readme_en: replace_catalog_block(readme_en, render_root_table(catalog, "en")),
        readme_zh: replace_catalog_block(readme_zh, render_root_table(catalog, "zh")),
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
        print(f"OK build_index --check: {catalog['project_count']} projects, {catalog['snapshot_count']} snapshots in sync.")
        return 0

    print(
        f"OK build_index: {catalog['project_count']} projects, {catalog['snapshot_count']} snapshots "
        f"(updated {len(drifted)} file(s))."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
