<div align="center">

# trust-no-readme

**Don't trust the README. Read the code.**

An independent, commit-pinned public archive auditing mainstream open-source projects against their own promises, with clickable source permalinks, isolated sandbox captures, upstream issue tracking, and CI-verified citations.

[English](./README.md) · [中文](./README.zh-CN.md) · [Methodology](./METHODOLOGY.md) · [Disclosure & Upstream Issues](./DISCLOSURE.md) · [Contributing](./CONTRIBUTING.md) · [For AI Agents](./AGENTS.md)

</div>

---

## Why This Exists

Open-source software earns trust through transparency, yet almost nobody reads tens of thousands of lines of code before running `pip install` or `npm i`. Too many high-star projects treat that gap as cover: promising "zero telemetry", "never sends code or secrets", or "respects opt-out" in the README, while shipping code that uploads raw scripts, bypasses opt-out switches, silently removes privacy docs, or rejects community privacy fixes.

AI agents have made full-repository source inspection and isolated sandbox verification cheap. Nothing in an open-source repo or its Git history is truly hidden anymore. `trust-no-readme` is a long-term, independent third-party gathering place to:

- **Audit mainstream open-source projects** at exact 40-character Git commit SHAs and compare documented promises against real code and sandbox traffic.
- **Publish verifiable evidence publicly** (canonical English reports paired with synced Chinese editions) while distinguishing deliberate product decisions from inadvertent engineering regressions.
- **File actionable bug reports upstream** alongside publication and track fixes through immutable re-audit snapshots.
- **Welcome community & AI agent PRs**: anyone (human or agent) can contribute verified audits using our templates and automated CI checks to help clean up the open-source ecosystem.

### Ground Rules

1. **Verifiable facts at a pinned SHA.** Every finding anchors to clickable `file:line` permalinks, verbatim code excerpts, dated Git/PR history, and zero-credential sandbox captures where applicable. When maintainers ship a fix, we add a new immutable snapshot and mark the old one `superseded`.
2. **Contract Alignment (`H`) and Workstation Risk (`R`) stay separate.** Breaking an explicit promise (`H3`/`H4`) is evaluated independently from high default workstation permissions (`R3`/`R4`).
3. **Mechanically verified in CI + responsible disclosure.** [`tools/validate.py`](./tools/validate.py) inertly fetches every audited SHA (and declared companion repos) read-only to verify cited files, line ranges, verbatim excerpts, and code identifiers. Unpatched exploitable security vulnerabilities follow private 90-day disclosure first ([`DISCLOSURE.md`](./DISCLOSURE.md)), never dropped as 0-days.

---

## Tracked Projects

<!-- CATALOG:BEGIN -->
Currently tracking **2** project(s) across **2** SHA-pinned snapshot(s):

| Project | Revision | Rating & Verdict | Key Finding | Upstream Issues | Report |
|---|---|---|---|---|---|
| [`CherryHQ/cherry-studio`](./audits/github/CherryHQ/cherry-studio/README.md) | `v2.2.3`<br>`62867d8f` | `H3 / R3` · `E3+D`<br>**`Isolate`** | CN-only accountId + US PostHog profiling vs. anonymous policy, 431-day zero-click PlantUML egress, opt-out Client-Id/IP & MCP auto-trust. | [#25580](https://github.com/CherryHQ/cherry-studio/issues/25580) (open)<br>[#25579](https://github.com/CherryHQ/cherry-studio/issues/25579) (open)<br>[#25581](https://github.com/CherryHQ/cherry-studio/issues/25581) (open) | [EN](./audits/github/CherryHQ/cherry-studio/2026-11-27-62867d8f.md) · [中文](./audits/github/CherryHQ/cherry-studio/2026-11-27-62867d8f.zh-CN.md) |
| [`browser-use/browser-use`](./audits/github/browser-use/browser-use/README.md) | `1.0.8`<br>`94784c1f` | `H4 / R3` · `E3+D`<br>**`Isolate`** | 141-day raw script/password telemetry (PR #733 rejected), 46s README wipe, coding-agent opt-out bypass & silent --help tracking. | [#6541](https://github.com/browser-use/browser-use/issues/6541) (open)<br>[#6542](https://github.com/browser-use/browser-use/issues/6542) (open)<br>[#6467](https://github.com/browser-use/browser-use/issues/6467) (existing) | [EN](./audits/github/browser-use/browser-use/2026-11-26-94784c1f.md) · [中文](./audits/github/browser-use/browser-use/2026-11-26-94784c1f.zh-CN.md) |
<!-- CATALOG:END -->

> **Quick legend** (full rubric in [`METHODOLOGY.md`](./METHODOLOGY.md)):
> - **Contract Alignment (`H0`–`H4`)**: `H0` consistent · `H1` vague/stale · `H2` material omission · `H3` explicit promise contradicted by default code · `H4` `H3` + dated maintainer awareness trail.
> - **Workstation Risk (`R0`–`R4`)**: default blast radius on a dev machine holding AI/cloud credentials (`R3` = default-on high host/credential/egress risk; `R4` = critical unauthenticated RCE/covert exfiltration).
> - **Evidence**: `E3` = full static source trace · `E3+D` = `E3` + zero-credential isolated sandbox reproduction.

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
