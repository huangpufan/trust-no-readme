---
repo: <owner/repo>
forge: github
sha: <40-char-sha>
release: "<version-or-tag>"
audited_at: <YYYY-MM-DD>
category: <category>
honesty: H0
h3_scope: none
risk: R0
verdict: Adopt
evidence_max: E3
coverage: full-exhaustive
verification: static+release-diff
git_depth: full
disclosure: public
status: active
language: en
headline_en: "<One crisp sentence <= 140 chars summarizing the core verified finding>"
headline_zh: "<不超过 90 字的一句话中文核心发现摘要>"
companion_repos: []
upstream_issues: []
tags:
  - <kebab-case-tag>
---

# <owner/repo>: Code vs. Claims Audit (`<version-or-tag>` / `<8char-sha>`)

> **Snapshot:** [`<8char-sha>`](https://github.com/<owner/repo>/tree/<40-char-sha>) · **Shipped Release:** `<version-or-tag>` · **Audited:** `<YYYY-MM-DD>` · **Threat Profile:** Developer workstation holding AI/cloud credentials · **Disclosure:** `public`
>
> **Scope & Non-Intent Notice:** Every finding below describes verifiable code, documentation, and isolated runtime behavior at this pinned commit only. It makes no claim about maintainer intent. When maintainers ship a fix, a new immutable snapshot is published and this snapshot is marked `superseded`.

---

## Executive Verdict

| Dimension | Assessment |
|---|---|
| **Adoption Verdict** | **`<Adopt / Adopt with Config / Isolate / Avoid>`**: <1-sentence practical recommendation> |
| **Contract Alignment (`H`)** | **`<H0–H4>`**: <1-sentence summary of claim-vs-code alignment> |
| **Workstation Risk (`R`)** | **`<R0–R4>`**: <1-sentence summary of default host/credential/egress boundary> |
| **What Checked Out Clean** | <1-sentence fair summary of verified positive engineering controls> |

| ID | Finding | H / R | Evidence | Default? | Upstream Issue |
|---|---|---|---|---|---|
| F1 | [<Short plain-English title>](#f1-short-plain-english-title) | `H? / R?` | `E3+D` | Yes | <`#123` or `Pending`> |

*(Keep the top table focused on the 2–5 highest-signal findings. Put secondary or already-documented boundary notes in a compact subsection or `<details>` block under Section 2.)*

---

## 1. Snapshot & Claim Ledger

### Release Parity

| Artifact | Revision / SHA | Notes |
|---|---|---|
| Audited commit | [`<8char-sha>`](https://github.com/<owner/repo>/tree/<40-char-sha>) | Full Git history inspected |
| Shipped release (`<version-or-tag>`) | `<tag-sha>` | <Byte-parity or diff summary> |

### Explicit Claims Checked

| ID | Claim Source | Verbatim Promise | Default Code Reality | Status |
|---|---|---|---|---|
| C1 | [`README.md:10`](https://github.com/<owner/repo>/blob/<40-char-sha>/README.md#L10) | "<exact verbatim quote>" | <1-sentence verified reality> | **Contradicted (F1)** |

---

## 2. Detailed Findings

### F1: <Short Plain-English Title>

**Rating:** `H? / R?` · **Evidence:** `E3+D` · **Default:** Yes · **Disclosure:** `public` · **Claim:** C1

- **The promise:** [`README.md:10`](https://github.com/<owner/repo>/blob/<40-char-sha>/README.md#L10) states `"<verbatim quote>"`.
- **What the code actually does:** [`path/to/file.py:42-55`](https://github.com/<owner/repo>/blob/<40-char-sha>/path/to/file.py#L42-L55) calls `relevant_symbol()` without checking the opt-out gate.

**Relevant source ([`path/to/file.py:42-55`](https://github.com/<owner/repo>/blob/<40-char-sha>/path/to/file.py#L42-L55)):**

```python
# Paste 5–15 verbatim lines from the pinned SHA here
```

- **Verification & exact scope:**
  - **Observed:** <Concise static trace or zero-credential sandbox reproduction result / payload excerpt.>
  - **What is NOT affected:** <Explicit benign scope boundaries so maintainers and users have exact precision.>
- **Timeline (if applicable):**
  - `YYYY-MM-DD` ([`<sha>`](https://github.com/<owner/repo>/commit/<full-sha>)): <Dated factual event.>
- **Mitigation:** <Exact copy-pasteable config, env var, or isolation step.>

---

## 3. Trajectory

| Date | Anchor | Event | Relevance |
|---|---|---|---|
| `YYYY-MM-DD` | [`<sha>`](https://github.com/<owner/repo>/commit/<full-sha>) | <Factual commit / PR / advisory> | <Context> |

---

## 4. Cleared Surfaces

| Surface Checked | Verified Source (`file:line`) | Outcome & Scope |
|---|---|---|
| <Checked area> | [`path/file.py:1-20`](https://github.com/<owner/repo>/blob/<40-char-sha>/path/file.py#L1-L20) | Clean: <exact scope> |

---

## 5. Coverage, Limits & Gaps

- **Code & release scope:** <What files/packages/releases were inspected and what was excluded.>
- **Dynamic sandbox scope:** <What was reproduced in an isolated zero-credential container vs. static-only.>
- **Out-of-snapshot (`E0`) gaps:** <Live website copy, remote server behavior, signed binaries not inspected, etc.>
