"""Unit tests for trust-no-readme tooling (build_index, validate, new_audit)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_index  # noqa: E402
import new_audit  # noqa: E402
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


def _make_mini_workspace(tmp: Path) -> Path:
    ws = tmp / "ws"
    (ws / "data" / "schema").mkdir(parents=True)
    shutil.copy2(ROOT / "data" / "schema" / "audit-frontmatter.schema.json", ws / "data" / "schema" / "audit-frontmatter.schema.json")
    shutil.copytree(ROOT / "templates", ws / "templates")
    (ws / "README.md").write_text("# test\n<!-- CATALOG:BEGIN -->\n<!-- CATALOG:END -->\n", encoding="utf-8")
    (ws / "README.zh-CN.md").write_text("# 测试\n<!-- CATALOG:BEGIN -->\n<!-- CATALOG:END -->\n", encoding="utf-8")
    return ws


def _valid_snapshot(sha: str, comp_sha: str | None = None, extra_body: str = "") -> str:
    comp_line = f'companion_repos:\n  - "dep=github:acme/dep@{comp_sha}"\n' if comp_sha else "companion_repos: []\n"
    comp_ref = f"\n- Companion check: `dep:lib/helper.py:2-3` calls `send_raw()`.\n" if comp_sha else ""
    return f"""---
repo: acme/demo
forge: github
sha: {sha}
release: "v1.0.0"
audited_at: 2026-11-26
category: dev-tool
honesty: H3
h3_scope: primary
risk: R2
verdict: Isolate
evidence_max: E3+D
coverage: full-exhaustive
verification: static+dynamic
git_depth: full
disclosure: public
status: active
language: en
headline_en: "Disabling telemetry still calls send_metrics() on shutdown in demo v1.0.0."
headline_zh: "关闭遥测开关后，demo v1.0.0 退出时仍调用 send_metrics()。"
{comp_line}upstream_issues:
  - "open|https://github.com/acme/demo/issues/42|F1: send_metrics ignores opt-out"
tags:
  - placebo-opt-out
---

# acme/demo — Code vs. Claims Audit

## Executive Verdict

| ID | Finding | H / R | Evidence | Default? | Upstream Issue |
|---|---|---|---|---|---|
| F1 | [Opt-out ignored](#f1-opt-out-ignored) | `H3 / R2` | `E3+D` | Yes | [#42](https://github.com/acme/demo/issues/42) |

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
                    "app/core.py": "# core\ndef shutdown():\n    send_metrics(\"ping\")\n    return True\n",
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

            errs, snaps, trans, cits = validate.validate_repo(
                [en_path, zh_path],
                root_dir=ws,
                local_map={"acme/demo": tmp / "main_repo", "dep": tmp / "comp_repo"},
            )
            self.assertEqual(errs, [], [e.format_text() for e in errs])
            self.assertEqual(snaps, 1)
            self.assertEqual(trans, 1)
            self.assertGreaterEqual(cits, 4)

            catalog, drifted = build_index.sync_indexes(root_dir=ws, check_only=False)
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
                    "app/core.py": "# core\ndef shutdown():\n    send_metrics(\"ping\")\n    return True\n",
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

            errs, _, _, _ = validate.validate_repo(
                [en_path], root_dir=ws, local_map={"acme/demo": tmp / "main_repo"}
            )
            rules = {e.rule for e in errs}
            self.assertIn("banned-intent-word", rules)
            self.assertIn("permalink-sha-mismatch", rules)
            self.assertIn("path-leak", rules)
            self.assertIn("unfilled-placeholder", rules)
            self.assertIn("excerpt-mismatch", rules)
            self.assertIn("upstream-symbol-mismatch", rules)

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
