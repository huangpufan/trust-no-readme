# Contributing to `trust-no-readme`

We welcome project nominations, upstream fix notifications, factual counter-evidence, maintainer responses, and community audit PRs from both humans and AI agents.

> **Using an AI coding agent?** Point it directly at [`AGENTS.md`](./AGENTS.md) (or [`llms.txt`](./llms.txt)). Every template, schema, scaffolder, and JSON-output validator is built so an agent can self-discover the workflow and pass every CI gate locally before opening a PR.

---

## 1. Ways to Contribute

| Contribution | Entrypoint |
|---|---|
| **Nominate an open-source project** | [Audit Request Issue](https://github.com/huangpufan/trust-no-readme/issues/new?template=request-audit.yml) |
| **Report an upstream fix for re-audit** | [Fix & Re-Audit Issue](https://github.com/huangpufan/trust-no-readme/issues/new?template=report-fix-reaudit.yml) or open a Re-Audit PR |
| **Dispute a finding with code counter-evidence** | [Dispute / Correction Issue](https://github.com/huangpufan/trust-no-readme/issues/new?template=dispute-finding.yml) |
| **Submit an official maintainer response** | PR adding `audits/<forge>/<owner>/<repo>/responses.md` ([template](./templates/maintainer-response.md)) |
| **Submit a new audit or re-audit** | PR adding `audits/<forge>/<owner>/<repo>/YYYY-MM-DD-<8char-sha>.md` ([template](./templates/audit-snapshot.md)) |

---

## 2. Submitting an Audit or Re-Audit PR

### Step 1: Check Disclosure Rules First
Read [`DISCLOSURE.md`](./DISCLOSURE.md). Only `PUBLIC_NOW` findings (explicit claim contradictions, broken opt-outs, undisclosed telemetry, incomplete deletion, documented-vs-default permission boundaries) belong in public PRs. Never submit unpatched exploitable vulnerabilities or exploit payloads.

### Step 2: Scaffold the Snapshot
Canonical reports are written in **English** so global users and upstream maintainers can verify them directly. Optional Chinese translations live beside them as `YYYY-MM-DD-<8char-sha>.zh-CN.md`.

```bash
# New audit
python3 tools/new_audit.py \
  --repo owner/repo \
  --sha <full-40-char-commit-sha> \
  --release <version-tag> \
  --category <category>

# Post-fix re-audit (never overwrite an existing SHA snapshot)
python3 tools/new_audit.py \
  --repo owner/repo \
  --sha <new-40-char-commit-sha> \
  --release <new-version-tag> \
  --reaudit
```

### Step 3: Fill Evidence & Declare Companion Repos
- Anchor every finding with clickable GitHub permalinks at the exact pinned SHA and short verbatim source excerpts (5–15 lines).
- If a finding cites a pinned companion repository (for example, a companion dependency package), declare it in frontmatter `companion_repos` (`alias=forge:owner/repo@<40-hex>`) and cite files as `alias:path/to/file.py:10-20` so CI verifies them too.
- Keep `headline_en` ≤ 140 chars and `headline_zh` ≤ 90 chars.
- If you filed actionable upstream bug/doc-mismatch issues (following [`templates/upstream-issue.md`](./templates/upstream-issue.md)), record them in `audits/<forge>/<owner>/<repo>/tracking.json` ([schema](./data/schema/tracking.schema.json)). Never overwrite frozen historical snapshots just to update issue status.

### Step 4: Regenerate Indexes & Run Mechanical Verification
Never hand-edit `data/catalog.json`, per-project `README.md`, or the `<!-- IMPACT:BEGIN -->`…`<!-- IMPACT:END -->` / `<!-- CATALOG:BEGIN -->`…`<!-- CATALOG:END -->` blocks in the root READMEs.

```bash
python3 tools/sync_issues.py --format json   # optional: observe live GitHub issue states
python3 tools/build_index.py
python3 tools/build_index.py --check
python3 tools/validate.py --verify-upstream
# For agents: add --format json for structured, self-repairable diagnostics
```

---

## 3. Mandatory CI Gates

Every Pull Request is automatically verified by [`.github/workflows/ci.yml`](./.github/workflows/ci.yml):
1. **Tooling unit tests** (`python3 -m unittest discover -s tests -v`).
2. **Frontmatter & tracking schema parity** against [`data/schema/audit-frontmatter.schema.json`](./data/schema/audit-frontmatter.schema.json) and [`data/schema/tracking.schema.json`](./data/schema/tracking.schema.json) (rejects `disclosure: embargoed`, unproved fix claims, embargoed CVD leaks, malformed SHAs, oversized headlines, and unfilled template placeholders).
3. **Structure & wording hygiene**: required sections, `F1..Fn` table-to-detail and `tracking.json` cross-reference parity, zero banned EN/ZH intent words outside quotes, zero local machine path leaks.
4. **Mechanical upstream verification**: CI inertly fetches the exact pinned SHA, declared `companion_repos` SHAs, and every claimed `fix_commit` in `tracking.json` read-only and verifies every cited `file:line`, permalink SHA, adjacent code identifier, and upstream fix commit.
5. **Generated index freshness** (`python3 tools/build_index.py --check`).
6. **PR scope hygiene**: audit and tracking PRs may add new snapshots or update `tracking.json` / `responses.md` and regenerated indexes, and must never overwrite existing historical snapshots or touch protected `tools/` / `.github/` / `data/schema/` files unless explicitly labeled as a tooling change.
