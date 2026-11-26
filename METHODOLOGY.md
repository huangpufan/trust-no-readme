# Methodology

`trust-no-readme` audits open-source projects against one verifiable standard: **compare what a repository explicitly promises against what its default code actually executes at a pinned Git commit SHA, and document exact proof, scope, and limits.**

---

## 1. Evidence Tiers

| Tier | Meaning | Used in Rating? |
|---|---|---|
| **`E3+D`** | Complete static source trace (`default entry → gate/transform → sink`) at pinned SHA **plus** independent reproduction inside an isolated, zero-credential sandbox (e.g., decoy credential reads, intercepted loopback HTTPS payload, post-delete storage check). | Yes (highest confidence) |
| **`E3`** | Complete static source trace at pinned SHA with verified `file:line` and verbatim source excerpts at every hop. | Yes |
| **`E2`** | Sink confirmed in shipped code, but reachability requires non-default flags or inferred dynamic dispatch. | Caveat only (never `H3` / `H4`) |
| **`E1`** | Unverified lead or pattern hit only. | Never |
| **`E0`** | Out-of-snapshot surface (live website copy, remote server retention, uninspected binary installer). | Listed explicitly under Coverage & Gaps |

---

## 2. Two Strictly Independent Axes

Honesty and risk are **never** averaged into one score. A high-privilege tool that honestly documents its host permissions scores `H0` / `H1` with `R3`.

### Axis A: Contract Alignment (`H0–H4`)
Does default code obey explicit README, security-doc, license, or UI promises? Accurate disclosure resolves `H`.

| Level | Plain Meaning |
|---|---|
| **`H0`** | **Consistent.** Examined promises hold at default config (or behavior stays within the tool category baseline). |
| **`H1`** | **Vague / Stale.** Ambiguous, outdated, or edition-blurred wording without direct contradiction. |
| **`H2`** | **Material Omission.** Undisclosed default behavior touching credentials, user content, linkable IDs, or persistent local files. |
| **`H3`** | **Direct Contradiction.** An explicit promise is proven false (`E3` / `E3+D`) at default config. Scoped as `primary` (everyday default path) or `edge` (migration / mid-process switch / secondary path). |
| **`H4`** | **Contradiction + Maintainer Awareness Timeline.** `H3` plus dated, **maintainer-authored** commits, PR reviews, or tracker actions showing prior awareness of that exact gap. Dated facts only; never motive. |

### Axis B: Boundary & Security Risk (`R0–R4`)
Evaluated on the default threat profile: **a developer workstation holding AI/cloud credentials and private source code**. Disclosure never lowers `R`.

| Level | Plain Meaning |
|---|---|
| **`R0`** | **Minimal.** No unsolicited egress, cross-boundary credential reads, or unguarded host execution. |
| **`R1`** | **Low.** Bounded anonymous metadata with working opt-out, or opt-in / local-admin-only risk. |
| **`R2`** | **Moderate.** Default-on linkable IDs, un-killable low-sensitivity startup fetches, or moderate local retention exposure with a clean mitigation. |
| **`R3`** | **High.** Default-on cross-boundary credential reads, unauthenticated localhost/LAN listeners without Origin/Host checks, default-auto unsandboxed host execution, or unverified remote code/plugin control planes. |
| **`R4`** | **Critical.** Default-on unauthenticated remote RCE, verified covert exfiltration of secrets/content to an unconsented third party, or arbitrary code execution on repo open/install. |

### Adoption Verdict (Dev Workstation Profile)

| Verdict | Mechanical Rule |
|---|---|
| **`Adopt`** | `H ≤ 1` and `R ≤ 1` |
| **`Adopt with Config`** | `H = 2` or `R = 2`, with a verified complete mitigation |
| **`Isolate`** | `R = 3` (even if `H0`), or `H ≥ 3` neutralized by container / OS-user / egress isolation |
| **`Avoid`** | `R = 4`, or `H ≥ 3` with no effective mitigation for the target requirement |

---

## 3. Report Structure & Mechanical Verification

Every canonical snapshot (`audits/<forge>/<owner>/<repo>/YYYY-MM-DD-<8char-sha>.md`) is written in English (with optional `.zh-CN.md` translation) and must include:

1. **Executive Verdict** (10-second verdict card + top findings table with upstream issue status).
2. **Snapshot & Claim Ledger** (audited SHA vs. shipped release parity + numbered claims with clickable GitHub permalinks).
3. **Detailed Findings** (Promise vs. Code, 5–15 line verbatim source excerpts, dynamic/static verification, explicit non-affected scope boundaries, timeline, and concrete mitigation).
4. **Trajectory** (dated commits, PRs, and advisories separated from current-SHA ratings).
5. **Cleared Surfaces** (what was inspected and verified clean, preventing one-sided reporting).
6. **Coverage, Limits & Gaps** (explicit scope boundaries and out-of-snapshot `E0` gaps).

CI (`tools/validate.py --verify-upstream`) inertly fetches every audited SHA and declared `companion_repos` SHA read-only and mechanically verifies file existence, line bounds, permalink SHAs, and cited code identifiers.
