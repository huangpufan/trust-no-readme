<div align="center">

# trust-no-readme

**Don't trust the README. Read the code.**

An independent, commit-pinned public archive auditing mainstream open-source projects against their own promises, with clickable source permalinks, isolated sandbox captures, upstream issue tracking, and CI-verified citations.

[English](./README.md) · [中文](./README.zh-CN.md) · [Impact & Outcomes](#supervision-impact--outcomes) · [Tracked Projects](#tracked-projects) · [Methodology](./METHODOLOGY.md) · [Disclosure & Upstream Issues](./DISCLOSURE.md) · [Contributing](./CONTRIBUTING.md) · [For AI Agents](./AGENTS.md)

</div>

---

## Supervision Impact & Outcomes

<!-- IMPACT:BEGIN -->
**2** project(s) audited (**2** pinned snapshots) · **5** upstream issue(s) filed (+1 pre-existing tracked, 1 private CVD) · **1** verified fix(es) (`1` merged on `main`, `0` shipped) · **1** maintainer-accepted · **0** declined or closed unfixed

### Verified Fixes Driven by Independent Audits

| Project | Issue & Finding Fixed | Latency -> Turnaround | Fix Proof & Release Status | Verification |
|---|---|---|---|---|
| [`CherryHQ/cherry-studio`](./audits/github/CherryHQ/cherry-studio/README.md) | [#25579](https://github.com/CherryHQ/cherry-studio/issues/25579) · `F2`<br>Zero-click PlantUML diagram egress to www.plantuml.com | `431d` latent -> **7.5h** to merge | Commit [`f59df558`](https://github.com/CherryHQ/cherry-studio/commit/f59df558c95c7e571c45cd39d604250abce2ac6d) · PR [#25582](https://github.com/CherryHQ/cherry-studio/pull/25582)<br>**Merged on `main` (unreleased; `v2.2.3` still affected)** | Verified `2026-11-27` |

> **History Preservation Rule:** A verified fix credits responsive upstream engineering and independent oversight; it never erases or overwrites prior audit snapshots. When a re-audit updates a project's current rating, its historical peak and superseded snapshot remain permanently archived and linked alongside it. Every claimed fix commit and ancestry relationship is mechanically verified against upstream Git in CI.
<!-- IMPACT:END -->

---

## Tracked Projects

<!-- CATALOG:BEGIN -->
Currently tracking **2** project(s) across **2** SHA-pinned snapshot(s):

| Project | Latest Rev | Current vs. Peak | Key Finding | Upstream Response & Issues | Report |
|---|---|---|---|---|---|
| [`CherryHQ/cherry-studio`](./audits/github/CherryHQ/cherry-studio/README.md) | `v2.2.3`<br>`62867d8f` | `H3 / R3` · `E3+D`<br>**`Isolate`** | CN-only accountId + US PostHog profiling vs. anonymous policy, 431-day zero-click PlantUML egress, opt-out Client-Id/IP & MCP auto-trust. | **Active Remediation**<br>✓ [#25579](https://github.com/CherryHQ/cherry-studio/issues/25579) (verified: main)<br>◐ [#25581](https://github.com/CherryHQ/cherry-studio/issues/25581) (accepted)<br>○ [#25580](https://github.com/CherryHQ/cherry-studio/issues/25580) (triaged) | [EN](./audits/github/CherryHQ/cherry-studio/2026-11-27-62867d8f.md) · [中文](./audits/github/CherryHQ/cherry-studio/2026-11-27-62867d8f.zh-CN.md) |
| [`browser-use/browser-use`](./audits/github/browser-use/browser-use/README.md) | `1.0.8`<br>`94784c1f` | `H4 / R3` · `E3+D`<br>**`Isolate`** | 141-day raw script/password telemetry (PR #733 rejected), 46s README wipe, coding-agent opt-out bypass & silent --help tracking. | **Community PRs Only**<br>○ [#6541](https://github.com/browser-use/browser-use/issues/6541) (ext PR #6545)<br>○ [#6542](https://github.com/browser-use/browser-use/issues/6542) (ext PR #6549)<br>· [#6467](https://github.com/browser-use/browser-use/issues/6467) (existing · ext PR #6470) | [EN](./audits/github/browser-use/browser-use/2026-11-26-94784c1f.md) · [中文](./audits/github/browser-use/browser-use/2026-11-26-94784c1f.zh-CN.md) |
<!-- CATALOG:END -->

> **Quick legend** (full rubric in [`METHODOLOGY.md`](./METHODOLOGY.md)):
> - **Contract Alignment (`H0`–`H4`)**: `H0` consistent · `H1` vague/stale · `H2` material omission · `H3` explicit promise contradicted by default code · `H4` `H3` + dated maintainer awareness trail.
> - **Workstation Risk (`R0`–`R4`)**: default blast radius on a dev machine holding AI/cloud credentials (`R3` = default-on high host/credential/egress risk; `R4` = critical unauthenticated RCE/covert exfiltration).
> - **Upstream Response & Remediation**: orthogonal per-project tracker ([`tracking.json`](./data/schema/tracking.schema.json)) separating verified fixes (`main` vs. shipped release), maintainer confirmations, community PRs awaiting review, silent periods, and declines (`wontfix` / `closed_unfixed`).
> - **Evidence**: `E3` = full static source trace · `E3+D` = `E3` + zero-credential isolated sandbox reproduction.

---

## Why This Exists

Open-source software earns trust through transparency, yet almost nobody reads tens of thousands of lines of code before running `pip install` or `npm i`. Too many high-star projects treat that gap as cover: promising "zero telemetry", "never sends code or secrets", or "respects opt-out" in the README, while shipping code that uploads raw scripts, bypasses opt-out switches, silently removes privacy docs, or rejects community privacy fixes.

AI agents have made full-repository source inspection and isolated sandbox verification cheap. Nothing in an open-source repo or its Git history is truly hidden anymore. `trust-no-readme` is a long-term, independent third-party gathering place to:

- **Audit mainstream open-source projects** at exact 40-character Git commit SHAs and compare documented promises against real code and sandbox traffic.
- **Publish verifiable evidence publicly** (canonical English reports paired with synced Chinese editions) while distinguishing deliberate product decisions from inadvertent engineering regressions.
- **File actionable bug reports upstream and track outcomes transparently**: record maintainer response speed, verified fixes (distinguishing `main` merges from shipped releases), unresolved silence, and declines without ever overwriting historical audit snapshots.
- **Welcome community & AI agent PRs**: anyone (human or agent) can contribute verified audits or fix verifications using our templates and automated CI checks to help clean up the open-source ecosystem.

### Ground Rules

1. **Verifiable facts at a pinned SHA.** Every finding anchors to clickable `file:line` permalinks, verbatim code excerpts, dated Git/PR history, and zero-credential sandbox captures where applicable. When maintainers ship a fix, we add a new immutable snapshot, update current ratings, and permanently preserve the historical peak and `superseded` snapshot.
2. **Contract Alignment (`H`), Workstation Risk (`R`), and Upstream Remediation stay separate.** Breaking an explicit promise (`H3`/`H4`), high default workstation permissions (`R3`/`R4`), and how maintainers respond to filed issues are evaluated independently through observable dated facts.
3. **Mechanically verified in CI + responsible disclosure.** [`tools/validate.py`](./tools/validate.py) inertly fetches every audited SHA, declared companion repo SHA, and claimed `fix_commit` read-only to verify citations, verbatim excerpts, and real upstream commits. Unpatched exploitable security vulnerabilities follow private 90-day disclosure first ([`DISCLOSURE.md`](./DISCLOSURE.md)), never dropped as 0-days.

---

## Open Collaboration: Humans & AI Agents Welcome

- **For Readers & Security Reviewers:** Browse the table above (canonical English + synced Chinese) or consume [`data/catalog.json`](./data/catalog.json) directly.
- **For Maintainers:**
  - Shipped a fix? Open a [Fix & Re-Audit Request](https://github.com/huangpufan/trust-no-readme/issues/new?template=report-fix-reaudit.yml) (or Re-Audit PR) with the fix SHA.
  - Spotted an error? Open a [Dispute / Correction](https://github.com/huangpufan/trust-no-readme/issues/new?template=dispute-finding.yml) with `file:line` counter-evidence.
  - Want an official statement linked on your project card? Submit `responses.md` via PR ([template](./templates/maintainer-response.md)).
- **For Community Contributors & AI Agents:**
  - This repo is **agent-friendly and self-discoverable**: point your agent at [`AGENTS.md`](./AGENTS.md) or [`llms.txt`](./llms.txt) for exact schemas, templates (`templates/`), scaffolder (`tools/new_audit.py`), and JSON-output validation (`tools/validate.py --verify-upstream --format json`).
  - Nominate a project via [Audit Request](https://github.com/huangpufan/trust-no-readme/issues/new?template=request-audit.yml) or submit a verified audit PR via [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## License

- Audit reports, methodology, and documentation (`audits/**`, `*.md`, `templates/**`): **CC-BY-4.0** (attribution + pinned SHA link required).
- Tooling, schemas, and workflows (`tools/**`, `tests/**`, `data/schema/**`, `.github/**`): **MIT**.
