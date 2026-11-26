# Upstream Issue Filing Template (Constructive Engineering Bug / Doc-Mismatch)

> **Purpose:** Use this template when filing a corresponding public Issue on an audited repository alongside publishing a `PUBLIC_NOW` audit snapshot.
>
> **Rules:**
> 1. **Never file `EMBARGO_PRIVATE` (unpatched exploitable vulnerabilities) as a public Issue.** Use the target project's `SECURITY.md` or GitHub Private Vulnerability Reporting.
> 2. **De-duplicate first:** Check open Issues and PRs in the target repo before filing.
> 3. **One concrete defect per Issue:** Keep it surgical, reproducible, and easy for a maintainer to patch in one PR.
> 4. **Zero rating jargon or confrontation:** Do **not** paste `H0–H4`, `R0–R4`, `Isolate`, or `Avoid` scores into an upstream Issue. Present only pinned SHA, exact `file:line`, minimal repro, and a concrete fix suggestion, with a single neutral link to the full pinned-SHA analysis at the end.
> 5. **Respect the target repo's own `.github/ISSUE_TEMPLATE/`** when present.

---

## Title Format

```text
[Bug]: <Specific component/function> <observable mismatch> when <condition>
```

*(Adapt prefix to match target repo convention, e.g., `[Bug]: ...` or `Bug: ...`.)*

---

## Body Template

```markdown
### Summary

At commit `<short-sha>` (also present in `<release-tag>`), `<component/function>` `<observable behavior>`, which contradicts `<documented/UI behavior>` at `<doc-or-ui file:line>`.

### Relevant Code

- Documented / UI guarantee: [`<doc_path>:<lines>`](https://github.com/<owner>/<repo>/blob/<full-sha>/<doc_path>#L<start>-L<end>)
- Code path: [`<code_path>:<lines>`](https://github.com/<owner>/<repo>/blob/<full-sha>/<code_path>#L<start>-L<end>)

```<lang>
# 5–12 exact lines showing the gap
```

### Minimal Reproduction

1. `<Step 1>`
2. `<Step 2>`
3. **Expected:** `<what doc/UI guarantees>`
4. **Actual:** `<observed output / file / request>`

### Suggested Fix

<1–3 concrete sentences describing the minimal surgical change, e.g., guard check or missing parameter.>

---

*Context: Found during an automated + isolated-sandbox code review of `<owner/repo>` at `<short-sha>`. Full read-only technical notes and reproduction details are documented here: `<trust-no-readme-report-url>`. Happy to verify and update our snapshot once a fix lands.*
```
