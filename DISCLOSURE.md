# Responsible Disclosure, Upstream Issues & Right of Reply

`trust-no-readme` exists to help developers verify what open-source software actually does, and to help maintainers fix real claim-vs-code gaps cleanly. It never drops unpatched zero-days or speculates about author intent.

---

## 1. Non-Intent & Fixed-SHA Scoping

- Every audit describes **verifiable code, documentation, and isolated runtime behavior at an exact 40-character Git commit SHA**.
- Reports avoid intent accusations (`spyware`, `malware`, `backdoor`, `steal`, `exfiltrate`, `fraud`, `scam`, `malicious`, `deceptive`, and Chinese equivalents) outside verbatim quotes, using observable verbs instead (`reads`, `hashes`, `sends`, `retains`, `omits`, `contradicts`).

---

## 2. Coordinated Vulnerability Disclosure (CVD)

Before any finding enters this repository, it passes mandatory disclosure triage:

1. **Public Contract, Privacy & Default-Design Findings (`disclosure: public`)**
   - Explicit README, security-doc, or UI contradictions (`H3` / `H4`), broken opt-out switches, undisclosed telemetry/egress, incomplete deletion, and documented-vs-default permission boundaries.
   - Because these describe observable design/contract behavior rather than weaponizable exploit chains, they are published once verified against source and (where applicable) isolated dynamic reproduction.

2. **Unpatched Exploitable Security Vulnerabilities (`disclosure: embargoed` / `partially-redacted`)**
   - Unpatched auth bypasses, secret/credential extraction endpoints, RCE chains, signature-verification bypasses, SSRF/DNS-rebinding exploit chains, or cross-project credential injection.
   - **Protocol:** Reported privately via the target project's `SECURITY.md` or GitHub Private Vulnerability Reporting under a standard **90-day coordinated disclosure window** (or earlier upon maintainer fix release).
   - No exploit payloads or weaponized PoCs are ever committed here. If sibling contract findings for the same repo are published first, any embargoed security item is replaced with a neutral `[REDACTED: Coordinated Disclosure Pending]` note until patched or expired.

---

## 3. Upstream Issue Filing Policy

Whenever a `PUBLIC_NOW` audit uncovers concrete, fixable defects (such as a broken opt-out regression, a missing cleanup parameter, or a stale pinned dependency causing unredacted telemetry), we follow a constructive upstream-issue protocol alongside publication:

1. **De-duplicate first:** Search the target repository's open issues and PRs. Never open duplicate spam if an active issue already tracks the exact gap.
2. **One concrete defect per issue:** Split into 1–2 tightly scoped, actionable engineering bug reports or doc-mismatch issues using [`templates/upstream-issue.md`](./templates/upstream-issue.md) (and respecting the target repo's own `.github/ISSUE_TEMPLATE/`).
3. **Engineering tone, zero rating jargon:** Upstream issues present the pinned SHA, exact `file:line`, minimal reproduction, and minimal fix direction, **never** dropping internal `H/R` scores or confrontational commentary into a maintainer's tracker. A single neutral link to the full pinned-SHA analysis is included at the bottom for reference.
4. **Transparent tracking:** Filed upstream issue URLs are recorded in the snapshot's `upstream_issues` frontmatter (`open` / `acknowledged` / `fixed` / `wontfix` / `existing`) and surfaced on the root and project tables so readers see live remediation progress.

---

## 4. Maintainer Right of Reply, Corrections & Fix Re-Audits

- **Shipped a fix?** Open a [Fix & Re-Audit Request](https://github.com/huangpufan/trust-no-readme/issues/new?template=report-fix-reaudit.yml) (or a Re-Audit PR) with the fixing commit SHA. We verify the fix, publish a new immutable snapshot, mark the older snapshot `superseded`, and update `upstream_issues` status to `fixed`.
- **Official maintainer response:** Maintainers may submit a concise factual statement via Issue or PR to `audits/<forge>/<owner>/<repo>/responses.md` ([template](./templates/maintainer-response.md)). It is linked directly on the project card.
- **Factual error or misreading:** Open a [Dispute / Correction](https://github.com/huangpufan/trust-no-readme/issues/new?template=dispute-finding.yml) citing exact `file:line` counter-evidence at the audited SHA. Confirmed corrections are applied transparently with a dated changelog note and credit.
