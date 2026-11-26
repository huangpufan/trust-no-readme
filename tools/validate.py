#!/usr/bin/env python3
"""Mechanical validator for trust-no-readme audit snapshots & translations (stdlib-only).

Checks:
1. Path convention (`audits/<forge>/<owner>/<repo>/YYYY-MM-DD-<shortsha>.md` & optional `.zh-CN.md`)
   and YAML frontmatter schema compliance.
2. Required Total-then-Detail sections, `F1..Fn` table-to-detail parity, zero unfilled placeholders,
   zero banned EN/ZH intent words outside quotes, and zero local machine path leaks.
3. Permalink SHA consistency (no floating `main`/`master` links on audited/companion repos).
4. Optional `--verify-upstream` (or `--local-repo key=/path`): inert read-only fetch of exact pinned
   SHAs (main repo + declared `companion_repos`), verifying:
   - Every cited `file:line` and permalink line range exists and is within file bounds.
   - Verbatim fenced source excerpts right after `Relevant source (...)` headers match upstream lines.
   - Cited code identifiers adjacent to single-citation lines exist in the cited source window
     (while respecting explicit negative assertions such as "omits `sessions_dir`").
5. Optional `--check-pr-scope <base-ref>`: flags audit PRs that unexpectedly touch protected
   tooling/schema files or overwrite unrelated historical snapshots.
6. `--format json`: emits machine-readable diagnostics for AI agents to self-repair in one loop.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from build_index import (
    AUDITS_DIR,
    ROOT,
    SNAPSHOT_RE,
    TRANSLATION_RE,
    forge_base_url,
    parse_companion_repos,
    parse_frontmatter,
)

SCHEMA_PATH = ROOT / "data" / "schema" / "audit-frontmatter.schema.json"

REQUIRED_HEADING_TOKENS = [
    ("executive verdict", "总论"),
    ("snapshot", "claim ledger", "快照", "承诺"),
    ("detailed findings", "分项证据"),
    ("trajectory", "演进轨迹"),
    ("cleared surfaces", "已核查"),
    ("coverage", "覆盖边界", "未验证"),
]

BANNED_WORDS_EN = [
    "spyware", "malware", "backdoor", "steal", "stealing",
    "exfiltrate", "exfiltration", "fraud", "scam", "malicious", "deceptive",
]

BANNED_WORDS_ZH = [
    "间谍软件", "恶意软件", "恶意后门", "偷取", "窃取",
    "骗局", "欺诈", "故意作恶", "刻意作恶",
]

LEAKED_PATH_RE = re.compile(r"(?:/home/[A-Za-z0-9_.-]+/|/Users/[A-Za-z0-9_.-]+/|/tmp/oss-audit|[A-Za-z]:\\Users\\)")

PLACEHOLDER_PATTERNS = [
    "<40-char-sha>",
    "<new-40-char-sha>",
    "<previous-40-char-sha>",
    "<owner/repo>",
    "<version-or-tag>",
    "<YYYY-MM-DD>",
    "<Short plain-English title>",
    "<8char-sha>",
    "TODO:",
    "TBD",
]

# Main-repo file:line citation (not preceded by repo alias `alias:`)
CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_./:-])"
    r"((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]{1,12}|"
    r"(?:[A-Za-z0-9_.-]+/)*(?:Dockerfile|Makefile|LICENSE|README|CHANGELOG))"
    r":(\d+)(?:-(\d+))?(?![A-Za-z0-9_./:-])"
)

# Companion-repo citation: `alias:path/to/file.py:10-20`
COMPANION_CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_./:-])"
    r"([A-Za-z0-9_.-]+):((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]{1,12})"
    r":(\d+)(?:-(\d+))?(?![A-Za-z0-9_./:-])"
)

# GitHub / GitLab / Codeberg blob permalink with line anchor
PERMALINK_RE = re.compile(
    r"https://(github\.com|gitlab\.com|codeberg\.org)/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/"
    r"(?:(?:-\/)?blob)/([^/\s]+)/([^\s#)]+)(?:#L(\d+)(?:-L?(\d+))?)?"
)

IPV4_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
URL_PREFIX_RE = re.compile(r"https?://\S*$")
INLINE_CODE_RE = re.compile(r"`([^`\n]{2,120})`")
TABLE_FID_RE = re.compile(r"^\|\s*(F\d+)\s*\|")
HEADING_FID_RE = re.compile(r"^###\s+(F\d+)\b")
EXCERPT_HEADER_RE = re.compile(r"(?:relevant source|source excerpt|关键源码|相关源码)", re.IGNORECASE)
NEGATION_PREFIX_RE = re.compile(
    r"(?:without|omits?|missing|never|lacks?|skips?|ignores?|bypasses?|no|not|未检查|遗漏|漏传|跳过|忽略|不含|无)\s*$",
    re.IGNORECASE,
)

COMMON_NON_SYMBOLS = {
    "true", "false", "None", "null", "Yes", "No", "Isolate", "Adopt", "Avoid",
    "public", "active", "superseded", "remediated", "disputed", "primary", "edge",
    "full", "static", "dynamic", "stdio", "stderr", "stdout", "stdin", "HEAD",
    "PUBLIC_NOW", "EMBARGO_PRIVATE", "PostHog", "Python", "SQLite", "Chrome",
    "Chromium", "Electron", "GitHub", "Linux", "Windows", "macOS",
}


@dataclasses.dataclass
class ValidationIssue:
    file: str
    line: int | None
    rule: str
    message: str
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "rule": self.rule,
            "message": self.message,
            "hint": self.hint,
        }

    def format_text(self) -> str:
        loc = f"{self.file}:L{self.line}" if self.line else self.file
        suffix = f" (hint: {self.hint})" if self.hint else ""
        return f"ERROR [{self.rule}] {loc}: {self.message}{suffix}"


@dataclasses.dataclass
class Citation:
    body_line: int
    target_key: str  # "__main__" or companion alias
    fpath: str
    start_line: int
    end_line: int
    raw_line: str
    from_permalink: bool = False


@dataclasses.dataclass
class ExcerptBlock:
    header_line: int
    target_key: str
    fpath: str
    start_line: int
    end_line: int
    code_lines: list[tuple[int, str]]  # (body_line_no, text)


def load_schema(schema_path: Path = SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_frontmatter(
    fm: dict[str, Any],
    rel_path: Path,
    schema: dict[str, Any],
    is_translation: bool = False,
    audits_dir: Path = AUDITS_DIR,
) -> list[ValidationIssue]:
    errs: list[ValidationIssue] = []
    fstr = rel_path.as_posix()
    forge, owner, repo_name, filename = rel_path.parts
    expected_repo = f"{owner}/{repo_name}"

    if is_translation:
        m = TRANSLATION_RE.match(filename)
        if not m:
            errs.append(ValidationIssue(fstr, 1, "filename-pattern", "Translation filename must match YYYY-MM-DD-<7..12hex>.zh-CN.md"))
            return errs
        date_name, short_sha_name, lang_suffix = m.group(1), m.group(2), m.group(3)
        expected_canonical = f"{date_name}-{short_sha_name}.md"
        if fm.get("language") != lang_suffix:
            errs.append(ValidationIssue(fstr, None, "translation-language", f"Translation file `{filename}` must declare `language: {lang_suffix}`"))
        if fm.get("translation_of") != expected_canonical:
            errs.append(ValidationIssue(fstr, None, "translation-link", f"Translation must declare `translation_of: {expected_canonical}`"))
        elif not (audits_dir / forge / owner / repo_name / expected_canonical).is_file():
            errs.append(ValidationIssue(fstr, None, "translation-orphan", f"Canonical English snapshot `{expected_canonical}` does not exist"))
    else:
        m = SNAPSHOT_RE.match(filename)
        if not m:
            errs.append(ValidationIssue(fstr, 1, "filename-pattern", "Filename must match YYYY-MM-DD-<7..12hex>.md"))
            return errs
        date_name, short_sha_name = m.group(1), m.group(2)
        if fm.get("language") != "en":
            errs.append(
                ValidationIssue(
                    fstr,
                    None,
                    "canonical-language",
                    f"Canonical snapshot `{filename}` must set `language: en` (found `{fm.get('language')}`)",
                    "Use English for canonical snapshots and put optional Chinese translations in `YYYY-MM-DD-<shortsha>.zh-CN.md`.",
                )
            )

    for req in schema.get("required", []):
        if req not in fm:
            errs.append(ValidationIssue(fstr, None, "frontmatter-required", f"Missing required frontmatter key `{req}`"))

    props = schema.get("properties", {})
    for key, spec in props.items():
        if key not in fm:
            continue
        val = fm[key]
        if "enum" in spec and val not in spec["enum"]:
            errs.append(ValidationIssue(fstr, None, "frontmatter-enum", f"`{key}={val!r}` not in {spec['enum']}"))
        if spec.get("type") == "string":
            if not isinstance(val, str):
                errs.append(ValidationIssue(fstr, None, "frontmatter-type", f"`{key}` must be a string"))
            else:
                if "pattern" in spec and not re.fullmatch(spec["pattern"], val):
                    errs.append(ValidationIssue(fstr, None, "frontmatter-pattern", f"`{key}={val!r}` fails pattern `{spec['pattern']}`"))
                if "minLength" in spec and len(val) < spec["minLength"]:
                    errs.append(ValidationIssue(fstr, None, "frontmatter-length", f"`{key}` shorter than {spec['minLength']} chars ({len(val)})"))
                if "maxLength" in spec and len(val) > spec["maxLength"]:
                    errs.append(ValidationIssue(fstr, None, "frontmatter-length", f"`{key}` exceeds {spec['maxLength']} chars ({len(val)})"))
        elif spec.get("type") == "array":
            if not isinstance(val, list):
                errs.append(ValidationIssue(fstr, None, "frontmatter-type", f"`{key}` must be a list"))
            else:
                item_pat = spec.get("items", {}).get("pattern")
                for item in val:
                    if not isinstance(item, str) or (item_pat and not re.fullmatch(item_pat, item)):
                        errs.append(ValidationIssue(fstr, None, "frontmatter-item", f"Invalid entry `{item!r}` in `{key}`"))
        elif "oneOf" in spec:
            ok = False
            for opt in spec["oneOf"]:
                if opt.get("type") == "string" and isinstance(val, str) and val in opt.get("enum", []):
                    ok = True
                if opt.get("type") == "integer" and isinstance(val, int) and not isinstance(val, bool) and val >= opt.get("minimum", 0):
                    ok = True
            if not ok:
                errs.append(ValidationIssue(fstr, None, "frontmatter-type", f"`{key}={val!r}` does not match schema"))

    if fm.get("forge") != forge:
        errs.append(ValidationIssue(fstr, None, "path-parity", f"frontmatter forge `{fm.get('forge')}` != directory `{forge}`"))
    if fm.get("repo") != expected_repo:
        errs.append(ValidationIssue(fstr, None, "path-parity", f"frontmatter repo `{fm.get('repo')}` != directory `{expected_repo}`"))
    sha = str(fm.get("sha", ""))
    if sha and not sha.startswith(short_sha_name):
        errs.append(ValidationIssue(fstr, None, "sha-parity", f"filename short sha `{short_sha_name}` does not match prefix of `{sha}`"))
    if str(fm.get("audited_at", "")) != date_name:
        errs.append(ValidationIssue(fstr, None, "date-parity", f"filename date `{date_name}` != audited_at `{fm.get('audited_at')}`"))
    if fm.get("disclosure") == "embargoed":
        errs.append(
            ValidationIssue(
                fstr,
                None,
                "disclosure-gate",
                "`disclosure: embargoed` snapshots must NOT be committed to the public repository",
                "Report unpatched exploitable vulnerabilities privately via SECURITY.md / GitHub PVR.",
            )
        )
    return errs


def validate_markdown_structure(
    body: str,
    rel_path: Path,
    fm: dict[str, Any],
) -> tuple[list[ValidationIssue], list[Citation], list[ExcerptBlock]]:
    errs: list[ValidationIssue] = []
    citations: list[Citation] = []
    excerpts: list[ExcerptBlock] = []
    fstr = rel_path.as_posix()
    lines = body.splitlines()
    lower_headings = [ln.lower() for ln in lines if ln.startswith("#")]

    for group in REQUIRED_HEADING_TOKENS:
        if not any(any(tok in h for tok in group) for h in lower_headings):
            errs.append(ValidationIssue(fstr, None, "missing-section", f"Missing required section matching any of {group}"))

    companions = parse_companion_repos(fm.get("companion_repos"))
    alias_set = {c["alias"] for c in companions}
    repo_to_expected_shas: dict[tuple[str, str], list[tuple[str, str]]] = {}
    main_forge = str(fm.get("forge", "github"))
    main_repo = str(fm.get("repo", ""))
    main_sha = str(fm.get("sha", ""))
    if main_repo and main_sha:
        repo_to_expected_shas[(main_forge, main_repo.lower())] = [("__main__", main_sha)]
    for c in companions:
        repo_to_expected_shas.setdefault((c["forge"], c["repo"].lower()), []).append((c["alias"], c["sha"]))

    table_fids: list[str] = []
    heading_fids: list[str] = []
    in_code_fence = False
    pending_excerpt_ref: tuple[int, str, str, int, int] | None = None
    active_excerpt: ExcerptBlock | None = None

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code_fence:
                in_code_fence = True
                if pending_excerpt_ref is not None:
                    h_ln, tkey, fpath, s_ln, e_ln = pending_excerpt_ref
                    active_excerpt = ExcerptBlock(h_ln, tkey, fpath, s_ln, e_ln, [])
                    pending_excerpt_ref = None
            else:
                in_code_fence = False
                if active_excerpt is not None:
                    excerpts.append(active_excerpt)
                    active_excerpt = None
            continue

        if in_code_fence:
            if active_excerpt is not None:
                active_excerpt.code_lines.append((idx, line))
            continue

        # Reset pending excerpt header if a non-blank non-fence line intervenes
        if stripped and pending_excerpt_ref is not None:
            pending_excerpt_ref = None

        # Placeholders check
        for ph in PLACEHOLDER_PATTERNS:
            if ph in line:
                errs.append(ValidationIssue(fstr, idx, "unfilled-placeholder", f"Unfilled template placeholder `{ph}` found"))

        # Leaked host paths check
        if LEAKED_PATH_RE.search(line):
            errs.append(
                ValidationIssue(
                    fstr,
                    idx,
                    "path-leak",
                    f"Contains local machine path leak: `{stripped[:100]}`",
                    "Remove local machine paths (/home/..., /Users/..., /tmp/oss-audit).",
                )
            )

        # Total-then-Detail F-ID parity
        tm = TABLE_FID_RE.match(stripped)
        if tm:
            table_fids.append(tm.group(1))
        hm = HEADING_FID_RE.match(stripped)
        if hm:
            heading_fids.append(hm.group(1))

        # Banned intent words (EN + ZH) outside blockquotes and quoted strings
        if not line.lstrip().startswith(">"):
            scrubbed = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'|`[^`\n]*`|“[^”\n]*”|「[^」\n]*」', "", line)
            low = scrubbed.lower()
            for bw in BANNED_WORDS_EN:
                if re.search(rf"\b{re.escape(bw)}\b", low):
                    errs.append(
                        ValidationIssue(
                            fstr,
                            idx,
                            "banned-intent-word",
                            f"Banned intent word `{bw}` outside verbatim quote",
                            "Use observable behavioral verbs (reads, hashes, sends, retains, omits, contradicts).",
                        )
                    )
            for bw_zh in BANNED_WORDS_ZH:
                if bw_zh in scrubbed:
                    errs.append(
                        ValidationIssue(
                            fstr,
                            idx,
                            "banned-intent-word",
                            f"Banned intent phrase `{bw_zh}` outside verbatim quote",
                            "改用可观察行为动词（读取、哈希、发送、留存、遗漏、违背承诺）。",
                        )
                    )

        line_citations: list[Citation] = []

        # Collect GitHub/GitLab/Codeberg permalinks and check SHA pinning
        for pm in PERMALINK_RE.finditer(line):
            host, slug, ref, ppath, l_start, l_end = pm.groups()
            pforge = "github" if host == "github.com" else ("gitlab" if host == "gitlab.com" else "codeberg")
            expected = repo_to_expected_shas.get((pforge, slug.lower()))
            if expected:
                matched_key: str | None = None
                for key_name, exp_sha in expected:
                    if ref == exp_sha:
                        matched_key = key_name
                        break
                if matched_key is None:
                    allowed = ", ".join(s[:8] for _, s in expected)
                    errs.append(
                        ValidationIssue(
                            fstr,
                            idx,
                            "permalink-sha-mismatch",
                            f"Permalink for `{slug}` uses ref `{ref}` instead of pinned SHA ({allowed})",
                            "Pin every source permalink to the exact 40-char audited SHA (or declared companion SHA).",
                        )
                    )
                elif l_start:
                    s_ln = int(l_start)
                    e_ln = int(l_end) if l_end else s_ln
                    cit = Citation(idx, matched_key, ppath, s_ln, e_ln, line, from_permalink=True)
                    citations.append(cit)
                    line_citations.append(cit)

        # Collect companion repo citations (`alias:path/to/file.py:10-20`)
        companion_spans: list[tuple[int, int]] = []
        for ccm in COMPANION_CITATION_RE.finditer(line):
            alias, fpath, s_str, e_str = ccm.groups()
            if alias not in alias_set:
                continue
            if URL_PREFIX_RE.search(line[: ccm.start()]):
                continue
            s_ln = int(s_str)
            e_ln = int(e_str) if e_str else s_ln
            cit = Citation(idx, alias, fpath, s_ln, e_ln, line)
            citations.append(cit)
            line_citations.append(cit)
            companion_spans.append((ccm.start(), ccm.end()))

        # Collect main-repo `file:line` citations
        for cm in CITATION_RE.finditer(line):
            start_pos, end_pos = cm.start(), cm.end()
            if any(cs <= start_pos and end_pos <= ce for cs, ce in companion_spans):
                continue
            if URL_PREFIX_RE.search(line[:start_pos]):
                continue
            fpath = cm.group(1)
            if IPV4_RE.match(fpath):
                continue
            if fpath.startswith(("http", "https")) or fpath.endswith((".com", ".org", ".io", ".dev", ".ai", ".net", ".gg")):
                continue
            s_ln = int(cm.group(2))
            e_ln = int(cm.group(3)) if cm.group(3) else s_ln
            cit = Citation(idx, "__main__", fpath, s_ln, e_ln, line)
            citations.append(cit)
            line_citations.append(cit)

        # Detect excerpt header right before a verbatim source block
        if EXCERPT_HEADER_RE.search(line) and line_citations:
            c0 = line_citations[0]
            pending_excerpt_ref = (idx, c0.target_key, c0.fpath, c0.start_line, c0.end_line)

    if table_fids or heading_fids:
        if set(table_fids) != set(heading_fids):
            errs.append(
                ValidationIssue(
                    fstr,
                    None,
                    "finding-parity",
                    f"F-ID parity mismatch between Executive Verdict table {table_fids} and Section 2 headings {heading_fids}",
                    "Ensure every F# in the summary table has a matching `### F#: ...` section (and vice versa).",
                )
            )
    return errs, citations, excerpts


def _extract_positive_symbol_tokens(raw_line: str) -> list[str]:
    """Extract likely code symbol identifiers from backticks, ignoring negated/path/prose tokens."""
    tokens: list[str] = []
    for m in INLINE_CODE_RE.finditer(raw_line):
        tok = m.group(1).strip()
        prefix = raw_line[max(0, m.start() - 32) : m.start()]
        if NEGATION_PREFIX_RE.search(prefix):
            continue
        # Strip trailing call parens like `capture_cli_event()` -> `capture_cli_event`
        if tok.endswith("()"):
            base = tok[:-2]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{2,50}", base):
                sym = base.split(".")[-1]
                if sym not in COMMON_NON_SYMBOLS:
                    tokens.append(sym)
            continue
        # Match explicit snake_case or ALL_CAPS identifiers (not file paths, flags, or key=val)
        if "/" in tok or ":" in tok or "=" in tok or " " in tok or tok.startswith("-"):
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{3,45}", tok) and "_" in tok:
            if tok not in COMMON_NON_SYMBOLS and not re.fullmatch(r"F\d+", tok):
                tokens.append(tok)
    return tokens


def verify_against_repo_map(
    repo_dirs: dict[str, Path],
    rel_path: Path,
    citations: list[Citation],
    excerpts: list[ExcerptBlock],
) -> tuple[list[ValidationIssue], int]:
    errs: list[ValidationIssue] = []
    fstr = rel_path.as_posix()
    file_cache: dict[tuple[str, str], list[str] | None] = {}
    checked = 0

    def load_lines(tkey: str, fpath: str) -> list[str] | None:
        ckey = (tkey, fpath)
        if ckey in file_cache:
            return file_cache[ckey]
        rdir = repo_dirs.get(tkey)
        if rdir is None:
            file_cache[ckey] = None
            return None
        full = (rdir / fpath).resolve()
        try:
            full.relative_to(rdir.resolve())
        except ValueError:
            file_cache[ckey] = None
            return None
        if full.is_file():
            file_cache[ckey] = full.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            file_cache[ckey] = None
        return file_cache[ckey]

    # Group non-permalink citations per body line to count distinct file:line refs on a line
    per_line_targets: dict[int, set[tuple[str, str, int, int]]] = {}
    for c in citations:
        per_line_targets.setdefault(c.body_line, set()).add((c.target_key, c.fpath, c.start_line, c.end_line))

    seen_checked: set[tuple[int, str, str, int, int]] = set()
    for c in citations:
        if c.target_key not in repo_dirs:
            continue
        dedup_key = (c.body_line, c.target_key, c.fpath, c.start_line, c.end_line)
        if dedup_key in seen_checked:
            continue
        seen_checked.add(dedup_key)

        flines = load_lines(c.target_key, c.fpath)
        label = c.fpath if c.target_key == "__main__" else f"{c.target_key}:{c.fpath}"
        if flines is None:
            errs.append(ValidationIssue(fstr, c.body_line, "upstream-file-missing", f"Cited file not found at pinned SHA: `{label}`"))
            continue
        if c.start_line < 1 or c.end_line < c.start_line or c.end_line > len(flines):
            errs.append(
                ValidationIssue(
                    fstr,
                    c.body_line,
                    "upstream-line-oob",
                    f"Cited range `{label}:{c.start_line}-{c.end_line}` out of bounds (file has {len(flines)} lines)",
                )
            )
            continue
        checked += 1

        # Verify code symbols on single-citation prose lines (skip markdown tables & headings)
        if (
            len(per_line_targets.get(c.body_line, set())) == 1
            and not c.raw_line.lstrip().startswith(("|", "#", ">"))
            and not EXCERPT_HEADER_RE.search(c.raw_line)
        ):
            symbols = _extract_positive_symbol_tokens(c.raw_line)
            if symbols:
                lo = max(0, c.start_line - 6)
                hi = min(len(flines), c.end_line + 5)
                window = "\n".join(flines[lo:hi])
                if not any(sym in window for sym in symbols):
                    errs.append(
                        ValidationIssue(
                            fstr,
                            c.body_line,
                            "upstream-symbol-mismatch",
                            f"None of cited code symbol(s) {symbols} found near `{label}:{c.start_line}-{c.end_line}`",
                            "Verify line numbers or quote an identifier that appears in the cited source range.",
                        )
                    )

    # Verify verbatim fenced source excerpts immediately following `Relevant source (...)` headers
    for ex in excerpts:
        if ex.target_key not in repo_dirs:
            continue
        flines = load_lines(ex.target_key, ex.fpath)
        if flines is None:
            continue
        lo = max(0, ex.start_line - 3)
        hi = min(len(flines), ex.end_line + 3)
        upstream_stripped = {ln.strip() for ln in flines[lo:hi] if ln.strip()}
        label = ex.fpath if ex.target_key == "__main__" else f"{ex.target_key}:{ex.fpath}"
        for body_ln, code_ln in ex.code_lines:
            cs = code_ln.strip()
            if not cs or cs in {"...", "# ...", "// ..."} or cs.startswith(("# ...", "// ...")):
                continue
            if cs not in upstream_stripped:
                errs.append(
                    ValidationIssue(
                        fstr,
                        body_ln,
                        "excerpt-mismatch",
                        f"Verbatim source excerpt line does not match `{label}:{ex.start_line}-{ex.end_line}`: `{cs[:80]}`",
                        "Copy lines verbatim from the pinned upstream SHA (use `# ...` for omitted middle lines).",
                    )
                )
    return errs, checked


def inert_fetch_sha(
    forge: str,
    repo_slug: str,
    sha: str,
    dest: Path,
    wanted_paths: set[str] | None = None,
) -> tuple[bool, str]:
    url = f"{forge_base_url(forge)}/{repo_slug}.git"
    dest.mkdir(parents=True, exist_ok=True)
    base_flags = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false"]

    # Fast path: partial blobless clone + root-anchored sparse checkout of cited paths only
    if wanted_paths:
        patterns = sorted(f"/{p.lstrip('/')}" for p in wanted_paths if p and not p.startswith(".."))
        if patterns:
            fast_steps = [
                ["git", *base_flags, "-C", str(dest), "init", "-q"],
                ["git", *base_flags, "-C", str(dest), "remote", "add", "origin", url],
                ["git", *base_flags, "-C", str(dest), "sparse-checkout", "init", "--no-cone"],
                ["git", *base_flags, "-C", str(dest), "sparse-checkout", "set", "--", *patterns],
                ["git", *base_flags, "-C", str(dest), "fetch", "--depth=1", "--filter=blob:none", "origin", sha],
                ["git", *base_flags, "-C", str(dest), "checkout", "-q", "FETCH_HEAD"],
            ]
            fast_ok = True
            for cmd in fast_steps:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
                if proc.returncode != 0:
                    fast_ok = False
                    break
            if fast_ok:
                return True, ""
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True, exist_ok=True)

    steps = [
        ["git", *base_flags, "-C", str(dest), "init", "-q"],
        ["git", *base_flags, "-C", str(dest), "remote", "add", "origin", url],
        ["git", *base_flags, "-C", str(dest), "fetch", "--depth=1", "origin", sha],
        ["git", *base_flags, "-C", str(dest), "checkout", "-q", "FETCH_HEAD"],
    ]
    for cmd in steps:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout).strip()[:300]
    return True, ""


def check_pr_scope(base_ref: str, root_dir: Path = ROOT, allow_mixed: bool = False) -> list[ValidationIssue]:
    proc = subprocess.run(
        ["git", "-C", str(root_dir), "diff", "--name-status", f"{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return [ValidationIssue("git", None, "pr-scope-git-error", f"Could not diff against `{base_ref}`: {proc.stderr.strip()}")]

    changed: list[tuple[str, str]] = []
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        changed.append((parts[0], parts[-1]))

    touches_audits = any(p.startswith("audits/") for _, p in changed)
    if not touches_audits or allow_mixed:
        return []

    errs: list[ValidationIssue] = []
    protected_prefixes = ("tools/", ".github/", "data/schema/")
    for status, path in changed:
        if path.startswith(protected_prefixes):
            errs.append(
                ValidationIssue(
                    path,
                    None,
                    "pr-scope-protected",
                    f"Audit PR modifies protected path `{path}`",
                    "Split tooling/CI/schema changes into a dedicated PR separate from audit snapshots.",
                )
            )
        if status == "M" and path.startswith("audits/"):
            fname = Path(path).name
            if SNAPSHOT_RE.match(fname):
                errs.append(
                    ValidationIssue(
                        path,
                        None,
                        "pr-scope-immutable-snapshot",
                        f"PR modifies an existing historical snapshot `{path}`",
                        "For upstream fixes, add a new `YYYY-MM-DD-<newsha>.md` file instead of overwriting history.",
                    )
                )
    return errs


def validate_repo(
    targets: list[Path],
    root_dir: Path = ROOT,
    verify_upstream: bool = False,
    local_map: dict[str, Path] | None = None,
) -> tuple[list[ValidationIssue], int, int, int]:
    local_map = local_map or {}
    audits_dir = root_dir / "audits"
    schema = load_schema(root_dir / "data" / "schema" / "audit-frontmatter.schema.json")

    all_errors: list[ValidationIssue] = []
    total_citations = 0
    snaps_checked = 0
    trans_checked = 0

    parsed_docs: list[tuple[Path, str, dict[str, Any], list[Citation], list[ExcerptBlock]]] = []
    wanted_by_repo_key: dict[tuple[str, str, str], set[str]] = {}

    for path in targets:
        rel = path.relative_to(audits_dir)
        rel_str = rel.as_posix()
        is_translation = bool(TRANSLATION_RE.match(path.name))
        if is_translation:
            trans_checked += 1
        else:
            snaps_checked += 1

        text = path.read_text(encoding="utf-8")
        try:
            fm, body = parse_frontmatter(text)
        except ValueError as exc:
            all_errors.append(ValidationIssue(rel_str, 1, "frontmatter-parse", str(exc)))
            continue

        all_errors.extend(validate_frontmatter(fm, rel, schema, is_translation=is_translation, audits_dir=audits_dir))
        body_errs, citations, excerpts = validate_markdown_structure(body, rel, fm)
        all_errors.extend(body_errs)
        parsed_docs.append((rel, rel_str, fm, citations, excerpts))

        slug = str(fm.get("repo", ""))
        sha = str(fm.get("sha", ""))
        forge = str(fm.get("forge", "github"))
        comp_by_alias = {c["alias"]: c for c in parse_companion_repos(fm.get("companion_repos"))}
        for c in citations:
            if c.target_key == "__main__" and slug and sha:
                wanted_by_repo_key.setdefault((forge, slug, sha), set()).add(c.fpath)
            elif c.target_key in comp_by_alias:
                comp = comp_by_alias[c.target_key]
                wanted_by_repo_key.setdefault((comp["forge"], comp["repo"], comp["sha"]), set()).add(c.fpath)

    with tempfile.TemporaryDirectory(prefix="tnr-verify-") as td:
        fetched_cache: dict[tuple[str, str, str], Path] = {}

        def resolve_repo(forge: str, slug: str, sha: str, alias: str | None, rel_str: str) -> Path | None:
            for lookup in ([alias] if alias else []) + [slug]:
                if lookup in local_map:
                    cand = local_map[lookup]
                    head = subprocess.run(
                        ["git", "-C", str(cand), "rev-parse", "HEAD"],
                        capture_output=True,
                        text=True,
                        check=False,
                    ).stdout.strip()
                    if head != sha:
                        all_errors.append(
                            ValidationIssue(rel_str, None, "local-sha-mismatch", f"Local repo `{lookup}` HEAD `{head}` != expected `{sha}`")
                        )
                        return None
                    return cand
            if verify_upstream and re.fullmatch(r"[0-9a-f]{40}", sha):
                key = (forge, slug, sha)
                if key not in fetched_cache:
                    dest = Path(td) / f"{slug.replace('/', '__')}_{sha[:8]}"
                    ok, err = inert_fetch_sha(forge, slug, sha, dest, wanted_paths=wanted_by_repo_key.get(key))
                    if not ok:
                        all_errors.append(
                            ValidationIssue(rel_str, None, "upstream-fetch-failed", f"Failed to fetch {forge}/{slug}@{sha}: {err}")
                        )
                        return None
                    fetched_cache[key] = dest
                return fetched_cache[key]
            return None

        for rel, rel_str, fm, citations, excerpts in parsed_docs:
            repo_dirs: dict[str, Path] = {}
            slug = str(fm.get("repo", ""))
            sha = str(fm.get("sha", ""))
            forge = str(fm.get("forge", "github"))
            if verify_upstream or slug in local_map:
                rdir = resolve_repo(forge, slug, sha, None, rel_str)
                if rdir is not None:
                    repo_dirs["__main__"] = rdir
            for comp in parse_companion_repos(fm.get("companion_repos")):
                if verify_upstream or comp["alias"] in local_map or comp["repo"] in local_map:
                    cdir = resolve_repo(comp["forge"], comp["repo"], comp["sha"], comp["alias"], rel_str)
                    if cdir is not None:
                        repo_dirs[comp["alias"]] = cdir

            if repo_dirs:
                c_errs, c_count = verify_against_repo_map(repo_dirs, rel, citations, excerpts)
                all_errors.extend(c_errs)
                total_citations += c_count

    return all_errors, snaps_checked, trans_checked, total_citations


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate trust-no-readme audit snapshots & translations.")
    ap.add_argument("--verify-upstream", action="store_true", help="Fetch pinned upstream & companion SHAs read-only and verify citations")
    ap.add_argument("--local-repo", action="append", default=[], help="Map owner/repo=/abs/path or alias=/abs/path for offline verification")
    ap.add_argument("--format", choices=["text", "json"], default="text", help="Output format (use json for agent self-repair)")
    ap.add_argument("--check-pr-scope", metavar="BASE_REF", help="Check PR diff against BASE_REF for protected paths / overwritten snapshots")
    ap.add_argument("--allow-mixed-pr", action="store_true", help="Allow mixing tooling and audit edits when running --check-pr-scope")
    ap.add_argument("files", nargs="*", help="Optional subset of snapshot/translation files to validate")
    args = ap.parse_args()

    local_map: dict[str, Path] = {}
    for entry in args.local_repo:
        if "=" not in entry:
            ap.error("--local-repo expects owner/repo=/path or alias=/path")
        k, v = entry.split("=", 1)
        local_map[k.strip()] = Path(v.strip()).resolve()

    if args.check_pr_scope:
        scope_errs = check_pr_scope(args.check_pr_scope, root_dir=ROOT, allow_mixed=args.allow_mixed_pr)
        if args.format == "json":
            print(json.dumps({"ok": not scope_errs, "errors": [e.to_dict() for e in scope_errs]}, indent=2, ensure_ascii=False))
        else:
            for e in scope_errs:
                print(e.format_text(), file=sys.stderr)
            if not scope_errs:
                print(f"OK pr-scope: clean against {args.check_pr_scope}.")
        return 1 if scope_errs else 0

    targets: list[Path] = []
    if args.files:
        targets = [Path(f).resolve() for f in args.files]
    elif AUDITS_DIR.is_dir():
        for p in sorted(AUDITS_DIR.rglob("*.md")):
            if p.name not in {"README.md", "responses.md"}:
                targets.append(p)

    all_errors, snaps_checked, trans_checked, total_citations = validate_repo(
        targets,
        root_dir=ROOT,
        verify_upstream=args.verify_upstream,
        local_map=local_map,
    )

    if args.format == "json":
        payload = {
            "ok": not all_errors,
            "snapshots_checked": snaps_checked,
            "translations_checked": trans_checked,
            "citations_verified": total_citations,
            "error_count": len(all_errors),
            "errors": [e.to_dict() for e in all_errors],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if all_errors else 0

    if all_errors:
        for e in all_errors:
            print(e.format_text(), file=sys.stderr)
        print(
            f"FAILED validate: {len(all_errors)} error(s) across {snaps_checked} snapshot(s) and {trans_checked} translation(s).",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK validate: {snaps_checked} snapshot(s) and {trans_checked} translation(s) valid"
        + (f", {total_citations} upstream citation(s) mechanically verified." if total_citations else ".")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
