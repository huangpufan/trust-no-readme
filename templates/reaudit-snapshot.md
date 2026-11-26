---
repo: <owner/repo>
forge: github
sha: <new-40-char-sha>
release: "<new-version-or-tag>"
audited_at: <YYYY-MM-DD>
category: <category>
honesty: H0
h3_scope: none
risk: R1
verdict: Adopt
evidence_max: E3+D
coverage: lead-driven
verification: static+release-diff+dynamic
git_depth: full
disclosure: public
status: active
language: en
supersedes_sha: <previous-40-char-sha>
headline_en: "<One crisp sentence <= 140 chars summarizing what was fixed and what remains>"
headline_zh: "<不超过 90 字的一句话中文复审摘要：修了什么、还剩什么>"
companion_repos: []
upstream_issues: []
tags:
  - re-audit
---

# <owner/repo>: Fix Re-Audit (`<new-version-or-tag>` / `<new-8char-sha>`)

> **New Snapshot:** [`<new-8char-sha>`](https://github.com/<owner/repo>/tree/<new-40-char-sha>) · **Supersedes:** [`<prev-8char-sha>`](./<previous-snapshot-filename>.md) · **Audited:** `<YYYY-MM-DD>` · **Disclosure:** `public`
>
> **Scope & Non-Intent Notice:** This immutable re-audit verifies upstream changes between `<prev-8char-sha>` and `<new-8char-sha>`. The previous snapshot remains archived as `superseded`.

---

## Executive Verdict

| Dimension | Previous (`<prev-8char-sha>`) | Current (`<new-8char-sha>`) |
|---|---|---|
| **Adoption Verdict** | `<old>` | **`<new>`** |
| **Contract Alignment (`H`)** | `<old>` | **`<new>`** |
| **Workstation Risk (`R`)** | `<old>` | **`<new>`** |

| ID | Finding / Remediation Status | H / R | Evidence | Default? | Upstream Issue / PR |
|---|---|---|---|---|---|
| F1 | [<Remaining finding or verified remediation summary>](#f1-title) | `H? / R?` | `E3+D` | Yes/No | <`#123` (fixed)> |

---

## 1. Snapshot & Claim Ledger

| Previous Finding | Fix Commit / PR | Verification Result at `<new-8char-sha>` |
|---|---|---|
| Previous F1 | [`<fix-sha>`](https://github.com/<owner/repo>/commit/<fix-full-sha>) | **Fixed** / **Partially Fixed** / **Still Present** |

---

## 2. Detailed Findings

### F1: <Title>

**Rating:** `H? / R?` · **Evidence:** `E3+D` · **Default:** No · **Disclosure:** `public`

- **Change verified:** [`path/to/file.py:10-20`](https://github.com/<owner/repo>/blob/<new-40-char-sha>/path/to/file.py#L10-L20) now guards `symbol_name`.
- **Remaining caveats (if any):** <Exact residual scope or `None`.>

---

## 3. Trajectory

| Date | Anchor | Event | Relevance |
|---|---|---|---|
| `YYYY-MM-DD` | [`<fix-sha>`](https://github.com/<owner/repo>/commit/<fix-full-sha>) | Upstream fix merged | Resolves previous F1 |

---

## 4. Cleared Surfaces

| Surface Checked | Verified Source (`file:line`) | Outcome & Scope |
|---|---|---|
| <Remediated surface> | [`path/to/file.py:10-20`](https://github.com/<owner/repo>/blob/<new-40-char-sha>/path/to/file.py#L10-L20) | Verified fixed at `<new-8char-sha>` |

---

## 5. Coverage, Limits & Gaps

- **Re-audit scope:** Focused diff and regression verification between `<prev-8char-sha>` and `<new-8char-sha>`.
- **Out-of-snapshot (`E0`) gaps:** <Any remaining external gaps.>
