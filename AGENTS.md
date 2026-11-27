# AGENTS: trust-no-readme

> **Audience:** Visiting AI agents (Claude Code, Codex, Cursor, OpenCode, custom agents) and human contributors reading, querying, auditing, or submitting PRs/Issues to `trust-no-readme`.

`trust-no-readme` is a public, community-open archive of **commit-pinned, mechanically verified open-source trust audits**. Every finding compares explicit project claims against actual code behavior at an exact 40-character Git commit SHA, and every cited `file:line` is checked against upstream source code in CI.

---

## 1. Machine-Readable Quick Map (Read This First)

| Need | Canonical File / Command |
|---|---|
| Query all tracked projects, ratings & impact | [`data/catalog.json`](./data/catalog.json) (schema v3; never scrape README tables) |
| Frontmatter JSON Schema | [`data/schema/audit-frontmatter.schema.json`](./data/schema/audit-frontmatter.schema.json) |
| Upstream Remediation Tracking Schema | [`data/schema/tracking.schema.json`](./data/schema/tracking.schema.json) |
| Canonical English audit template | [`templates/audit-snapshot.md`](./templates/audit-snapshot.md) |
| Post-fix re-audit snapshot template | [`templates/reaudit-snapshot.md`](./templates/reaudit-snapshot.md) |
| Maintainer official response template | [`templates/maintainer-response.md`](./templates/maintainer-response.md) |
| Upstream engineering Issue template | [`templates/upstream-issue.md`](./templates/upstream-issue.md) |
| Scoring rubric & evidence tiers | [`METHODOLOGY.md`](./METHODOLOGY.md) |
| Disclosure & right-of-reply policy | [`DISCLOSURE.md`](./DISCLOSURE.md) |
| Scaffold a new snapshot file | `python3 tools/new_audit.py --repo owner/name --sha <40-hex> --category <cat>` |
| Sync observed upstream issue states | `python3 tools/sync_issues.py --format json` (add `--write` to update `tracking.json`) |
| Regenerate indexes, impact & README tables | `python3 tools/build_index.py` |
| Run mechanical validator (JSON output) | `python3 tools/validate.py --verify-upstream --format json` |
| Run tooling unit tests | `python3 -m unittest discover -s tests -v` |

---

## 2. Six Supported Agent Workflows

### Workflow A: Query Existing Audits & Impact (Read-Only)
1. Read [`data/catalog.json`](./data/catalog.json) for structured project status, current vs. historical peak ratings (`honesty`, `risk`, `verdict`, `history`), `impact_summary`, `verified_fixes`, per-project `remediation` posture/items, and report paths.
2. Open the linked canonical English report (`audits/<forge>/<owner>/<repo>/YYYY-MM-DD-<8char-sha>.md`), its `.zh-CN.md` sibling, or `tracking.json`.

### Workflow B: Submit a New Audit Snapshot (PR)
1. **Verify disclosure eligibility first** ([`DISCLOSURE.md`](./DISCLOSURE.md)):
   - Public contract contradictions, broken opt-outs, undisclosed telemetry, incomplete deletion, and default boundary risks (`disclosure: public`) may be submitted here.
   - **Never** submit unpatched exploitable security vulnerabilities (auth bypass, RCE, token theft, exploit payloads). Those must go privately to the upstream project's `SECURITY.md` or GitHub Private Vulnerability Reporting first.
2. **Scaffold the snapshot** (canonical primary language is **English**):
   ```bash
   python3 tools/new_audit.py \
     --repo owner/name \
     --sha <full-40-char-sha> \
     --release <tag-or-version> \
     --category <category-enum>
   ```
3. **Fill the report** following [`templates/audit-snapshot.md`](./templates/audit-snapshot.md):
   - Keep `headline_en` ≤ 140 chars and `headline_zh` ≤ 90 chars (single crisp sentence for the root table).
   - Anchor every claim and finding to clickable GitHub permalinks at the exact pinned SHA and include 5–15 line verbatim source excerpts for top findings.
   - If citing a pinned companion repository (e.g., a dependency repo), declare it in `companion_repos` (`alias=forge:owner/repo@<40-char-sha>`) and cite it as `alias:path/to/file.py:10-20` so CI verifies it mechanically too.
   - Optional Chinese translation: save alongside as `YYYY-MM-DD-<8char-sha>.zh-CN.md` with `language: zh-CN` and `translation_of: YYYY-MM-DD-<8char-sha>.md`.
4. **Regenerate indexes & run full mechanical verification** (fix all reported errors until exit code `0`):
   ```bash
   python3 tools/build_index.py
   python3 tools/build_index.py --check
   python3 tools/validate.py --verify-upstream --format json
   ```
   *(Offline local-clone verification also works: `python3 tools/validate.py --local-repo owner/name=/path/to/clone --format json`.)*
5. **Open a Pull Request** completing every item in [`.github/PULL_REQUEST_TEMPLATE.md`](./.github/PULL_REQUEST_TEMPLATE.md).

### Workflow C: Submit a Fix & Re-Audit Snapshot (PR)
When an upstream project ships a fix in a new release (or resolves decisive findings changing `H`, `R`, or `Verdict`):
1. **Never overwrite** the historical SHA snapshot.
2. Scaffold a new snapshot for the newer commit SHA:
   ```bash
   python3 tools/new_audit.py --repo owner/name --sha <new-40-char-sha> --reaudit
   ```
3. Fill [`templates/reaudit-snapshot.md`](./templates/reaudit-snapshot.md), update `audits/<forge>/<owner>/<repo>/tracking.json` items to `verified_shipped` (with `fix_commit`, `fix_release`, and `verified_snapshot`), run `build_index.py` (which marks the older snapshot `superseded` while preserving the project's historical peak in `data/catalog.json` and READMEs), and pass `validate.py --verify-upstream`.

### Workflow D: Submit an Official Maintainer Response (PR)
Upstream maintainers (or their agents) may add or update `audits/<forge>/<owner>/<repo>/responses.md` using [`templates/maintainer-response.md`](./templates/maintainer-response.md), run `python3 tools/build_index.py` to surface the link on the project page, and open a PR.

### Workflow E: Open a Structured Issue
Do not open unstructured issues. Use the exact schemas in `.github/ISSUE_TEMPLATE/`:
- **Nominate a project:** [`request-audit.yml`](./.github/ISSUE_TEMPLATE/request-audit.yml) (`[Audit Request] owner/repo`)
- **Notify of an upstream fix:** [`report-fix-reaudit.yml`](./.github/ISSUE_TEMPLATE/report-fix-reaudit.yml) (`[Fix Re-Audit] owner/repo`)
- **Challenge a finding with code counter-evidence:** [`dispute-finding.yml`](./.github/ISSUE_TEMPLATE/dispute-finding.yml) (`[Dispute] owner/repo: F#`)

### Workflow F: Sync Upstream Issues & Verify Fixes (PR)
1. Weekly automated read-only observation runs via [`.github/workflows/sync-issues.yml`](./.github/workflows/sync-issues.yml) (`tools/sync_issues.py` + `tools/publish_sync_digest.py`), maintaining a single rolling triage digest Issue (`upstream-sync`) when upstream replies, PR merges, or new releases occur; it never pushes to `main` and never auto-verifies fixes.
2. Run `python3 tools/sync_issues.py --format json` locally (add `--write` to update `observed` states, `latest_upstream_release`, `last_checked_at`, and safe non-verification transitions such as `fix_claimed` in `tracking.json`).
3. Review emitted alerts (`verify_fix`, `check_release`, `review_reopened`, `review_not_planned`, `review_maintainer_reply`, `silent_14d`, `silent_30d`).
4. **Never trust upstream closure blindly.** Before setting `verified_main` or `partial_fix`, inspect the actual upstream `fix_commit` diff (and run sandbox verification when behavior changed), then fill `fix_commit`, `resolved_findings`, `verified_at`, and bilingual `verification_summary_*` in `tracking.json`.
5. Run `python3 tools/build_index.py && python3 tools/validate.py --verify-upstream --format json` (which mechanically confirms `fix_commit` exists upstream) before opening a PR.

---

## 3. Hard Guardrails (Enforced Mechanically by CI)

1. **Public evidence only.** Never commit private raw dumps, unvetted drafts, model transcripts, social-media copy, or local machine paths (`/home/...`, `/Users/...`, `/tmp/...`, `C:\Users\...`). Embargoed `private_disclosures` entries in `tracking.json` must contain zero technical specifics, URLs, or `file:line` citations.
2. **Zero intent accusations.** State observable code and tracker behavior (`reads`, `hashes`, `sends`, `retains`, `omits`, `contradicts`, `closed without fix`, `replied in 28m`). CI rejects banned intent words (English and Chinese) outside verbatim quotes in both snapshots and `tracking.json`.
3. **Zero hallucinated citations or fix credits.** Every `file:line`, permalink SHA, companion repo citation, adjacent code identifier, and claimed `fix_commit` in `tracking.json` is checked against the real upstream repository in CI.
4. **Never hand-edit generated files.** `data/catalog.json`, `audits/<forge>/<owner>/<repo>/README.md`, and the `<!-- IMPACT:BEGIN -->`…`<!-- IMPACT:END -->` / `<!-- CATALOG:BEGIN -->`…`<!-- CATALOG:END -->` blocks in `README.md` / `README.zh-CN.md` are owned exclusively by `python3 tools/build_index.py`.
5. **Immutable history & tight PR scope.** Historical `YYYY-MM-DD-<shortsha>.md` (and `.zh-CN.md`) snapshots are strictly immutable. Tracking updates edit `tracking.json` (plus regenerated indexes) without touching frozen snapshots. Do not modify `tools/`, `.github/`, or `data/schema/` inside an audit or tracking PR.
6. **Zero unfilled placeholders.** CI rejects template placeholders (`<40-char-sha>`, `TODO:`, `TBD`).
7. **Upstream Issue etiquette.** When filing corresponding bug/doc-mismatch issues on upstream repos, use [`templates/upstream-issue.md`](./templates/upstream-issue.md): de-duplicate first, file only actionable `PUBLIC_NOW` defects, omit internal `H/R` rating jargon, and record filed links in `audits/<forge>/<owner>/<repo>/tracking.json`.
8. **Clean human prose (no em-dashes).** Avoid em-dashes (`—` / `——`) in authored report prose, `tracking.json` summaries, READMEs, and upstream issues; use standard commas, colons, semicolons, or parentheses instead (preserve upstream quotes verbatim only inside direct quotations or code blocks).
