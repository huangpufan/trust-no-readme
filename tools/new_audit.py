#!/usr/bin/env python3
"""Scaffold a new audit snapshot (or post-fix re-audit) from templates/.

Stdlib-only Python. Designed for both humans and AI agents.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from build_index import ROOT, forge_base_url

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_categories(root_dir: Path = ROOT) -> list[str]:
    schema = json.loads((root_dir / "data" / "schema" / "audit-frontmatter.schema.json").read_text(encoding="utf-8"))
    return list(schema["properties"]["category"]["enum"])


def scaffold_snapshot(
    *,
    repo: str,
    sha: str,
    forge: str = "github",
    release: str = "HEAD",
    category: str = "other",
    audited_at: str | None = None,
    reaudit: bool = False,
    previous_sha: str | None = None,
    force: bool = False,
    root_dir: Path = ROOT,
) -> Path:
    if not REPO_RE.fullmatch(repo):
        raise ValueError(f"Invalid --repo `{repo}` (expected owner/name)")
    if not SHA_RE.fullmatch(sha):
        raise ValueError(f"Invalid --sha `{sha}` (expected 40 lowercase hex chars)")
    if forge not in {"github", "gitlab", "codeberg"}:
        raise ValueError(f"Unsupported --forge `{forge}`")
    cats = valid_categories(root_dir)
    if category not in cats:
        raise ValueError(f"Invalid --category `{category}` (choose from: {', '.join(cats)})")
    date_str = audited_at or dt.date.today().isoformat()
    if not DATE_RE.fullmatch(date_str):
        raise ValueError(f"Invalid --date `{date_str}` (expected YYYY-MM-DD)")
    if previous_sha and not SHA_RE.fullmatch(previous_sha):
        raise ValueError(f"Invalid --previous-sha `{previous_sha}` (expected 40 lowercase hex chars)")

    short_sha = sha[:8]
    owner, repo_name = repo.split("/", 1)
    out_dir = root_dir / "audits" / forge / owner / repo_name
    out_path = out_dir / f"{date_str}-{short_sha}.md"
    if out_path.exists() and not force:
        raise FileExistsError(f"Snapshot already exists: {out_path} (use --force to overwrite)")

    tpl_name = "reaudit-snapshot.md" if reaudit else "audit-snapshot.md"
    tpl_text = (root_dir / "templates" / tpl_name).read_text(encoding="utf-8")

    replacements = {
        "<owner/repo>": repo,
        "forge: github": f"forge: {forge}",
        "https://github.com": forge_base_url(forge),
        "<40-char-sha>": sha,
        "<new-40-char-sha>": sha,
        "<8char-sha>": short_sha,
        "<new-8char-sha>": short_sha,
        "<version-or-tag>": release,
        "<new-version-or-tag>": release,
        "<YYYY-MM-DD>": date_str,
        "<category>": category,
    }
    if previous_sha:
        replacements["<previous-40-char-sha>"] = previous_sha
        replacements["<prev-8char-sha>"] = previous_sha[:8]

    rendered = tpl_text
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a new trust-no-readme audit snapshot.")
    ap.add_argument("--repo", required=True, help="Target repository slug (owner/name)")
    ap.add_argument("--sha", required=True, help="Exact 40-character lowercase Git commit SHA")
    ap.add_argument("--forge", default="github", choices=["github", "gitlab", "codeberg"])
    ap.add_argument("--release", default="HEAD", help="Shipped release version or tag (e.g. v1.2.3)")
    ap.add_argument("--category", default="other", help="Project category enum from schema")
    ap.add_argument("--date", default=None, help="Audit date YYYY-MM-DD (defaults to today)")
    ap.add_argument("--reaudit", action="store_true", help="Use templates/reaudit-snapshot.md")
    ap.add_argument("--previous-sha", default=None, help="Previous 40-char SHA being superseded (for --reaudit)")
    ap.add_argument("--force", action="store_true", help="Overwrite if target snapshot file already exists")
    args = ap.parse_args()

    try:
        out_path = scaffold_snapshot(
            repo=args.repo,
            sha=args.sha,
            forge=args.forge,
            release=args.release,
            category=args.category,
            audited_at=args.date,
            reaudit=args.reaudit,
            previous_sha=args.previous_sha,
            force=args.force,
        )
    except (ValueError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rel = out_path.relative_to(ROOT).as_posix()
    print(f"Created {rel}")
    print("Next steps:")
    print(f"  1. Fill all sections & remove template placeholders in {rel}")
    print("  2. python3 tools/build_index.py")
    print(f"  3. python3 tools/validate.py --verify-upstream --format json {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
