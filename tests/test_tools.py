"""Unit tests for trust-no-readme tooling (build_index, validate, new_audit, sync_issues)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_index  # noqa: E402
import new_audit  # noqa: E402
import sync_issues  # noqa: E402
import validate  # noqa: E402


def _init_git_repo(repo_dir: Path, files: dict[str, str]) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = repo_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    env_flags = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", *env_flags, "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], text=True).strip()


def _commit_more(repo_dir: Path, files: dict[str, str], msg: str) -> str:
    for rel, content in files.items():
        p = repo_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    env_flags = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", *env_flags, "-C", str(repo_dir), "commit", "-q", "-m", msg], check=True)
    return subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], text=True).strip()


def _make_mini_workspace(tmp: Path) -> Path:
    ws = tmp / "ws"
    (ws / "data" / "schema").mkdir(parents=True)
    shutil.copy2(ROOT / "data" / "schema" / "audit-frontmatter.schema.json", ws / "data" / "schema" / "audit-frontmatter.schema.json")
    shutil.copy2(ROOT / "data" / "schema" / "tracking.schema.json", ws / "data" / "schema" / "tracking.schema.json")
    shutil.copytree(ROOT / "templates", ws / "templates")
    (ws / "README.md").write_text(
        "# test\n<!-- IMPACT:BEGIN -->\n<!-- IMPACT:END -->\n<!-- CATALOG:BEGIN -->\n<!-- CATALOG:END -->\n",
        encoding="utf-8",
    )
    (ws / "README.zh-CN.md").write_text(
        "# 测试\n<!-- IMPACT:BEGIN -->\n<!-- IMPACT:END -->\n<!-- CATALOG:BEGIN -->\n<!-- CATALOG:END -->\n",
        encoding="utf-8",
    )
    return ws


def _valid_snapshot(
    sha: str,
    comp_sha: str | None = None,
    extra_body: str = "",
    *,
    audited_at: str = "2026-11-26",
    release: str = "v1.0.0",
    honesty: str = "H3",
    h3_scope: str = "primary",
    risk: str = "R2",
    verdict: str = "Isolate",
    supersedes_sha: str | None = None,
    upstream_issues: list[str] | None = None,
    headline_en: str | None = None,
    headline_zh: str | None = None,
) -> str:
    comp_line = f'companion_repos:\n  - "dep=github:acme/dep@{comp_sha}"\n' if comp_sha else "companion_repos: []\n"
    comp_ref = "\n- Companion check: `dep:lib/helper.py:2-3` calls `send_raw()`.\n" if comp_sha else ""
    sup_line = f"supersedes_sha: {supersedes_sha}\n" if supersedes_sha else ""
    issues_list = upstream_issues if upstream_issues is not None else [
        "open|https://github.com/acme/demo/issues/42|F1: send_metrics ignores opt-out"
    ]
    issues_block = "upstream_issues:\n" + "".join(f'  - "{x}"\n' for x in issues_list) if issues_list else "upstream_issues: []\n"
    h_en = headline_en or f"Disabling telemetry still calls send_metrics() on shutdown in demo {release}."
    h_zh = headline_zh or f"关闭遥测开关后，demo {release} 退出时仍调用 send_metrics()。"
    return f"""---
repo: acme/demo
forge: github
sha: {sha}
release: "{release}"
audited_at: {audited_at}
category: dev-tool
honesty: {honesty}
h3_scope: {h3_scope}
risk: {risk}
verdict: {verdict}
evidence_max: E3+D
coverage: full-exhaustive
verification: static+dynamic
git_depth: full
disclosure: public
status: active
language: en
{sup_line}headline_en: "{h_en}"
headline_zh: "{h_zh}"
{comp_line}{issues_block}tags:
  - placebo-opt-out
---

# acme/demo: Code vs. Claims Audit

## Executive Verdict

| ID | Finding | H / R | Evidence | Default? | Upstream Issue |
|---|---|---|---|---|---|
| F1 | [Opt-out ignored](#f1-opt-out-ignored) | `{honesty} / {risk}` | `E3+D` | Yes | [#42](https://github.com/acme/demo/issues/42) |

## 1. Snapshot & Claim Ledger

| ID | Source | Promise | Reality |
|---|---|---|---|
| C1 | [`README.md:1`](https://github.com/acme/demo/blob/{sha}/README.md#L1) | "Zero telemetry when disabled" | Contradicted |

## 2. Detailed Findings

### F1: Opt-out ignored

- [`app/core.py:2-4`](https://github.com/acme/demo/blob/{sha}/app/core.py#L2-L4) calls `send_metrics()` and omits `opt_out_flag`.{comp_ref}

**Relevant source ([`app/core.py:2-4`](https://github.com/acme/demo/blob/{sha}/app/core.py#L2-L4)):**

```python
def shutdown():
    send_metrics("ping")
```
{extra_body}
## 3. Trajectory
- Initial release.

## 4. Cleared Surfaces
- Clean license.

## 5. Coverage, Limits & Gaps
- Full static review.
"""


class TestTrustNoReadmeTools(unittest.TestCase):
    def test_valid_snapshot_with_companion_and_translation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws = _make_mini_workspace(tmp)
            main_sha = _init_git_repo(
                tmp / "main_repo",
                {
                    "README.md": "Zero telemetry when disabled.\n",
                    "app/core.py": '# core\ndef shutdown():\n    send_metrics("ping")\n    return True\n',
                },
            )
            comp_sha = _init_git_repo(
                tmp / "comp_repo",
                {"lib/helper.py": "# helper\ndef send_raw():\n    return 1\n"},
            )
            snap_dir = ws / "audits" / "github" / "acme" / "demo"
            snap_dir.mkdir(parents=True)
            en_path = snap_dir / f"2026-11-26-{main_sha[:8]}.md"
            en_path.write_text(_valid_snapshot(main_sha, comp_sha), encoding="utf-8")

            zh_text = _valid_snapshot(main_sha, comp_sha).replace(
                "language: en\n", f"language: zh-CN\ntranslation_of: {en_path.name}\n"
            )
            zh_path = snap_dir / f"2026-11-26-{main_sha[:8]}.zh-CN.md"
            zh_path.write_text(zh_text, encoding="utf-8")

            errs, snaps, trans, cits, t_checked, fix_verified = validate.validate_repo(
                [en_path, zh_path],
                root_dir=ws,
                local_map={"acme/demo": tmp / "main_repo", "dep": tmp / "comp_repo"},
            )
            self.assertEqual(errs, [], [e.format_text() for e in errs])
            self.assertEqual(snaps, 1)
            self.assertEqual(trans, 1)
            self.assertEqual(t_checked, 0)
            self.assertEqual(fix_verified, 0)
            self.assertGreaterEqual(cits, 4)

            catalog, drifted = build_index.sync_indexes(root_dir=ws, check_only=False)
            self.assertEqual(catalog["schema_version"], 3)
            self.assertEqual(catalog["project_count"], 1)
            self.assertEqual(catalog["snapshot_count"], 1)
            self.assertIn("zh-CN", catalog["projects"][0]["latest_translations"])
            self.assertIn("[#42](https://github.com/acme/demo/issues/42) (open)", (ws / "README.md").read_text(encoding="utf-8"))
            self.assertIn("[中文]", (ws / "README.md").read_text(encoding="utf-8"))
            self.assertTrue(drifted)
            _, drifted2 = build_index.sync_indexes(root_dir=ws, check_only=True)
            self.assertEqual(drifted2, [])

    def test_catches_excerpt_mismatch_hallucinated_symbol_and_banned_words(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws = _make_mini_workspace(tmp)
            main_sha = _init_git_repo(
                tmp / "main_repo",
                {
                    "README.md": "Zero telemetry when disabled.\n",
                    "app/core.py": '# core\ndef shutdown():\n    send_metrics("ping")\n    return True\n',
                },
            )
            bad_body = (
                "\n- This spyware 恶意后门 calls `totally_fake_func()` at `app/core.py:2-3`.\n"
                "- Bad permalink: [core](https://github.com/acme/demo/blob/main/app/core.py#L2)\n"
                "- Local path: /home/alice/secret/file.txt\n"
                "- Placeholder: TODO: fill later\n"
            )
            text = _valid_snapshot(main_sha, extra_body=bad_body).replace(
                '    send_metrics("ping")', '    send_fabricated("evil")'
            )
            snap_dir = ws / "audits" / "github" / "acme" / "demo"
            snap_dir.mkdir(parents=True)
            en_path = snap_dir / f"2026-11-26-{main_sha[:8]}.md"
            en_path.write_text(text, encoding="utf-8")

            errs, _, _, _, _, _ = validate.validate_repo(
                [en_path], root_dir=ws, local_map={"acme/demo": tmp / "main_repo"}
            )
            rules = {e.rule for e in errs}
            self.assertIn("banned-intent-word", rules)
            self.assertIn("permalink-sha-mismatch", rules)
            self.assertIn("path-leak", rules)
            self.assertIn("unfilled-placeholder", rules)
            self.assertIn("excerpt-mismatch", rules)
            self.assertIn("upstream-symbol-mismatch", rules)

    def test_tracking_verified_fix_peak_history_and_impact_showcase(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws = _make_mini_workspace(tmp)
            repo_dir = tmp / "main_repo"
            sha1 = _init_git_repo(
                repo_dir,
                {
                    "README.md": "Zero telemetry when disabled.\n",
                    "app/core.py": '# core\ndef shutdown():\n    send_metrics("ping")\n    return True\n',
                },
            )
            sha2 = _commit_more(
                repo_dir,
                {
                    "app/core.py": '# core\ndef shutdown(opt_out=True):\n    if not opt_out:\n        send_metrics("ping")\n    return True\n',
                },
                "fix: honor opt-out on shutdown",
            )
            subprocess.run(["git", "-C", str(repo_dir), "tag", "v1.0.1", sha2], check=True)
            snap_dir = ws / "audits" / "github" / "acme" / "demo"
            snap_dir.mkdir(parents=True)
            snap1_name = f"2026-11-26-{sha1[:8]}.md"
            snap2_name = f"2026-11-28-{sha2[:8]}.md"
            (snap_dir / snap1_name).write_text(
                _valid_snapshot(sha1, audited_at="2026-11-26", release="v1.0.0", honesty="H4", risk="R3", verdict="Isolate"),
                encoding="utf-8",
            )
            (snap_dir / snap2_name).write_text(
                _valid_snapshot(
                    sha2,
                    audited_at="2026-11-28",
                    release="v1.0.1",
                    honesty="H0",
                    h3_scope="none",
                    risk="R1",
                    verdict="Adopt",
                    supersedes_sha=sha1,
                ).replace(
                    'def shutdown():\n    send_metrics("ping")',
                    'def shutdown(opt_out=True):\n    if not opt_out:\n        send_metrics("ping")',
                ),
                encoding="utf-8",
            )

            tracking = {
                "schema_version": 1,
                "repo": "acme/demo",
                "forge": "github",
                "official_bots": ["acme-bot[bot]"],
                "last_checked_at": "2026-11-28",
                "latest_upstream_release": {"tag": "v1.0.1", "published_at": "2026-11-28T10:00:00Z"},
                "items": [
                    {
                        "id": "demo-42",
                        "origin": "audit_filed",
                        "snapshot": snap1_name,
                        "finding_ids": ["F1"],
                        "resolved_findings": ["F1"],
                        "url": "https://github.com/acme/demo/issues/42",
                        "ref": "#42",
                        "title_en": "Shutdown sends telemetry after opt-out",
                        "title_zh": "关闭开关后退出仍发送遥测",
                        "latent_days": 120,
                        "filed_at": "2026-11-26T08:00:00Z",
                        "status": "verified_shipped",
                        "maintainer_response": {
                            "replied": True,
                            "first_reply_at": "2026-11-26T10:00:00Z",
                            "first_decision_at": "2026-11-26T10:30:00Z",
                            "actor": "maintainer1",
                            "summary_en": "Confirmed opt-out bug and shipped fix in v1.0.1.",
                            "summary_zh": "确认缺陷并在 v1.0.1 发布修复。",
                        },
                        "remediation": {
                            "pr_url": "https://github.com/acme/demo/pull/43",
                            "pr_author_type": "maintainer",
                            "fix_commit": sha2,
                            "merged_at": "2026-11-26T16:00:00Z",
                            "fix_release": "v1.0.1",
                            "verified_at": "2026-11-28",
                            "verified_snapshot": snap2_name,
                            "verification_summary_en": "Verified opt_out guard blocks shutdown metrics in v1.0.1.",
                            "verification_summary_zh": "已核验 v1.0.1 中 opt_out 拦截退出上报。",
                        },
                        "resolution_evidence_url": None,
                        "observed": {"github_state": "closed", "state_reason": "completed", "labels": ["bug"], "closed_at": "2026-11-26T16:00:00Z", "linked_prs": []},
                        "events": [
                            {"date": "2026-11-26", "kind": "filed", "url": "https://github.com/acme/demo/issues/42", "summary_en": "Filed issue #42.", "summary_zh": "提交 #42。"},
                            {"date": "2026-11-28", "kind": "verified_shipped", "commit": sha2, "summary_en": "Verified in v1.0.1.", "summary_zh": "已在 v1.0.1 验真。"},
                        ],
                    }
                ],
                "private_disclosures": [
                    {
                        "id": "cvd-1",
                        "snapshot": snap1_name,
                        "reported_at": "2026-11-26",
                        "window_days": 90,
                        "status": "embargoed",
                        "advisory_url": None,
                        "fix_commit": None,
                        "fix_release": None,
                        "disclosed_at": None,
                        "summary_en": "1 withheld security finding under 90-day coordinated private disclosure (details omitted).",
                        "summary_zh": "1 项处于 90 天负责任私密披露期的暂隐安全问题（细节暂隐）。",
                    }
                ],
            }
            tpath = snap_dir / "tracking.json"
            tpath.write_text(json.dumps(tracking, indent=2), encoding="utf-8")

            errs, _, _, _, t_checked, fix_verified = validate.validate_repo(
                [snap_dir / snap1_name, snap_dir / snap2_name],
                root_dir=ws,
                local_map={"acme/demo": repo_dir},
                tracking_targets=[tpath],
            )
            self.assertEqual(errs, [], [e.format_text() for e in errs])
            self.assertEqual(t_checked, 1)
            self.assertEqual(fix_verified, 1)

            catalog, _ = build_index.sync_indexes(root_dir=ws, check_only=False)
            self.assertEqual(catalog["impact_summary"]["verified_fixes_total"], 1)
            self.assertEqual(catalog["impact_summary"]["verified_fixes_shipped"], 1)
            self.assertEqual(catalog["impact_summary"]["private_cvd_embargoed"], 1)
            proj = catalog["projects"][0]
            self.assertTrue(proj["history"]["improved_from_peak"])
            self.assertEqual(proj["history"]["peak_honesty"], "H4")
            self.assertEqual(proj["remediation"]["posture"]["code"], "remediated_shipped")

            readme_en = (ws / "README.md").read_text(encoding="utf-8")
            readme_zh = (ws / "README.zh-CN.md").read_text(encoding="utf-8")
            proj_md = (snap_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("Verified Fixes Driven by Independent Audits", readme_en)
            self.assertIn("`120d` latent -> **8h** to merge", readme_en)
            self.assertIn("_(peak `H4/R3` @ v1.0.0)_", readme_en)
            self.assertIn("已验证修复成果", readme_zh)
            self.assertIn("_(历史峰值 `H4/R3` @ v1.0.0)_", readme_zh)
            self.assertIn("**Historical Peak:** `H4 / R3`", proj_md)
            self.assertIn("Remediated (Shipped)", proj_md)

            _, drifted_again = build_index.sync_indexes(root_dir=ws, check_only=True)
            self.assertEqual(drifted_again, [])

    def test_pr_scope_blocks_delete_rename_and_mixed_snapshot_edits(self) -> None:
        # Deletion, rename out of audits/, and edit under --allow-mixed-pr must ALL fail!
        with patch.object(validate.subprocess, "run") as mrun:
            mrun.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="D\0audits/github/a/b/2026-11-26-12345678.md\0",
                stderr="",
            )
            errs = validate.check_pr_scope("origin/main", allow_mixed=True, allow_correction=True)
            self.assertIn("pr-scope-immutable-snapshot", {e.rule for e in errs})

            # Rename encoded with R100
            mrun.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="R100\0audits/github/a/b/2026-11-26-12345678.md\0moved/snap.md\0",
                stderr="",
            )
            errs = validate.check_pr_scope("origin/main", allow_mixed=True, allow_correction=False)
            self.assertIn("pr-scope-immutable-snapshot", {e.rule for e in errs})

            # Edit M under allow_mixed=True (tooling label) still blocked; allowed only with allow_correction=True
            mrun.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="M\0audits/github/a/b/2026-11-26-12345678.md\0M\0tools/validate.py\0",
                stderr="",
            )
            errs_tooling = validate.check_pr_scope("origin/main", allow_mixed=True, allow_correction=False)
            self.assertEqual({e.rule for e in errs_tooling}, {"pr-scope-immutable-snapshot"})

            errs_corr = validate.check_pr_scope("origin/main", allow_mixed=False, allow_correction=True)
            self.assertEqual({e.rule for e in errs_corr}, {"pr-scope-protected"})

            # Updating tracking.json without touching snapshots or tools passes cleanly
            mrun.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="M\0audits/github/a/b/tracking.json\0M\0data/catalog.json\0",
                stderr="",
            )
            self.assertEqual(validate.check_pr_scope("origin/main"), [])

    def test_tracking_blocks_extra_props_dupes_origin_relabel_and_fake_proofs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws = _make_mini_workspace(tmp)
            repo_dir = tmp / "main_repo"
            sha0 = _init_git_repo(repo_dir, {"README.md": "hi\n", "app/core.py": "x = 0\n"})
            sha1 = _commit_more(repo_dir, {"app/core.py": "x = 1\n"}, "audited")
            # Create an unrelated branch commit not on HEAD
            subprocess.run(["git", "-C", str(repo_dir), "checkout", "-q", "-b", "unmerged"], check=True)
            sha_branch = _commit_more(repo_dir, {"app/core.py": "x = 2\n"}, "unmerged branch")
            subprocess.run(["git", "-C", str(repo_dir), "checkout", "-q", "-"], check=True)

            snap_dir = ws / "audits" / "github" / "acme" / "demo"
            snap_dir.mkdir(parents=True)
            snap1_name = f"2026-11-26-{sha1[:8]}.md"
            (snap_dir / snap1_name).write_text(
                _valid_snapshot(
                    sha1,
                    audited_at="2026-11-26",
                    release="v1.0.0",
                    upstream_issues=[
                        "open|https://github.com/acme/demo/issues/42|F1: open",
                        "existing|https://github.com/acme/demo/issues/99|F1: existing issue",
                    ],
                ),
                encoding="utf-8",
            )

            bad_tracking = {
                "schema_version": 1,
                "repo": "acme/demo",
                "forge": "github",
                "last_checked_at": "2026-11-27",
                "items": [
                    {
                        # Reuses affected snapshot + whitespace summaries + predating sha0 + wrong release
                        "id": "demo-42",
                        "origin": "audit_filed",
                        "snapshot": snap1_name,
                        "finding_ids": ["F1"],
                        "resolved_findings": [],
                        "url": "https://github.com/acme/demo/issues/42",
                        "ref": "#42",
                        "title_en": "Whitespace summary and reused snapshot",
                        "title_zh": "空白摘要且复用旧快照",
                        "filed_at": "2026-11-26",
                        "status": "verified_shipped",
                        "remediation": {
                            "fix_commit": sha0,
                            "fix_release": "v9.9.9",
                            "verified_at": "2026-11-27",
                            "verified_snapshot": snap1_name,
                            "verification_summary_en": "   ",
                            "verification_summary_zh": "   ",
                        },
                    },
                    {
                        # Duplicate URL under different ID + unmerged branch fix_commit
                        "id": "demo-42-clone",
                        "origin": "audit_filed",
                        "snapshot": snap1_name,
                        "finding_ids": ["F1"],
                        "resolved_findings": ["F1"],
                        "url": "https://github.com/ACME/demo/issues/42/",
                        "ref": "#42",
                        "title_en": "Duplicate issue URL with unmerged commit",
                        "title_zh": "重复链接且未合入主干",
                        "filed_at": "2026-11-26",
                        "status": "verified_main",
                        "remediation": {
                            "fix_commit": sha_branch,
                            "verified_at": "2026-11-27",
                            "verification_summary_en": "Unmerged branch commit.",
                            "verification_summary_zh": "未合入主干。",
                        },
                    },
                    {
                        # Relabeled existing issue + backdated filed_at + partial_fix with empty resolved_findings
                        "id": "demo-99",
                        "origin": "audit_filed",
                        "snapshot": snap1_name,
                        "finding_ids": ["F1"],
                        "resolved_findings": [],
                        "url": "https://github.com/acme/demo/issues/99",
                        "ref": "#99",
                        "title_en": "Relabeled existing community issue",
                        "title_zh": "篡改归因的既有社区单",
                        "filed_at": "2026-11-10",
                        "status": "partial_fix",
                        "remediation": {
                            "fix_commit": sha_branch,
                            "verified_at": "2026-11-27",
                            "verification_summary_en": "Partial.",
                            "verification_summary_zh": "部分修复。",
                        },
                    },
                    {
                        # Off-repo resolution_evidence_url for wontfix
                        "id": "demo-43",
                        "origin": "audit_filed",
                        "snapshot": snap1_name,
                        "finding_ids": ["F1"],
                        "url": "https://github.com/acme/demo/issues/43",
                        "ref": "#43",
                        "title_en": "Declined with unrelated URL",
                        "title_zh": "拒修链接无关",
                        "filed_at": "2026-11-26",
                        "status": "wontfix",
                        "resolution_evidence_url": "https://example.com/unrelated",
                    },
                ],
                "private_disclosures": [
                    {
                        "id": "cvd-1",
                        "snapshot": snap1_name,
                        "reported_at": "2026-11-26",
                        "window_days": 90,
                        "status": "embargoed",
                        "advisory_url": None,
                        "fix_commit": None,
                        "fix_release": None,
                        "disclosed_at": None,
                        "summary_en": "Embargoed neutral summary.",
                        "summary_zh": "暂隐摘要。",
                        "secret_extra": "https://leak.example/internal",
                    },
                    {
                        "id": "cvd-2",
                        "snapshot": snap1_name,
                        "reported_at": "2026-11-26",
                        "window_days": 90,
                        "status": "disclosed_fixed",
                        "advisory_url": "https://github.com/acme/demo/security/advisories/GHSA-1",
                        "fix_commit": None,
                        "fix_release": None,
                        "disclosed_at": "2026-11-27",
                        "summary_en": "Claimed fixed without fix commit.",
                        "summary_zh": "缺少修复提交。",
                    },
                ],
            }
            tpath = snap_dir / "tracking.json"
            tpath.write_text(json.dumps(bad_tracking, indent=2), encoding="utf-8")

            errs, _, _, _, _, _ = validate.validate_repo(
                [],
                root_dir=ws,
                local_map={"acme/demo": repo_dir},
                tracking_targets=[tpath],
            )
            rules = {e.rule for e in errs}
            self.assertIn("tracking-schema-extra-property", rules)
            self.assertIn("tracking-duplicate-url", rules)
            self.assertIn("tracking-origin-conflict", rules)
            self.assertIn("tracking-origin-backdated", rules)
            self.assertIn("tracking-fix-proof", rules)
            self.assertIn("tracking-shipped-proof", rules)
            self.assertIn("tracking-decline-proof", rules)
            self.assertIn("cvd-disclosure-proof", rules)
            self.assertIn("fix-commit-predates-audit", rules)
            self.assertIn("fix-commit-not-on-default-branch", rules)

            # Verify build_index NEVER leaks extra properties or unproved CVD credit into catalog.json
            catalog = build_index.build_catalog_data(build_index.discover_snapshots(ws / "audits", ws), ws / "audits")
            pd0 = catalog["projects"][0]["remediation"]["private_disclosures"][0]
            self.assertNotIn("secret_extra", pd0)
            self.assertEqual(catalog["impact_summary"]["private_cvd_fixed"], 0)

    def test_peak_split_axes_markdown_escaping_legacy_fixed_and_markers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws = _make_mini_workspace(tmp)
            snap_dir = ws / "audits" / "github" / "acme" / "demo"
            snap_dir.mkdir(parents=True)
            sha1, sha2, sha3 = "1" * 40, "2" * 40, "3" * 40
            # v1: H1/R4, v2: H4/R1, v3 (latest): H0/R1 -> peak H is H4 @ v2, peak R is R4 @ v1 (never H4/R4 @ v2!)
            (snap_dir / f"2026-11-20-{sha1[:8]}.md").write_text(
                _valid_snapshot(sha1, audited_at="2026-11-20", release="v1.0", honesty="H1", h3_scope="none", risk="R4", verdict="Avoid"),
                encoding="utf-8",
            )
            (snap_dir / f"2026-11-22-{sha2[:8]}.md").write_text(
                _valid_snapshot(sha2, audited_at="2026-11-22", release="v2.0", honesty="H4", h3_scope="primary", risk="R1", verdict="Isolate"),
                encoding="utf-8",
            )
            (snap_dir / f"2026-11-25-{sha3[:8]}.md").write_text(
                _valid_snapshot(
                    sha3,
                    audited_at="2026-11-25",
                    release="v3.0",
                    honesty="H0",
                    h3_scope="none",
                    risk="R1",
                    verdict="Adopt",
                    upstream_issues=["fixed|https://github.com/acme/demo/issues/42|Legacy fixed | pipe \\1 test"],
                    headline_en="Fixed pipe | and backslash \\1 safely in v3.0.",
                    headline_zh="安全转义竖线 | 与反斜杠 \\1。",
                ),
                encoding="utf-8",
            )

            catalog, _ = build_index.sync_indexes(root_dir=ws, check_only=False)
            # Legacy `fixed` without tracking.json MUST NOT award verified credit!
            self.assertEqual(catalog["impact_summary"]["verified_fixes_total"], 0)
            readme_en = (ws / "README.md").read_text(encoding="utf-8")
            self.assertIn("_(peak `H4` @ v2.0, `R4` @ v1.0)_", readme_en)
            self.assertNotIn("H4/R4", readme_en)
            self.assertIn("Fixed pipe \\| and backslash \\1 safely in v3.0.", readme_en)

            # Pre-existing badge preserves `existing ·` even on `pr_open`
            badge_en = build_index.format_issue_badge(
                {"ref": "#6467", "url": "https://github.com/a/b/issues/6467", "origin": "pre_existing", "status": "pr_open", "remediation": {"pr_url": "https://github.com/a/b/pull/6470", "pr_author_type": "community"}},
                "2026-11-27",
                "en",
            )
            badge_zh = build_index.format_issue_badge(
                {"ref": "#6467", "url": "https://github.com/a/b/issues/6467", "origin": "pre_existing", "status": "pr_open", "remediation": {"pr_url": "https://github.com/a/b/pull/6470", "pr_author_type": "community"}},
                "2026-11-27",
                "zh",
            )
            self.assertIn("existing · ext PR #6470", badge_en)
            self.assertIn("已有 · 社区 PR #6470", badge_zh)

            # Missing, duplicate, or nested IMPACT/CATALOG markers must raise ValueError
            (ws / "README.md").write_text("# missing impact\n<!-- CATALOG:BEGIN -->\n<!-- CATALOG:END -->\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_index.sync_indexes(root_dir=ws, check_only=True)

            (ws / "README.md").write_text(
                "<!-- IMPACT:BEGIN --><!-- IMPACT:END --><!-- IMPACT:BEGIN --><!-- IMPACT:END -->\n<!-- CATALOG:BEGIN --><!-- CATALOG:END -->\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                build_index.sync_indexes(root_dir=ws, check_only=True)

            (ws / "README.md").write_text(
                "<!-- IMPACT:BEGIN -->\n<!-- CATALOG:BEGIN -->\n<!-- CATALOG:END -->\n<!-- IMPACT:END -->\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                build_index.sync_indexes(root_dir=ws, check_only=True)

            # Bracket & pipe in issue ref must both stay escaped without double-escaping `|`
            bracket_badge = build_index.format_issue_badge(
                {"ref": "issue | [1]", "url": "https://github.com/a/b/issues/1", "origin": "audit_filed", "status": "open", "remediation": {}},
                "2026-11-27",
                "en",
            )
            self.assertIn("[issue \\| \\[1\\]](https://github.com/a/b/issues/1)", bracket_badge)
            self.assertNotIn("\\\\|", bracket_badge)

    def test_round2_hardening_url_aliases_dates_embargo_expiry_and_event_proofs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws = _make_mini_workspace(tmp)
            repo_dir = tmp / "main_repo"
            sha1 = _init_git_repo(repo_dir, {"README.md": "hi\n", "app/core.py": "x = 1\n"})
            sha2 = _commit_more(repo_dir, {"app/core.py": "x = 2\n"}, "fix")
            snap_dir = ws / "audits" / "github" / "acme" / "demo"
            snap_dir.mkdir(parents=True)
            snap1 = f"2026-11-26-{sha1[:8]}.md"
            snap_nosup = f"2026-11-28-{sha2[:8]}.md"
            (snap_dir / snap1).write_text(
                _valid_snapshot(
                    sha1,
                    audited_at="2026-11-26",
                    release="v1.0.0",
                    upstream_issues=["existing|https://github.com/acme/demo/issues/99|F1: existing"],
                ),
                encoding="utf-8",
            )
            # Newer snapshot WITHOUT supersedes_sha and WITHOUT git tag v2.0.0
            (snap_dir / snap_nosup).write_text(
                _valid_snapshot(sha2, audited_at="2026-11-28", release="v2.0.0", honesty="H0", h3_scope="none", risk="R1", verdict="Adopt"),
                encoding="utf-8",
            )

            bad = {
                "schema_version": 1,
                "repo": "acme/demo",
                "forge": "github",
                "last_checked_at": "2026-02-30",
                "items": [
                    {
                        # Browser-normalized URL alias (:443 + percent encoding + leading zero) + impossible date
                        "id": "alias-99",
                        "origin": "audit_filed",
                        "snapshot": snap1,
                        "finding_ids": ["F1"],
                        "resolved_findings": [],
                        "url": "https://github.com:443/%61cme/demo/issues/099",
                        "ref": "#99",
                        "title_en": "URL alias bypassing existing check",
                        "title_zh": "链接变体绕过",
                        "filed_at": "2026-02-31",
                        "status": "open",
                        "resolution_evidence_url": "https://github.com/acme/demo",
                        "events": [
                            {"date": "2026-11-28", "kind": "verified_shipped", "commit": None, "summary_en": "Fake event.", "summary_zh": "假事件。"}
                        ],
                    },
                    {
                        # Shipped against snapshot without supersedes_sha and without git release tag
                        "id": "ship-nosup",
                        "origin": "audit_filed",
                        "snapshot": snap1,
                        "finding_ids": ["F1", {}],
                        "resolved_findings": ["F1"],
                        "url": "https://github.com/acme/demo/issues/50",
                        "ref": "#50",
                        "title_en": "Missing supersedes and release tag",
                        "title_zh": "缺少取代链与标签",
                        "filed_at": "2026-11-26",
                        "status": "verified_shipped",
                        "remediation": {
                            "fix_commit": sha2,
                            "fix_release": "v2.0.0",
                            "verified_at": "2026-11-28",
                            "verified_snapshot": snap_nosup,
                            "verification_summary_en": "Verified.",
                            "verification_summary_zh": "已验真。",
                        },
                    },
                ],
                "private_disclosures": [
                    {
                        "id": "cvd-prose",
                        "snapshot": snap1,
                        "reported_at": "2026-11-26",
                        "window_days": 90,
                        "status": "embargoed",
                        "advisory_url": None,
                        "fix_commit": None,
                        "fix_release": None,
                        "disclosed_at": None,
                        "summary_en": "Auth bypass in Windows updater.",
                        "summary_zh": "Windows 更新器认证绕过。",
                    },
                    {
                        "id": "cvd-early",
                        "snapshot": snap1,
                        "reported_at": "2026-11-26",
                        "window_days": 90,
                        "status": "disclosed_expired",
                        "advisory_url": None,
                        "fix_commit": None,
                        "fix_release": None,
                        "disclosed_at": "2026-12-01",
                        "summary_en": "Disclosed before 90 days.",
                        "summary_zh": "未满 90 天提前披露。",
                    },
                    {
                        "id": "cvd-early-fixed",
                        "snapshot": snap1,
                        "reported_at": "2026-11-26",
                        "window_days": 90,
                        "status": "disclosed_fixed",
                        "advisory_url": None,
                        "fix_commit": sha2,
                        "fix_release": None,
                        "disclosed_at": "2026-11-20",
                        "summary_en": "Early disclosed_fixed without release and before report.",
                        "summary_zh": "早于报告且无发版提前披露。",
                    },
                ],
            }
            tpath = snap_dir / "tracking.json"
            tpath.write_text(json.dumps(bad, indent=2), encoding="utf-8")

            errs, _, _, _, _, _ = validate.validate_repo(
                [],
                root_dir=ws,
                local_map={"acme/demo": repo_dir},
                tracking_targets=[tpath],
            )
            rules = {e.rule for e in errs}
            self.assertIn("tracking-invalid-date", rules)
            self.assertIn("tracking-origin-conflict", rules)
            self.assertIn("tracking-decline-proof", rules)
            self.assertIn("tracking-event-proof", rules)
            self.assertIn("tracking-shipped-proof", rules)
            self.assertIn("fix-release-tag-missing", rules)
            self.assertIn("cvd-embargo-leak", rules)
            self.assertIn("cvd-disclosure-proof", rules)
            self.assertIn("tracking-schema-type", rules)

            # Scalar `items` and 4301-digit issue ID must return structured diagnostics without raising
            huge_id = "1" * 4301
            self.assertTrue(validate._normalize_canonical_url(f"https://github.com/acme/demo/issues/{huge_id}").endswith(huge_id))
            tpath.write_text(json.dumps({"schema_version": 1, "repo": "acme/demo", "forge": "github", "last_checked_at": "2026-11-27", "items": "bad"}), encoding="utf-8")
            errs_scalar, _, _, _, _, _ = validate.validate_repo([], root_dir=ws, tracking_targets=[tpath])
            self.assertIn("tracking-schema-type", {e.rule for e in errs_scalar})

    def test_posture_derivation_for_silent_mixed_and_community_pr(self) -> None:
        p1 = build_index.derive_remediation_posture(
            [
                {"origin": "audit_filed", "status": "pr_open", "filed_at": "2026-11-26", "maintainer_response": {"replied": False}, "remediation": {"pr_author_type": "community"}},
                {"origin": "pre_existing", "status": "open", "filed_at": "2026-11-18", "maintainer_response": {"replied": False}, "remediation": {}},
            ],
            "2026-11-27",
        )
        self.assertEqual(p1["code"], "community_prs_only")

        p2 = build_index.derive_remediation_posture(
            [
                {"origin": "audit_filed", "status": "open", "filed_at": "2026-10-20", "maintainer_response": {"replied": False}, "remediation": {}},
            ],
            "2026-11-24",
        )
        self.assertEqual(p2["code"], "no_response_30d")
        self.assertEqual(p2["max_silent_days"], 35)

        p3 = build_index.derive_remediation_posture(
            [
                {"origin": "audit_filed", "status": "verified_main", "filed_at": "2026-11-27", "maintainer_response": {"replied": True}, "remediation": {}},
                {"origin": "audit_filed", "status": "wontfix", "filed_at": "2026-11-27", "maintainer_response": {"replied": True}, "remediation": {}},
            ],
            "2026-11-28",
        )
        self.assertEqual(p3["code"], "mixed_response")

    def test_sync_issues_null_fields_partial_failure_recompute_and_never_auto_verifies(self) -> None:
        # 1. Explicit null maintainer_response & remediation must not crash; closed-as-completed -> fix_claimed (never verified)
        tracking = {
            "schema_version": 1,
            "repo": "acme/demo",
            "forge": "github",
            "official_bots": ["acme-triage[bot]"],
            "last_checked_at": "2026-11-26",
            "latest_upstream_release": None,
            "items": [
                {
                    "id": "demo-10",
                    "origin": "audit_filed",
                    "snapshot": "2026-11-26-aaaaaaaa.md",
                    "finding_ids": ["F1"],
                    "url": "https://github.com/acme/demo/issues/10",
                    "ref": "#10",
                    "title_en": "Opt-out bypass",
                    "title_zh": "绕过开关",
                    "filed_at": "2026-11-26T00:00:00Z",
                    "status": "open",
                    "maintainer_response": None,
                    "remediation": None,
                    "observed": {"github_state": "open", "state_reason": None, "labels": [], "closed_at": None, "linked_prs": [{"number": 11, "url": "https://github.com/acme/demo/pull/11", "state": "open", "merged": False}]},
                }
            ],
            "private_disclosures": [],
        }
        fake_sha = "b" * 40
        fixtures: dict[str, Any] = {
            "repos/acme/demo/releases/latest": {"tag_name": "v1.0.0", "published_at": "2026-11-20T00:00:00Z", "html_url": "https://github.com/acme/demo/releases/tag/v1.0.0"},
            "repos/acme/demo/issues/10": {"state": "closed", "state_reason": "completed", "closed_at": "2026-11-27T08:00:00Z", "labels": [{"name": "bug"}]},
            "repos/acme/demo/issues/10/comments": [{"user": {"login": "acme-triage[bot]"}, "author_association": "NONE", "created_at": "2026-11-26T02:00:00Z"}],
            "repos/acme/demo/issues/10/timeline": [
                {"event": "cross-referenced", "source": {"issue": {"html_url": "https://github.com/random-fork/demo/pull/999"}}},
                {"event": "cross-referenced", "source": {"issue": {"html_url": "https://github.com/acme/demo/pull/11"}}},
            ],
            "repos/acme/demo/pulls/11": {
                "html_url": "https://github.com/acme/demo/pull/11",
                "state": "closed",
                "merged": True,
                "merged_at": "2026-11-27T08:00:00Z",
                "merge_commit_sha": fake_sha,
                "user": {"login": "contrib1"},
                "author_association": "CONTRIBUTOR",
            },
        }
        updated, alerts, _ = sync_issues.sync_project_tracking_data(tracking, fetcher=lambda p, paginate=False: fixtures[p], checked_date="2026-11-27")
        item = updated["items"][0]
        self.assertEqual(item["status"], "fix_claimed")
        self.assertEqual(updated["last_checked_at"], "2026-11-27")
        self.assertTrue(item["maintainer_response"]["replied"])
        self.assertEqual(item["remediation"]["fix_commit"], fake_sha)
        self.assertEqual([pr["number"] for pr in item["observed"]["linked_prs"]], [11])
        self.assertIn("verify_fix", {a["kind"] for a in alerts})

        # 2. HTTP 429 on linked PR must preserve prior linked_prs and NOT advance last_checked_at!
        def rate_limited_fetcher(path: str, paginate: bool = False) -> Any:
            if path == "repos/acme/demo/pulls/11":
                raise urllib.error.HTTPError(path, 429, "Too Many Requests", {}, None)
            return fixtures[path]

        updated_429, alerts_429, _ = sync_issues.sync_project_tracking_data(tracking, fetcher=rate_limited_fetcher, checked_date="2026-11-29")
        self.assertEqual(updated_429["last_checked_at"], "2026-11-26")
        self.assertEqual([pr["number"] for pr in updated_429["items"][0]["observed"]["linked_prs"]], [11])
        self.assertIn("api_error", {a["kind"] for a in alerts_429})

        # 3. Reopening an unverified fix_claimed issue with stale merged PR (before reopened) resets cleanly to open
        reopened_fixtures = dict(fixtures)
        reopened_fixtures["repos/acme/demo/issues/10"] = {"state": "open", "state_reason": "reopened", "closed_at": None, "labels": []}
        reopened_fixtures["repos/acme/demo/issues/10/timeline"] = [
            {"event": "cross-referenced", "source": {"issue": {"html_url": "https://github.com/acme/demo/pull/11"}}},
            {"event": "reopened", "created_at": "2026-11-28T00:00:00Z"},
        ]
        updated_reopen, alerts_reopen, _ = sync_issues.sync_project_tracking_data(updated, fetcher=lambda p, paginate=False: reopened_fixtures[p], checked_date="2026-11-28")
        self.assertEqual(updated_reopen["items"][0]["status"], "open")
        self.assertIsNone(updated_reopen["items"][0]["remediation"]["fix_commit"])
        self.assertNotIn("verify_fix", {a["kind"] for a in alerts_reopen})

        # 4. A fresh PR merged AFTER reopening transitions back to fix_claimed and emits verify_fix
        fresh_sha = "c" * 40
        post_reopen_fixtures = dict(reopened_fixtures)
        post_reopen_fixtures["repos/acme/demo/issues/10/timeline"] = [
            {"event": "cross-referenced", "source": {"issue": {"html_url": "https://github.com/acme/demo/pull/11"}}},
            {"event": "reopened", "created_at": "2026-11-28T00:00:00Z"},
            {"event": "cross-referenced", "source": {"issue": {"html_url": "https://github.com/acme/demo/pull/12"}}},
        ]
        post_reopen_fixtures["repos/acme/demo/pulls/12"] = {
            "html_url": "https://github.com/acme/demo/pull/12",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-11-29T10:00:00Z",
            "merge_commit_sha": fresh_sha,
            "user": {"login": "contrib2"},
            "author_association": "CONTRIBUTOR",
        }
        updated_fresh, alerts_fresh, _ = sync_issues.sync_project_tracking_data(updated_reopen, fetcher=lambda p, paginate=False: post_reopen_fixtures[p], checked_date="2026-11-29")
        self.assertEqual(updated_fresh["items"][0]["status"], "fix_claimed")
        self.assertEqual(updated_fresh["items"][0]["remediation"]["fix_commit"], fresh_sha)
        self.assertEqual(updated_fresh["items"][0]["remediation"]["pr_url"], "https://github.com/acme/demo/pull/12")
        self.assertIn("verify_fix", {a["kind"] for a in alerts_fresh})

    def test_fail_closed_git_ancestry_early_cvd_tag_date_and_regressed_shipped_proof(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws = _make_mini_workspace(tmp)
            repo_dir = tmp / "repo"
            sha1 = _init_git_repo(repo_dir, {"README.md": "hi\n", "app/core.py": "x = 1\n"})
            # Commit fix with an explicit date of 2026-12-10 and annotated tag on 2026-12-10
            (repo_dir / "app/core.py").write_text("x = 2\n", encoding="utf-8")
            env_future = {
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "t@e.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "t@e.com",
                "GIT_AUTHOR_DATE": "2026-12-10T12:00:00Z",
                "GIT_COMMITTER_DATE": "2026-12-10T12:00:00Z",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            }
            subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True, env=env_future)
            subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "cvd fix"], check=True, env=env_future)
            sha2 = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", str(repo_dir), "tag", "-a", "v2.0.0", "-m", "release v2.0.0"], check=True, env=env_future)

            snap_dir = ws / "audits" / "github" / "acme" / "demo"
            snap_dir.mkdir(parents=True)
            snap1 = f"2026-11-26-{sha1[:8]}.md"
            (snap_dir / snap1).write_text(_valid_snapshot(sha1, audited_at="2026-11-26", release="v1.0.0"), encoding="utf-8")

            # 1. Early CVD disclosed_at (2026-12-01) predating tag date (2026-12-10) -> cvd-embargo-leak
            # 2. Regressed item with verified_shipped event omitting fix_release/verified_snapshot -> tracking-shipped-proof
            tpath = snap_dir / "tracking.json"
            tpath.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repo": "acme/demo",
                        "forge": "github",
                        "last_checked_at": "2026-12-11",
                        "items": [
                            {
                                "id": "reg-ship-1",
                                "origin": "audit_filed",
                                "snapshot": snap1,
                                "finding_ids": ["F1"],
                                "resolved_findings": [],
                                "url": "https://github.com/acme/demo/issues/1",
                                "ref": "#1",
                                "title_en": "Regressed without shipped evidence",
                                "title_zh": "缺少发版证据的回退项",
                                "filed_at": "2026-11-26",
                                "status": "regressed",
                                "remediation": {"fix_commit": sha2},
                                "events": [
                                    {"date": "2026-12-10", "kind": "verified_shipped", "commit": sha2, "summary_en": "Shipped.", "summary_zh": "已发版。"}
                                ],
                            }
                        ],
                        "private_disclosures": [
                            {
                                "id": "cvd-valid-first",
                                "snapshot": snap1,
                                "reported_at": "2026-11-26",
                                "window_days": 90,
                                "status": "disclosed_fixed",
                                "advisory_url": None,
                                "fix_commit": sha2,
                                "fix_release": "v2.0.0",
                                "disclosed_at": "2026-12-10",
                                "summary_en": "Valid early disclosure on release tag date.",
                                "summary_zh": "在发版标签当日披露。",
                            },
                            {
                                "id": "cvd-early-tag",
                                "snapshot": snap1,
                                "reported_at": "2026-11-26",
                                "window_days": 90,
                                "status": "disclosed_fixed",
                                "advisory_url": None,
                                "fix_commit": sha2,
                                "fix_release": "v2.0.0",
                                "disclosed_at": "2026-12-01",
                                "summary_en": "Disclosed before release tag date.",
                                "summary_zh": "在发版标签日期前提前披露。",
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            errs, _, _, _, _, _ = validate.validate_repo([], root_dir=ws, local_map={"acme/demo": repo_dir}, tracking_targets=[tpath])
            rules = {e.rule for e in errs}
            self.assertIn("tracking-shipped-proof", rules)
            self.assertIn("cvd-embargo-leak", rules)
            self.assertIn("fix-commit-not-in-release", rules)

            # Once disclosed_at >= tag date (2026-12-10), early CVD check passes cleanly
            chk_ok = validate.CommitCheck(
                forge="github",
                repo="acme/demo",
                sha=sha2,
                ctx_label="test",
                mode="cvd_shipped",
                orig_snap_sha=sha1,
                fix_release="v2.0.0",
                early_disclosed_at="2026-12-10",
            )
            self.assertEqual(validate.verify_commit_in_local_repo(repo_dir, chk_ok), [])

            # 3. Failed shallowness probe or pre-audit merge-base git error MUST fail closed
            chk_main = validate.CommitCheck(
                forge="github",
                repo="acme/demo",
                sha=sha2,
                ctx_label="test",
                mode="verified_main",
                orig_snap_sha=sha1,
            )
            with mock.patch.object(validate, "_git_is_shallow_checked", return_value=None):
                e_shallow = validate.verify_commit_in_local_repo(repo_dir, chk_main)
                self.assertEqual([e.rule for e in e_shallow], ["fix-commit-orig-missing"])
            with mock.patch.object(validate, "_git_is_ancestor_checked", return_value=None):
                e_anc = validate.verify_commit_in_local_repo(repo_dir, chk_main)
                self.assertEqual([e.rule for e in e_anc], ["fix-commit-orig-missing"])

            # 4. Upstream verifier never silently falls back to HEAD when verified_shipped lacks target_snap_sha/fix_release
            chk_missing_ship = validate.CommitCheck(
                forge="github",
                repo="acme/demo",
                sha=sha2,
                ctx_label="test",
                mode="verified_shipped",
                orig_snap_sha=sha1,
                target_snap_sha=None,
                fix_release=None,
            )
            e_up = validate.verify_commit_in_upstream_repo(tmp / "up_dest", chk_missing_ship)
            self.assertEqual([e.rule for e in e_up], ["fix-commit-not-in-release"])

    def test_new_audit_scaffolder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ws = _make_mini_workspace(tmp)
            sha = "a" * 40
            out = new_audit.scaffold_snapshot(
                repo="org/proj",
                sha=sha,
                release="v2.0.0",
                category="coding-agent",
                audited_at="2026-11-26",
                root_dir=ws,
            )
            self.assertTrue(out.is_file())
            self.assertEqual(out.name, f"2026-11-26-{sha[:8]}.md")
            content = out.read_text(encoding="utf-8")
            self.assertIn(f"sha: {sha}", content)
            self.assertIn('release: "v2.0.0"', content)
            with self.assertRaises(FileExistsError):
                new_audit.scaffold_snapshot(repo="org/proj", sha=sha, category="coding-agent", audited_at="2026-11-26", root_dir=ws)


if __name__ == "__main__":
    unittest.main()
