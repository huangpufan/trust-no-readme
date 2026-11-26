# AGENTS: trust-no-readme

> **Audience:** Visiting AI agents (Claude Code, Codex, Cursor, OpenCode, custom agents) and human contributors reading, querying, auditing, or submitting PRs/Issues to `trust-no-readme`.

`trust-no-readme` is a public, community-open archive of **commit-pinned, mechanically verified open-source trust audits**. Every finding compares explicit project claims against actual code behavior at an exact 40-character Git commit SHA, and every cited `file:line` is checked against upstream source code in CI.

---

## 1. Machine-Readable Quick Map (Read This First)

| Need | Canonical File / Command |
|---|---|
| Query all tracked projects & ratings | [`data/catalog.json`](./data/catalog.json) (never scrape README tables) |
| Frontmatter JSON Schema | [`data/schema/audit-frontmatter.schema.json`](./data/schema/audit-frontmatter.schema.json) |
| Canonical English audit template | [`templates/audit-snapshot.md`](./templates/audit-snapshot.md) |
| Post-fix re-audit snapshot template | [`templates/reaudit-snapshot.md`](./templates/reaudit-snapshot.md) |
| Maintainer official response template | [`templates/maintainer-response.md`](./templates/maintainer-response.md) |
| Upstream engineering Issue template | [`templates/upstream-issue.md`](./templates/upstream-issue.md) |
| Scoring rubric & evidence tiers | [`METHODOLOGY.md`](./METHODOLOGY.md) |
| Disclosure & right-of-reply policy | [`DISCLOSURE.md`](./DISCLOSURE.md) |
| Scaffold a new snapshot file | `python3 tools/new_audit.py --repo owner/name --sha <40-hex> --category <cat>` |
| Regenerate indexes & README tables | `python3 tools/build_index.py` |
| Run mechanical validator (JSON output) | `python3 tools/validate.py --verify-upstream --format json` |
| Run tooling unit tests | `python3 -m unittest discover -s tests -v` |

---

## 2. Five Supported Agent Workflows

### Workflow A: Query Existing Audits (Read-Only)
1. Read [`data/catalog.json`](./data/catalog.json) for structured project status, ratings (`honesty`, `risk`, `verdict`), `upstream_issues`, and report paths.
2. Open the linked canonical English report (`audits/<forge>/<owner>/<repo>/YYYY-MM-DD-<8char-sha>.md`), or its `.zh-CN.md` sibling if present.

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
When an upstream project ships a fix:
1. **Never overwrite** the historical SHA snapshot.
2. Scaffold a new snapshot for the newer commit SHA:
   ```bash
   python3 tools/new_audit.py --repo owner/name --sha <new-40-char-sha> --reaudit
   ```
3. Fill [`templates/reaudit-snapshot.md`](./templates/reaudit-snapshot.md), update `upstream_issues` states (`fixed|...`), run `build_index.py` (which marks the older snapshot `superseded` automatically in `data/catalog.json` and the project README), and pass `validate.py --verify-upstream`.

### Workflow D: Submit an Official Maintainer Response (PR)
Upstream maintainers (or their agents) may add or update `audits/<forge>/<owner>/<repo>/responses.md` using [`templates/maintainer-response.md`](./templates/maintainer-response.md), run `python3 tools/build_index.py` to surface the link on the project page, and open a PR.

### Workflow E: Open a Structured Issue
Do not open unstructured issues. Use the exact schemas in `.github/ISSUE_TEMPLATE/`:
- **Nominate a project:** [`request-audit.yml`](./.github/ISSUE_TEMPLATE/request-audit.yml) (`[Audit Request] owner/repo`)
- **Notify of an upstream fix:** [`report-fix-reaudit.yml`](./.github/ISSUE_TEMPLATE/report-fix-reaudit.yml) (`[Fix Re-Audit] owner/repo`)
- **Challenge a finding with code counter-evidence:** [`dispute-finding.yml`](./.github/ISSUE_TEMPLATE/dispute-finding.yml) (`[Dispute] owner/repo: F#`)

---

## 3. Hard Guardrails (Enforced Mechanically by CI)

1. **Public evidence only.** Never commit private raw dumps, unvetted drafts, model transcripts, social-media copy, or local machine paths (`/home/...`, `/Users/...`, `/tmp/...`, `C:\Users\...`).
2. **Zero intent accusations.** State observable code behavior at a pinned SHA (`reads`, `hashes`, `sends`, `retains`, `omits`, `contradicts`). CI rejects banned intent words (English and Chinese) outside verbatim quotes.
3. **Zero hallucinated citations.** Every `file:line`, permalink SHA, companion repo citation, and adjacent code identifier is checked against the real upstream repository at the pinned commit.
4. **Never hand-edit generated files.** `data/catalog.json`, `audits/<forge>/<owner>/<repo>/README.md`, and the `<!-- CATALOG:BEGIN -->`…`<!-- CATALOG:END -->` blocks in `README.md` / `README.zh-CN.md` are owned exclusively by `python3 tools/build_index.py`.
5. **Immutable history & tight PR scope.** Audit PRs must only add/update their own snapshot(s), optional translation, `responses.md`, and regenerated index files. Do not modify `tools/`, `.github/`, `data/schema/`, or unrelated historical snapshots inside an audit PR.
6. **Zero unfilled placeholders.** CI rejects template placeholders (`<40-char-sha>`, `TODO:`, `TBD`).
7. **Upstream Issue etiquette.** When filing corresponding bug/doc-mismatch issues on upstream repos, use [`templates/upstream-issue.md`](./templates/upstream-issue.md): de-duplicate first, file only actionable `PUBLIC_NOW` defects, omit internal `H/R` rating jargon, and link back once filed via frontmatter `upstream_issues`.
8. **Clean human prose (no em-dashes).** Avoid em-dashes (`—` / `——`) in authored report prose, READMEs, and upstream issues; use standard commas, colons, semicolons, or parentheses instead (preserve upstream quotes verbatim only inside direct quotations or code blocks).
