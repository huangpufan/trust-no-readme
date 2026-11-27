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
4. **Transparent longitudinal tracking (`tracking.json`):** Filed upstream issue URLs, maintainer replies, linked PRs, verified fix commits, release status, and declines (`wontfix` / `closed_unfixed`, always backed by exact evidence URLs) are maintained in each project's mutable `audits/<forge>/<owner>/<repo>/tracking.json` ledger rather than mutating frozen historical snapshots.
5. **Verified fixes vs. preserved history:** Upstream closures enter `fix_claimed` first and only promote to `verified_main` (after code diff inspection) or `verified_shipped` (after release verification + new immutable re-audit snapshot). Verified fixes are prominently credited in the root **Supervision Impact & Outcomes** ledger, while historical peak ratings and `superseded` snapshots stay permanently archived.
6. **Private CVD records in `tracking.json`:** Embargoed disclosures may appear only in `private_disclosures` with zero technical details, URLs, or `file:line` paths (mechanically enforced by `tools/validate.py`) until patched or expired.

---

## 4. Maintainer Right of Reply, Corrections & Fix Re-Audits

- **Shipped a fix?** Open a [Fix & Re-Audit Request](https://github.com/huangpufan/trust-no-readme/issues/new?template=report-fix-reaudit.yml) (or a Re-Audit PR) with the fixing commit SHA. For fixes merged to `main` ahead of a release, we inspect the commit diff and mark the item `verified_main` in `tracking.json` (while noting the current shipped release remains affected). Once a release ships, we publish a new immutable re-audit snapshot, mark the older snapshot `superseded` while preserving its historical peak rating, and promote the item to `verified_shipped`.
- **Official maintainer response:** Maintainers may submit a concise factual statement via Issue or PR to `audits/<forge>/<owner>/<repo>/responses.md` ([template](./templates/maintainer-response.md)). It is linked directly on the project card.
- **Factual error or misreading:** Open a [Dispute / Correction](https://github.com/huangpufan/trust-no-readme/issues/new?template=dispute-finding.yml) citing exact `file:line` counter-evidence at the audited SHA. Confirmed corrections are applied transparently with a dated changelog note and credit.
