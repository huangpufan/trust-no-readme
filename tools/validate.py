#!/usr/bin/env python3
"""Mechanical validator for trust-no-readme audit snapshots, translations & tracking.json (stdlib-only).

Checks:
1. Path convention (`audits/<forge>/<owner>/<repo>/YYYY-MM-DD-<shortsha>.md` & optional `.zh-CN.md`),
   real calendar date validity, and YAML frontmatter schema compliance.
2. Required Total-then-Detail sections, `F1..Fn` table-to-detail parity, zero unfilled placeholders,
   zero banned EN/ZH intent words outside quotes, and zero local machine path leaks.
3. Permalink SHA consistency (no floating `main`/`master` links on audited/companion repos).
4. Per-project `tracking.json` strict JSON Schema compliance (including `additionalProperties: false`),
   finding cross-references, canonical URL uniqueness, origin provenance vs. frozen snapshots,
   strict proof gates for `verified_main`, `verified_shipped` (requiring `supersedes_sha` chain,
   matching `fix_release`, and full finding resolution), `partial_fix`, `wontfix`, `closed_unfixed`,
   strict standardized embargoed CVD copy & `disclosed_expired` window checks, and wording hygiene.
5. Optional `--verify-upstream` (or `--local-repo key=/path`): inert read-only verification of cited
   files/lines/excerpts/identifiers at exact pinned SHAs, AND fail-closed commit ancestry & release
   tag verification for claimed `fix_commit` SHAs.
6. Optional `--check-pr-scope <base-ref>`: uses `--no-renames --name-status -z` to block any deletion,
   rename, or unauthorized edit of frozen snapshots/translations (even under `--allow-mixed-pr`), and
   blocks tooling/schema edits inside audit PRs unless `--allow-mixed-pr` is passed.
7. `--format json`: emits machine-readable diagnostics for AI agents to self-repair in one loop.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
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
    parse_iso_date,
    parse_upstream_issues,
)

SCHEMA_PATH = ROOT / "data" / "schema" / "audit-frontmatter.schema.json"
TRACKING_SCHEMA_PATH = ROOT / "data" / "schema" / "tracking.schema.json"

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

CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_./:-])"
    r"((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]{1,12}|"
    r"(?:[A-Za-z0-9_.-]+/)*(?:Dockerfile|Makefile|LICENSE|README|CHANGELOG))"
    r":(\d+)(?:-(\d+))?(?![A-Za-z0-9_./:-])"
)

COMPANION_CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_./:-])"
    r"([A-Za-z0-9_.-]+):((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]{1,12})"
    r":(\d+)(?:-(\d+))?(?![A-Za-z0-9_./:-])"
)

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
PR_PATH_RE = re.compile(r"/(?:pull|pulls|merge_requests)/(\d+)$")
SPECIFIC_EVIDENCE_PATH_RE = re.compile(
    r"/(?:issues|pull|pulls|merge_requests|discussions)/\d+$|/commit/[0-9a-f]{7,40}$"
)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
EMBARGO_EN_RE = re.compile(
    r"^(\d+) withheld security findings? under (\d+)-day coordinated private disclosure \(details omitted\)\.$"
)
EMBARGO_ZH_RE = re.compile(
    r"^(\d+) 项处于 (\d+) 天负责任私密披露期的暂隐安全问题（细节暂隐）。$"
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
    target_key: str
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
    code_lines: list[tuple[int, str]]


@dataclasses.dataclass
class CommitCheck:
    forge: str
    repo: str
    sha: str
    ctx_label: str
    mode: str  # "exists_only" | "verified_main" | "verified_shipped" | "partial_fix" | "cvd_shipped"
    orig_snap_sha: str | None = None
    target_snap_sha: str | None = None
    fix_release: str | None = None
    cutoff_date: str | None = None
    early_disclosed_at: str | None = None


def load_schema(schema_path: Path = SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _is_valid_calendar_iso(value: Any, allow_datetime: bool = True) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            dt.date.fromisoformat(s)
            return True
        except ValueError:
            return False
    if allow_datetime and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", s):
        try:
            dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
    return False


def _normalize_canonical_url(url: str) -> str:
    """Browser-equivalent URL canonicalizer (percent-decoding, default ports, dot segments, ASCII numeric IDs)."""
    s = url.strip().replace("\\", "/")
    try:
        parsed = urllib.parse.urlsplit(s)
    except ValueError:
        return s.lower()
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        # Preserve malformed port rather than normalizing it away as a default port
        return s.lower()
    if port is None or (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        netloc = host
    else:
        netloc = f"{host}:{port}"

    raw_path = urllib.parse.unquote(parsed.path or "/")
    norm_path = posixpath.normpath(raw_path)
    if not norm_path.startswith("/"):
        norm_path = "/" + norm_path
    norm_path = norm_path.rstrip("/") or "/"

    parts = [p for p in norm_path.split("/") if p]
    if len(parts) >= 2:
        parts[0] = parts[0].lower()
        parts[1] = parts[1].lower()
    if len(parts) >= 4 and parts[2] == "-":
        parts.pop(2)
    if len(parts) >= 4 and parts[2].lower() in {"issues", "issue", "pull", "pulls", "merge_requests", "discussions"}:
        kind = parts[2].lower()
        if kind == "issue":
            kind = "issues"
        parts[2] = kind
        if re.fullmatch(r"[0-9]+", parts[3]):
            parts[3] = parts[3].lstrip("0") or "0"
        parts = parts[:4]
    elif len(parts) >= 4 and parts[2].lower() in {"commit", "commits"}:
        parts[2] = "commit"
        parts[3] = parts[3].lower()
        parts = parts[:4]

    canonical_path = "/" + "/".join(parts) if parts else ""
    return f"{scheme}://{netloc}{canonical_path}"


def _safe_date_shift(base: dt.date, delta_days: int) -> dt.date | None:
    try:
        return base + dt.timedelta(days=delta_days)
    except OverflowError:
        return None


def _allowed_repo_url_prefixes(forge: str, repo: str, companions: list[dict[str, str]] | None = None) -> list[str]:
    prefixes = [f"{forge_base_url(forge)}/{repo}".lower()]
    for c in companions or []:
        prefixes.append(f"{forge_base_url(c['forge'])}/{c['repo']}".lower())
    return prefixes


def _url_belongs_to_prefixes(url: str, prefixes: list[str]) -> bool:
    norm = _normalize_canonical_url(url)
    return any(norm == p or norm.startswith(p + "/") for p in prefixes)


def _check_prose_hygiene(text: str, fstr: str, field_label: str, line_no: int | None = None) -> list[ValidationIssue]:
    errs: list[ValidationIssue] = []
    for ph in PLACEHOLDER_PATTERNS:
        if ph in text:
            errs.append(ValidationIssue(fstr, line_no, "unfilled-placeholder", f"Unfilled template placeholder `{ph}` in {field_label}"))
    if LEAKED_PATH_RE.search(text):
        errs.append(
            ValidationIssue(
                fstr,
                line_no,
                "path-leak",
                f"Contains local machine path leak in {field_label}: `{text[:100]}`",
                "Remove local machine paths (/home/..., /Users/..., /tmp/oss-audit).",
            )
        )
    scrubbed = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'|`[^`\n]*`|“[^”\n]*”|「[^」\n]*」', "", text)
    low = scrubbed.lower()
    for bw in BANNED_WORDS_EN:
        if re.search(rf"\b{re.escape(bw)}\b", low):
            errs.append(
                ValidationIssue(
                    fstr,
                    line_no,
                    "banned-intent-word",
                    f"Banned intent word `{bw}` outside verbatim quote in {field_label}",
                    "Use observable behavioral verbs (reads, hashes, sends, retains, omits, contradicts).",
                )
            )
    for bw_zh in BANNED_WORDS_ZH:
        if bw_zh in scrubbed:
            errs.append(
                ValidationIssue(
                    fstr,
                    line_no,
                    "banned-intent-word",
                    f"Banned intent phrase `{bw_zh}` outside verbatim quote in {field_label}",
                    "改用可观察行为动词（读取、哈希、发送、留存、遗漏、违背承诺）。",
                )
            )
    return errs


def _matches_json_type(val: Any, expected: str) -> bool:
    if expected == "null":
        return val is None
    if expected == "boolean":
        return isinstance(val, bool)
    if expected == "integer":
        return isinstance(val, int) and not isinstance(val, bool)
    if expected == "string":
        return isinstance(val, str)
    if expected == "array":
        return isinstance(val, list)
    if expected == "object":
        return isinstance(val, dict)
    return True


def _validate_json_schema_node(val: Any, spec: dict[str, Any], fstr: str, path_label: str) -> list[ValidationIssue]:
    errs: list[ValidationIssue] = []
    expected_type = spec.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_json_type(val, t) for t in types):
            errs.append(ValidationIssue(fstr, None, "tracking-schema-type", f"`{path_label}` has invalid type (expected {types})"))
            return errs

    if val is None:
        if "enum" in spec and None not in spec["enum"]:
            errs.append(ValidationIssue(fstr, None, "tracking-schema-enum", f"`{path_label}` cannot be null"))
        return errs

    if "enum" in spec and val not in spec["enum"]:
        errs.append(ValidationIssue(fstr, None, "tracking-schema-enum", f"`{path_label}={val!r}` not in {spec['enum']}"))

    if isinstance(val, str):
        if "minLength" in spec and len(val.strip()) < spec["minLength"]:
            errs.append(ValidationIssue(fstr, None, "tracking-schema-length", f"`{path_label}` shorter than {spec['minLength']} non-whitespace chars"))
        if "pattern" in spec and not re.fullmatch(spec["pattern"], val):
            errs.append(ValidationIssue(fstr, None, "tracking-schema-pattern", f"`{path_label}={val!r}` fails pattern `{spec['pattern']}`"))
        if "maxLength" in spec and len(val) > spec["maxLength"]:
            errs.append(ValidationIssue(fstr, None, "tracking-schema-length", f"`{path_label}` exceeds {spec['maxLength']} chars ({len(val)})"))
    elif isinstance(val, int) and not isinstance(val, bool):
        if "minimum" in spec and val < spec["minimum"]:
            errs.append(ValidationIssue(fstr, None, "tracking-schema-range", f"`{path_label}={val}` below minimum {spec['minimum']}"))
        if "maximum" in spec and val > spec["maximum"]:
            errs.append(ValidationIssue(fstr, None, "tracking-schema-range", f"`{path_label}={val}` above maximum {spec['maximum']}"))
    elif isinstance(val, list):
        if "minItems" in spec and len(val) < spec["minItems"]:
            errs.append(ValidationIssue(fstr, None, "tracking-schema-length", f"`{path_label}` requires at least {spec['minItems']} item(s)"))
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for idx, elem in enumerate(val):
                errs.extend(_validate_json_schema_node(elem, item_spec, fstr, f"{path_label}[{idx}]"))
    elif isinstance(val, dict):
        for req in spec.get("required", []):
            if req not in val:
                errs.append(ValidationIssue(fstr, None, "tracking-schema-required", f"Missing required key `{path_label}.{req}`" if path_label else f"Missing required key `{req}`"))
        props = spec.get("properties", {})
        disallow_extra = spec.get("additionalProperties") is False
        for k, v in val.items():
            sub_path = f"{path_label}.{k}" if path_label else k
            if k in props:
                errs.extend(_validate_json_schema_node(v, props[k], fstr, sub_path))
            elif disallow_extra:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-schema-extra-property",
                        f"Unexpected extra property `{sub_path}` disallowed by schema",
                        "Remove undeclared keys from tracking.json.",
                    )
                )
    return errs


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
                if "minLength" in spec and len(val.strip()) < spec["minLength"]:
                    errs.append(ValidationIssue(fstr, None, "frontmatter-length", f"`{key}` shorter than {spec['minLength']} chars ({len(val.strip())})"))
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
    if fm.get("audited_at") and not _is_valid_calendar_iso(fm.get("audited_at"), allow_datetime=False):
        errs.append(ValidationIssue(fstr, None, "frontmatter-invalid-date", f"`audited_at={fm.get('audited_at')!r}` is not a valid calendar date"))
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

        if stripped and pending_excerpt_ref is not None:
            pending_excerpt_ref = None

        for ph in PLACEHOLDER_PATTERNS:
            if ph in line:
                errs.append(ValidationIssue(fstr, idx, "unfilled-placeholder", f"Unfilled template placeholder `{ph}` found"))

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

        tm = TABLE_FID_RE.match(stripped)
        if tm:
            table_fids.append(tm.group(1))
        hm = HEADING_FID_RE.match(stripped)
        if hm:
            heading_fids.append(hm.group(1))

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


def _load_snapshot_meta(snap_path: Path) -> dict[str, Any] | None:
    if not snap_path.is_file():
        return None
    text = snap_path.read_text(encoding="utf-8")
    try:
        fm, body = parse_frontmatter(text)
    except ValueError:
        return None
    fids: set[str] = set()
    for line in body.splitlines():
        hm = HEADING_FID_RE.match(line.strip())
        if hm:
            fids.add(hm.group(1))
    existing_urls = {
        _normalize_canonical_url(iss["url"])
        for iss in parse_upstream_issues(fm.get("upstream_issues"))
        if iss.get("state") == "existing" and iss.get("url")
    }
    return {
        "fm": fm,
        "fids": fids,
        "existing_urls": existing_urls,
        "companions": parse_companion_repos(fm.get("companion_repos")),
    }


def _nonblank(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


def _supersedes_chain_reaches(
    start_meta: dict[str, Any],
    target_sha: str,
    sha_to_meta: dict[str, dict[str, Any]],
) -> bool:
    """Verify `start_meta` is connected to `target_sha` via explicit `supersedes_sha` chain."""
    seen: set[str] = set()
    cur = start_meta["fm"].get("supersedes_sha")
    while isinstance(cur, str) and cur:
        if cur == target_sha:
            return True
        if cur in seen:
            return False
        seen.add(cur)
        nxt = sha_to_meta.get(cur)
        if nxt is None:
            return False
        cur = nxt["fm"].get("supersedes_sha")
    return False


def validate_tracking_file(
    tracking_path: Path,
    audits_dir: Path,
    tracking_schema: dict[str, Any],
) -> tuple[list[ValidationIssue], list[CommitCheck]]:
    """Validate one `tracking.json`. Returns (errors, commit_checks)."""
    rel = tracking_path.relative_to(audits_dir)
    fstr = rel.as_posix()
    errs: list[ValidationIssue] = []
    commit_checks: list[CommitCheck] = []

    if len(rel.parts) != 4 or rel.parts[-1] != "tracking.json":
        errs.append(ValidationIssue(fstr, 1, "tracking-path", "Tracking file must live at audits/<forge>/<owner>/<repo>/tracking.json"))
        return errs, commit_checks

    forge, owner, repo_name, _ = rel.parts
    expected_repo = f"{owner}/{repo_name}"
    proj_dir = tracking_path.parent

    try:
        data = json.loads(tracking_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errs.append(ValidationIssue(fstr, exc.lineno, "tracking-json-parse", f"Invalid JSON: {exc.msg}"))
        return errs, commit_checks

    errs.extend(_validate_json_schema_node(data, tracking_schema, fstr, ""))
    if not isinstance(data, dict):
        return errs, commit_checks

    if data.get("forge") != forge:
        errs.append(ValidationIssue(fstr, None, "tracking-path-parity", f"tracking forge `{data.get('forge')}` != directory `{forge}`"))
    if data.get("repo") != expected_repo:
        errs.append(ValidationIssue(fstr, None, "tracking-path-parity", f"tracking repo `{data.get('repo')}` != directory `{expected_repo}`"))

    if data.get("last_checked_at") and not _is_valid_calendar_iso(data.get("last_checked_at"), allow_datetime=False):
        errs.append(ValidationIssue(fstr, None, "tracking-invalid-date", f"`last_checked_at={data.get('last_checked_at')!r}` is not a valid calendar date"))
    lrel = data.get("latest_upstream_release")
    if isinstance(lrel, dict) and lrel.get("published_at") and not _is_valid_calendar_iso(lrel.get("published_at"), allow_datetime=True):
        errs.append(ValidationIssue(fstr, None, "tracking-invalid-date", f"`latest_upstream_release.published_at={lrel.get('published_at')!r}` is not a valid calendar date/time"))

    snap_meta_cache: dict[str, dict[str, Any] | None] = {}

    def get_snap_meta(snap_name: str) -> dict[str, Any] | None:
        if snap_name not in snap_meta_cache:
            snap_meta_cache[snap_name] = _load_snapshot_meta(proj_dir / snap_name)
        return snap_meta_cache[snap_name]

    project_existing_urls: set[str] = set()
    sha_to_meta: dict[str, dict[str, Any]] = {}
    for sp in sorted(proj_dir.glob("20*.md")):
        if SNAPSHOT_RE.match(sp.name):
            meta = get_snap_meta(sp.name)
            if meta:
                project_existing_urls.update(meta["existing_urls"])
                if isinstance(meta["fm"].get("sha"), str):
                    sha_to_meta[meta["fm"]["sha"]] = meta

    seen_item_ids: set[str] = set()
    seen_item_urls: set[str] = set()
    items_list = data.get("items") if isinstance(data.get("items"), list) else []

    for idx, item in enumerate(items_list):
        if not isinstance(item, dict):
            continue
        label = f"items[{idx}]"
        iid = item.get("id")
        if isinstance(iid, str):
            if iid in seen_item_ids:
                errs.append(ValidationIssue(fstr, None, "tracking-duplicate-id", f"Duplicate item id `{iid}`"))
            seen_item_ids.add(iid)

        snap_name = item.get("snapshot")
        snap_meta = get_snap_meta(snap_name) if isinstance(snap_name, str) else None
        companions = snap_meta["companions"] if snap_meta else []
        allowed_prefixes = _allowed_repo_url_prefixes(forge, expected_repo, companions)

        # Calendar date checks on all item timestamps
        if item.get("filed_at") is not None and not _is_valid_calendar_iso(item.get("filed_at"), allow_datetime=True):
            errs.append(ValidationIssue(fstr, None, "tracking-invalid-date", f"`{label}.filed_at={item.get('filed_at')!r}` is not a valid calendar date/time"))

        mr = item.get("maintainer_response") if isinstance(item.get("maintainer_response"), dict) else {}
        rem = item.get("remediation") if isinstance(item.get("remediation"), dict) else {}
        obs = item.get("observed") if isinstance(item.get("observed"), dict) else {}
        for dkey, dval, allow_dt in (
            (f"{label}.maintainer_response.first_reply_at", mr.get("first_reply_at"), True),
            (f"{label}.maintainer_response.first_decision_at", mr.get("first_decision_at"), True),
            (f"{label}.remediation.merged_at", rem.get("merged_at"), True),
            (f"{label}.remediation.verified_at", rem.get("verified_at"), False),
            (f"{label}.observed.closed_at", obs.get("closed_at"), True),
        ):
            if dval is not None and not _is_valid_calendar_iso(dval, allow_datetime=allow_dt):
                errs.append(ValidationIssue(fstr, None, "tracking-invalid-date", f"`{dkey}={dval!r}` is not a valid calendar date/time"))

        # Canonical upstream URL uniqueness & scope
        item_url = item.get("url")
        norm_item_url: str | None = None
        if isinstance(item_url, str) and item_url.strip():
            norm_item_url = _normalize_canonical_url(item_url)
            if norm_item_url in seen_item_urls:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-duplicate-url",
                        f"`{label}.url` duplicates an already tracked upstream URL `{norm_item_url}`",
                        "Each upstream issue may appear only once per project tracking.json.",
                    )
                )
            seen_item_urls.add(norm_item_url)
            if not _url_belongs_to_prefixes(item_url, allowed_prefixes):
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-url-scope",
                        f"`{label}.url` `{item_url}` does not belong to `{forge}/{expected_repo}` (or declared companion repos)",
                    )
                )

        # Safe extraction of string finding IDs (avoid TypeError on schema-invalid entries)
        raw_fids = item.get("finding_ids") if isinstance(item.get("finding_ids"), list) else []
        str_fids = [f for f in raw_fids if isinstance(f, str)]
        raw_resolved = item.get("resolved_findings") if isinstance(item.get("resolved_findings"), list) else []
        str_resolved = [f for f in raw_resolved if isinstance(f, str)]

        if isinstance(snap_name, str):
            if snap_meta is None:
                errs.append(ValidationIssue(fstr, None, "tracking-snapshot-missing", f"`{label}.snapshot` `{snap_name}` not found in `{proj_dir.name}/`"))
            else:
                for fid in str_fids:
                    if fid not in snap_meta["fids"]:
                        errs.append(
                            ValidationIssue(
                                fstr,
                                None,
                                "tracking-finding-missing",
                                f"`{label}.finding_ids` references `{fid}` which does not exist in `{snap_name}` (found {sorted(snap_meta['fids'])})",
                            )
                        )

        # Origin attribution integrity against frozen snapshots & calendar dates
        origin = item.get("origin")
        if norm_item_url and norm_item_url in project_existing_urls and origin == "audit_filed":
            errs.append(
                ValidationIssue(
                    fstr,
                    None,
                    "tracking-origin-conflict",
                    f"`{label}` sets `origin: audit_filed`, contradicting frozen snapshot `existing` record for `{norm_item_url}`",
                    "Keep pre-existing community issues tagged `origin: pre_existing`.",
                )
            )
        if origin == "audit_filed" and snap_meta is not None:
            d_filed = parse_iso_date(item.get("filed_at"))
            d_audited = parse_iso_date(snap_meta["fm"].get("audited_at"))
            if d_filed is not None and d_audited is not None and (d_audited - d_filed).days > 1:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-origin-backdated",
                        f"`{label}` is `audit_filed` with `filed_at={item.get('filed_at')}`, predating snapshot `{snap_name}` (`audited_at={snap_meta['fm'].get('audited_at')}`)",
                        "Issues opened before the audit must use `origin: pre_existing`.",
                    )
                )

        fids_set = set(str_fids)
        resolved_set = set(str_resolved)
        for rf in str_resolved:
            if rf not in fids_set:
                errs.append(ValidationIssue(fstr, None, "tracking-resolved-subset", f"`{label}.resolved_findings` contains `{rf}` not in `finding_ids`"))

        status = item.get("status")

        pr_url = rem.get("pr_url")
        if isinstance(pr_url, str) and pr_url.strip():
            norm_pr = _normalize_canonical_url(pr_url)
            if not _url_belongs_to_prefixes(pr_url, allowed_prefixes) or not PR_PATH_RE.search(norm_pr):
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-pr-url-scope",
                        f"`{label}.remediation.pr_url` `{pr_url}` must point to a PR/MR on `{forge}/{expected_repo}` (or declared companion repo)",
                    )
                )

        # Validate resolution_evidence_url whenever provided (and require it on decline states)
        ev_url = item.get("resolution_evidence_url")
        if status in {"wontfix", "closed_unfixed"} and not _nonblank(ev_url):
            errs.append(
                ValidationIssue(
                    fstr,
                    None,
                    "tracking-decline-proof",
                    f"`{label}` has status `{status}` without `resolution_evidence_url`",
                    "Link the exact upstream closing comment, issue, PR, or commit URL on the tracked repository.",
                )
            )
        if _nonblank(ev_url):
            ev_norm = _normalize_canonical_url(str(ev_url))
            if not _url_belongs_to_prefixes(str(ev_url), allowed_prefixes) or not SPECIFIC_EVIDENCE_PATH_RE.search(ev_norm):
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-decline-proof",
                        f"`{label}.resolution_evidence_url` `{ev_url}` must point to a specific issue/PR/commit/discussion on `{forge}/{expected_repo}`",
                    )
                )

        orig_snap_sha = str(snap_meta["fm"].get("sha")) if (snap_meta and snap_meta["fm"].get("sha")) else None
        fix_commit = rem.get("fix_commit")
        has_valid_fix_sha = isinstance(fix_commit, str) and bool(SHA40_RE.fullmatch(fix_commit))

        # Strict proof gates for verified / partial fixes
        if status in {"verified_main", "verified_shipped", "partial_fix"}:
            if not has_valid_fix_sha:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-fix-proof",
                        f"`{label}` has status `{status}` but is missing 40-char `remediation.fix_commit`",
                        "Verify the upstream fix commit SHA before claiming a verified/partial fix.",
                    )
                )
            elif orig_snap_sha and fix_commit == orig_snap_sha:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-fix-proof",
                        f"`{label}.remediation.fix_commit` equals the broken audited snapshot SHA `{orig_snap_sha[:8]}`",
                    )
                )
            if not _nonblank(rem.get("verified_at")):
                errs.append(ValidationIssue(fstr, None, "tracking-fix-proof", f"`{label}` has status `{status}` without `remediation.verified_at`"))
            if not _nonblank(rem.get("verification_summary_en")) or not _nonblank(rem.get("verification_summary_zh")):
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-fix-proof",
                        f"`{label}` has status `{status}` without non-blank bilingual `verification_summary_en` and `verification_summary_zh`",
                    )
                )
            if not str_resolved:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-fix-proof",
                        f"`{label}` has status `{status}` with empty `resolved_findings`",
                        "List the exact finding IDs (e.g. [\"F2\"]) verified as resolved.",
                    )
                )
            elif status in {"verified_main", "verified_shipped"} and resolved_set != fids_set:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-fix-proof",
                        f"`{label}` has full-fix status `{status}` but only resolved {sorted(resolved_set)} of {sorted(fids_set)}",
                        "Use `partial_fix` when only a subset of tracked findings is resolved.",
                    )
                )

        vsnap_meta: dict[str, Any] | None = None
        if rem.get("verified_snapshot"):
            vsnap_name = str(rem["verified_snapshot"])
            vsnap_meta = get_snap_meta(vsnap_name)
            if vsnap_meta is None:
                errs.append(ValidationIssue(fstr, None, "tracking-snapshot-missing", f"`{label}.remediation.verified_snapshot` `{vsnap_name}` does not exist"))

        ev_kinds = {ev.get("kind") for ev in (item.get("events") or []) if isinstance(ev, dict)}
        needs_shipped_proof = status == "verified_shipped" or (status == "regressed" and "verified_shipped" in ev_kinds)
        if needs_shipped_proof:
            shipped_desc = "is `verified_shipped`" if status == "verified_shipped" else "declares a `verified_shipped` event"
            if not _nonblank(rem.get("fix_release")):
                errs.append(ValidationIssue(fstr, None, "tracking-shipped-proof", f"`{label}` {shipped_desc} without non-blank `remediation.fix_release`"))
            if not rem.get("verified_snapshot"):
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "tracking-shipped-proof",
                        f"`{label}` {shipped_desc} without `remediation.verified_snapshot`",
                        "Publish a newer immutable re-audit snapshot for the shipped release before declaring `verified_shipped`.",
                    )
                )
            else:
                vsnap_name = str(rem["verified_snapshot"])
                if snap_name and vsnap_name == snap_name:
                    errs.append(
                        ValidationIssue(
                            fstr,
                            None,
                            "tracking-shipped-proof",
                            f"`{label}.remediation.verified_snapshot` `{vsnap_name}` cannot be the same affected snapshot as `snapshot`",
                            "Create a new re-audit snapshot for the fixing release.",
                        )
                    )
                if vsnap_meta is not None:
                    vfm = vsnap_meta["fm"]
                    if orig_snap_sha and vfm.get("sha") == orig_snap_sha:
                        errs.append(
                            ValidationIssue(
                                fstr,
                                None,
                                "tracking-shipped-proof",
                                f"`{label}.remediation.verified_snapshot` has the same SHA as the affected snapshot",
                            )
                        )
                    if orig_snap_sha and not _supersedes_chain_reaches(vsnap_meta, orig_snap_sha, sha_to_meta):
                        errs.append(
                            ValidationIssue(
                                fstr,
                                None,
                                "tracking-shipped-proof",
                                f"`{label}.remediation.verified_snapshot` (`{vsnap_name}`) must supersede `{snap_name}` via `supersedes_sha`",
                            )
                        )
                    if snap_meta is not None:
                        d_orig = parse_iso_date(snap_meta["fm"].get("audited_at"))
                        d_new = parse_iso_date(vfm.get("audited_at"))
                        if d_orig and d_new and d_new < d_orig:
                            errs.append(
                                ValidationIssue(
                                    fstr,
                                    None,
                                    "tracking-shipped-proof",
                                    f"`{label}.remediation.verified_snapshot` (`{vfm.get('audited_at')}`) is older than affected snapshot (`{snap_meta['fm'].get('audited_at')}`)",
                                )
                            )
                    if _nonblank(rem.get("fix_release")) and str(vfm.get("release", "")).strip() != str(rem["fix_release"]).strip():
                        errs.append(
                            ValidationIssue(
                                fstr,
                                None,
                                "tracking-shipped-proof",
                                f"`{label}.remediation.fix_release` (`{rem['fix_release']}`) does not match `{vsnap_name}` release (`{vfm.get('release')}`)",
                            )
                        )

        if has_valid_fix_sha:
            cutoff_candidates = [d for d in (parse_iso_date(item.get("filed_at")), parse_iso_date(rem.get("merged_at"))) if d is not None]
            shifted = _safe_date_shift(min(cutoff_candidates), -14) if cutoff_candidates else None
            cutoff_str = shifted.isoformat() if shifted else None
            target_snap_sha = str(vsnap_meta["fm"].get("sha")) if (vsnap_meta and vsnap_meta["fm"].get("sha")) else None
            if status in {"verified_main", "verified_shipped", "partial_fix"}:
                mode = status
            elif status == "regressed" and "verified_shipped" in ev_kinds:
                mode = "verified_shipped"
            elif status == "regressed" and "verified_main" in ev_kinds:
                mode = "verified_main"
            else:
                mode = "exists_only"
            commit_checks.append(
                CommitCheck(
                    forge=forge,
                    repo=expected_repo,
                    sha=str(fix_commit),
                    ctx_label=f"{fstr}:{label}.remediation.fix_commit",
                    mode=mode,
                    orig_snap_sha=orig_snap_sha,
                    target_snap_sha=target_snap_sha,
                    fix_release=str(rem["fix_release"]).strip() if _nonblank(rem.get("fix_release")) else None,
                    cutoff_date=cutoff_str,
                )
            )

        for text_key, text_val in [
            (f"{label}.title_en", item.get("title_en")),
            (f"{label}.title_zh", item.get("title_zh")),
            (f"{label}.maintainer_response.summary_en", mr.get("summary_en")),
            (f"{label}.maintainer_response.summary_zh", mr.get("summary_zh")),
            (f"{label}.remediation.verification_summary_en", rem.get("verification_summary_en")),
            (f"{label}.remediation.verification_summary_zh", rem.get("verification_summary_zh")),
        ]:
            if isinstance(text_val, str) and text_val:
                errs.extend(_check_prose_hygiene(text_val, fstr, text_key))

        for e_idx, ev in enumerate(item.get("events") or []):
            if not isinstance(ev, dict):
                continue
            ev_label = f"{label}.events[{e_idx}]"
            if ev.get("date") is not None and not _is_valid_calendar_iso(ev.get("date"), allow_datetime=False):
                errs.append(ValidationIssue(fstr, None, "tracking-invalid-date", f"`{ev_label}.date={ev.get('date')!r}` is not a valid calendar date"))
            ekind = ev.get("kind")
            if ekind == "verified_main":
                if status not in {"verified_main", "verified_shipped", "regressed"} or not has_valid_fix_sha or ev.get("commit") != fix_commit:
                    errs.append(
                        ValidationIssue(
                            fstr,
                            None,
                            "tracking-event-proof",
                            f"`{ev_label}` declares `verified_main` without matching verified item status and `fix_commit`",
                        )
                    )
            elif ekind == "verified_shipped":
                if status not in {"verified_shipped", "regressed"} or not has_valid_fix_sha or ev.get("commit") != fix_commit:
                    errs.append(
                        ValidationIssue(
                            fstr,
                            None,
                            "tracking-event-proof",
                            f"`{ev_label}` declares `verified_shipped` without matching `verified_shipped` status and `fix_commit`",
                        )
                    )
            for ek in ("summary_en", "summary_zh"):
                if isinstance(ev.get(ek), str):
                    errs.extend(_check_prose_hygiene(ev[ek], fstr, f"{ev_label}.{ek}"))
            if ev.get("commit") and SHA40_RE.fullmatch(str(ev["commit"])):
                commit_checks.append(
                    CommitCheck(
                        forge=forge,
                        repo=expected_repo,
                        sha=str(ev["commit"]),
                        ctx_label=f"{fstr}:{ev_label}.commit",
                        mode="exists_only",
                    )
                )

    seen_pd_ids: set[str] = set()
    pds_list = data.get("private_disclosures") if isinstance(data.get("private_disclosures"), list) else []
    for idx, pd in enumerate(pds_list):
        if not isinstance(pd, dict):
            continue
        label = f"private_disclosures[{idx}]"
        pid = pd.get("id")
        if isinstance(pid, str):
            if pid in seen_pd_ids:
                errs.append(ValidationIssue(fstr, None, "tracking-duplicate-id", f"Duplicate private_disclosures id `{pid}`"))
            seen_pd_ids.add(pid)

        if pd.get("reported_at") is not None and not _is_valid_calendar_iso(pd.get("reported_at"), allow_datetime=False):
            errs.append(ValidationIssue(fstr, None, "tracking-invalid-date", f"`{label}.reported_at={pd.get('reported_at')!r}` is not a valid calendar date"))
        if pd.get("disclosed_at") is not None and not _is_valid_calendar_iso(pd.get("disclosed_at"), allow_datetime=False):
            errs.append(ValidationIssue(fstr, None, "tracking-invalid-date", f"`{label}.disclosed_at={pd.get('disclosed_at')!r}` is not a valid calendar date"))

        pd_snap_name = pd.get("snapshot")
        pd_snap_meta = get_snap_meta(pd_snap_name) if isinstance(pd_snap_name, str) else None
        if isinstance(pd_snap_name, str) and pd_snap_meta is None:
            errs.append(ValidationIssue(fstr, None, "tracking-snapshot-missing", f"`{label}.snapshot` `{pd_snap_name}` does not exist"))

        pst = pd.get("status")
        win = pd.get("window_days") if isinstance(pd.get("window_days"), int) else 90
        if pst == "embargoed":
            for forbidden_key in ("advisory_url", "fix_commit", "fix_release", "disclosed_at"):
                if pd.get(forbidden_key) is not None:
                    errs.append(
                        ValidationIssue(
                            fstr,
                            None,
                            "cvd-embargo-leak",
                            f"`{label}` is `embargoed` and must keep `{forbidden_key}` as null",
                            "Do not disclose advisory links, fix commits, or technical specifics while embargoed.",
                        )
                    )
            m_en = EMBARGO_EN_RE.fullmatch(str(pd.get("summary_en") or ""))
            m_zh = EMBARGO_ZH_RE.fullmatch(str(pd.get("summary_zh") or ""))
            if not m_en or int(m_en.group(2)) != win:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "cvd-embargo-leak",
                        f"`{label}.summary_en` must use the standardized neutral embargo notice for {win}-day window",
                        f'Use: "1 withheld security finding under {win}-day coordinated private disclosure (details omitted)."',
                    )
                )
            if not m_zh or int(m_zh.group(2)) != win:
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "cvd-embargo-leak",
                        f"`{label}.summary_zh` must use the standardized neutral embargo notice for {win}-day window",
                        f'Use: "1 项处于 {win} 天负责任私密披露期的暂隐安全问题（细节暂隐）。"',
                    )
                )
        elif pst == "disclosed_expired":
            if not _nonblank(pd.get("disclosed_at")):
                errs.append(ValidationIssue(fstr, None, "cvd-embargo-leak", f"`{label}` is `disclosed_expired` without `disclosed_at`"))
            else:
                d_rep = parse_iso_date(pd.get("reported_at"))
                d_disc = parse_iso_date(pd.get("disclosed_at"))
                if d_rep and d_disc:
                    expiry = _safe_date_shift(d_rep, win)
                    if expiry is None or d_disc < expiry:
                        exp_str = expiry.isoformat() if expiry else "out-of-range"
                        errs.append(
                            ValidationIssue(
                                fstr,
                                None,
                                "cvd-embargo-leak",
                                f"`{label}` is `disclosed_expired` at `{pd.get('disclosed_at')}`, before the {win}-day window expires on `{exp_str}`",
                            )
                        )
        elif pst == "disclosed_fixed":
            if not _nonblank(pd.get("disclosed_at")):
                errs.append(ValidationIssue(fstr, None, "cvd-disclosure-proof", f"`{label}` is `disclosed_fixed` without `disclosed_at`"))
            else:
                d_rep = parse_iso_date(pd.get("reported_at"))
                d_disc = parse_iso_date(pd.get("disclosed_at"))
                if d_rep and d_disc:
                    if d_disc < d_rep:
                        errs.append(
                            ValidationIssue(
                                fstr,
                                None,
                                "cvd-disclosure-proof",
                                f"`{label}.disclosed_at` (`{pd.get('disclosed_at')}`) predates `reported_at` (`{pd.get('reported_at')}`)",
                            )
                        )
                    expiry = _safe_date_shift(d_rep, win)
                    if (expiry is None or d_disc < expiry) and not _nonblank(pd.get("fix_release")):
                        errs.append(
                            ValidationIssue(
                                fstr,
                                None,
                                "cvd-embargo-leak",
                                f"`{label}` is `disclosed_fixed` before the {win}-day window ends without a shipped `fix_release`",
                                "Early disclosure before embargo expiry requires a shipped maintainer fix release (per DISCLOSURE.md).",
                            )
                        )
            if not (isinstance(pd.get("fix_commit"), str) and SHA40_RE.fullmatch(pd["fix_commit"])):
                errs.append(
                    ValidationIssue(
                        fstr,
                        None,
                        "cvd-disclosure-proof",
                        f"`{label}` is `disclosed_fixed` without a valid 40-char `fix_commit`",
                        "Provide the verified upstream fix commit SHA before awarding CVD fix credit.",
                    )
                )

        if pst != "embargoed" and isinstance(pd.get("fix_commit"), str) and SHA40_RE.fullmatch(pd["fix_commit"]):
            orig_pd_sha = str(pd_snap_meta["fm"].get("sha")) if (pd_snap_meta and pd_snap_meta["fm"].get("sha")) else None
            d_rep = parse_iso_date(pd.get("reported_at"))
            d_disc = parse_iso_date(pd.get("disclosed_at"))
            shifted_pd = _safe_date_shift(d_rep, -14) if d_rep else None
            cutoff_str = shifted_pd.isoformat() if shifted_pd else None
            pd_rel = str(pd["fix_release"]).strip() if _nonblank(pd.get("fix_release")) else None
            early_disclosed_at: str | None = None
            if pst == "disclosed_fixed":
                pd_mode = "cvd_shipped" if pd_rel else "verified_main"
                if d_rep and d_disc:
                    expiry = _safe_date_shift(d_rep, win)
                    if expiry is None or d_disc < expiry:
                        early_disclosed_at = d_disc.isoformat()
            else:
                pd_mode = "exists_only"
            commit_checks.append(
                CommitCheck(
                    forge=forge,
                    repo=expected_repo,
                    sha=str(pd["fix_commit"]),
                    ctx_label=f"{fstr}:{label}.fix_commit",
                    mode=pd_mode,
                    orig_snap_sha=orig_pd_sha,
                    fix_release=pd_rel,
                    cutoff_date=cutoff_str,
                    early_disclosed_at=early_disclosed_at,
                )
            )

        for sk in ("summary_en", "summary_zh"):
            if isinstance(pd.get(sk), str):
                errs.extend(_check_prose_hygiene(pd[sk], fstr, f"{label}.{sk}"))

    return errs, commit_checks


def _extract_positive_symbol_tokens(raw_line: str) -> list[str]:
    tokens: list[str] = []
    for m in INLINE_CODE_RE.finditer(raw_line):
        tok = m.group(1).strip()
        prefix = raw_line[max(0, m.start() - 32) : m.start()]
        if NEGATION_PREFIX_RE.search(prefix):
            continue
        if tok.endswith("()"):
            base = tok[:-2]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{2,50}", base):
                sym = base.split(".")[-1]
                if sym not in COMMON_NON_SYMBOLS:
                    tokens.append(sym)
            continue
        if "/" in tok or ":" in tok or "=" in tok or " " in tok or tok.startswith("-"):
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{3,45}", tok) and "_" in tok:
            if tok not in COMMON_NON_SYMBOLS and not re.fullmatch(r"F\d+", tok):
                tokens.append(tok)
    return tokens


def verify_against_repo_map(
    repo_sources: dict[str, tuple[Path, str | None]],
    rel_path: Path,
    citations: list[Citation],
    excerpts: list[ExcerptBlock],
) -> tuple[list[ValidationIssue], int]:
    """Verify citations/excerpts against `(repo_dir, pinned_sha_for_git_show_or_None)`."""
    errs: list[ValidationIssue] = []
    fstr = rel_path.as_posix()
    file_cache: dict[tuple[str, str], list[str] | None] = {}
    checked = 0

    def load_lines(tkey: str, fpath: str) -> list[str] | None:
        ckey = (tkey, fpath)
        if ckey in file_cache:
            return file_cache[ckey]
        src = repo_sources.get(tkey)
        if src is None:
            file_cache[ckey] = None
            return None
        rdir, pinned_sha = src
        clean_path = fpath.lstrip("/")
        if ".." in Path(clean_path).parts:
            file_cache[ckey] = None
            return None
        if pinned_sha:
            proc = subprocess.run(
                ["git", "-C", str(rdir), "show", f"{pinned_sha}:{clean_path}"],
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0:
                file_cache[ckey] = proc.stdout.decode("utf-8", errors="replace").splitlines()
            else:
                file_cache[ckey] = None
            return file_cache[ckey]
        full = (rdir / clean_path).resolve()
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

    per_line_targets: dict[int, set[tuple[str, str, int, int]]] = {}
    for c in citations:
        per_line_targets.setdefault(c.body_line, set()).add((c.target_key, c.fpath, c.start_line, c.end_line))

    seen_checked: set[tuple[int, str, str, int, int]] = set()
    for c in citations:
        if c.target_key not in repo_sources:
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

    for ex in excerpts:
        if ex.target_key not in repo_sources:
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


def _git_is_ancestor_checked(repo_dir: Path, ancestor: str, descendant: str) -> bool | None:
    """Return True if ancestor, False if strictly not ancestor, or None on git error."""
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def _git_is_ancestor(repo_dir: Path, ancestor: str, descendant: str) -> bool:
    return _git_is_ancestor_checked(repo_dir, ancestor, descendant) is True


def _git_has_commit(repo_dir: Path, sha: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _git_is_shallow_checked(repo_dir: Path) -> bool | None:
    """Return True if shallow, False if complete, or None if rev-parse fails."""
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    val = proc.stdout.strip()
    if val == "true":
        return True
    if val == "false":
        return False
    return None


def _parse_git_iso_utc_date(raw: str) -> dt.date | None:
    s = raw.strip()
    if not s:
        return None
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(dt.timezone.utc).date()
    return parsed.date()


def _resolve_tag_date_utc(repo_dir: Path, tag_ref: str, tag_sha: str) -> dt.date | None:
    """Return latest UTC date between tagger/creator date of tag_ref and committer date of tag_sha."""
    creator_proc = subprocess.run(
        ["git", "-C", str(repo_dir), "for-each-ref", "--format=%(creatordate:iso-strict)", tag_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if creator_proc.returncode != 0:
        return None
    creator_date = _parse_git_iso_utc_date(creator_proc.stdout)
    commit_proc = subprocess.run(
        ["git", "-C", str(repo_dir), "show", "-s", "--format=%cI", tag_sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit_proc.returncode != 0:
        return None
    commit_date = _parse_git_iso_utc_date(commit_proc.stdout)
    if creator_date is None or commit_date is None:
        return None
    return max(creator_date, commit_date)


def _check_early_cvd_release_date(chk: CommitCheck, tag_date: dt.date | None) -> ValidationIssue | None:
    if chk.mode != "cvd_shipped" or not chk.early_disclosed_at:
        return None
    d_disc = parse_iso_date(chk.early_disclosed_at)
    if d_disc is None or tag_date is None:
        return ValidationIssue(
            chk.ctx_label,
            None,
            "cvd-embargo-leak",
            f"Could not verify release date for `{chk.fix_release}` prior to early disclosure `{chk.early_disclosed_at}`",
        )
    if tag_date > d_disc:
        return ValidationIssue(
            chk.ctx_label,
            None,
            "cvd-embargo-leak",
            f"Release tag `{chk.fix_release}` date (`{tag_date.isoformat()}`) postdates early disclosure `{chk.early_disclosed_at}`",
            "Early CVD disclosure requires the maintainer release to have shipped on or before `disclosed_at`.",
        )
    return None


def _candidate_release_tags(fix_release: str) -> list[str]:
    rel = fix_release.strip()
    cands = [rel]
    if rel.startswith("v"):
        cands.append(rel[1:])
    else:
        cands.append(f"v{rel}")
    return cands


def _resolve_local_release_tag(repo_dir: Path, fix_release: str) -> tuple[str, dt.date | None] | None:
    for tag in _candidate_release_tags(fix_release):
        tag_ref = f"refs/tags/{tag}"
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "-q", "--verify", f"{tag_ref}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and SHA40_RE.fullmatch(proc.stdout.strip()):
            tag_sha = proc.stdout.strip()
            return tag_sha, _resolve_tag_date_utc(repo_dir, tag_ref, tag_sha)
    return None


def _resolve_upstream_release_tag(
    repo_dir: Path,
    base_flags: list[str],
    fix_release: str,
    cutoff_date: str | None,
) -> tuple[str, dt.date | None] | None:
    for tag in _candidate_release_tags(fix_release):
        tag_ref = f"refs/tags/{tag}"
        fetch_args = ["git", *base_flags, "-C", str(repo_dir), "fetch", "--filter=tree:0"]
        if cutoff_date:
            fetch_args.append(f"--shallow-since={cutoff_date}")
        fetch_args.extend(["origin", f"+{tag_ref}:{tag_ref}"])
        proc = subprocess.run(fetch_args, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0 and cutoff_date and "couldn't find remote ref" not in (proc.stderr or "").lower():
            retry_args = ["git", *base_flags, "-C", str(repo_dir), "fetch", "--filter=tree:0", "origin", f"+{tag_ref}:{tag_ref}"]
            proc = subprocess.run(retry_args, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode == 0:
            rev = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "-q", "--verify", f"{tag_ref}^{{commit}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if rev.returncode == 0 and SHA40_RE.fullmatch(rev.stdout.strip()):
                tag_sha = rev.stdout.strip()
                return tag_sha, _resolve_tag_date_utc(repo_dir, tag_ref, tag_sha)
    return None


def verify_commit_in_local_repo(repo_dir: Path, chk: CommitCheck) -> list[ValidationIssue]:
    errs: list[ValidationIssue] = []
    if not _git_has_commit(repo_dir, chk.sha):
        errs.append(ValidationIssue(chk.ctx_label, None, "fix-commit-missing", f"Claimed commit `{chk.sha}` not found in local repo `{chk.repo}`"))
        return errs

    if chk.mode == "exists_only":
        return errs

    # Fail-closed pre-audit ancestry verification
    if not chk.orig_snap_sha:
        errs.append(
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-orig-missing",
                f"Original audited snapshot SHA missing for `{chk.repo}`; cannot prove fix postdates audit",
            )
        )
        return errs
    if not _git_has_commit(repo_dir, chk.orig_snap_sha):
        errs.append(
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-orig-missing",
                f"Original audited SHA `{chk.orig_snap_sha[:8]}` not found in local repo `{chk.repo}`; cannot prove fix postdates audit",
            )
        )
        return errs
    if _git_is_shallow_checked(repo_dir) is not False:
        errs.append(
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-orig-missing",
                f"Local repo `{chk.repo}` is shallow or failed shallowness check; refusing pre-audit ancestry check",
            )
        )
        return errs
    if chk.sha == chk.orig_snap_sha:
        errs.append(
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-predates-audit",
                f"Claimed fix commit `{chk.sha[:8]}` is already an ancestor of broken audited SHA `{chk.orig_snap_sha[:8]}`",
            )
        )
        return errs
    pre_audit = _git_is_ancestor_checked(repo_dir, chk.sha, chk.orig_snap_sha)
    if pre_audit is None:
        errs.append(
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-orig-missing",
                f"Git ancestry check failed between `{chk.sha[:8]}` and `{chk.orig_snap_sha[:8]}` in local repo `{chk.repo}`",
            )
        )
        return errs
    if pre_audit:
        errs.append(
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-predates-audit",
                f"Claimed fix commit `{chk.sha[:8]}` is already an ancestor of broken audited SHA `{chk.orig_snap_sha[:8]}`",
            )
        )
        return errs

    if chk.mode in {"verified_shipped", "cvd_shipped"}:
        if chk.mode == "verified_shipped":
            if not chk.target_snap_sha or not _git_has_commit(repo_dir, chk.target_snap_sha):
                errs.append(ValidationIssue(chk.ctx_label, None, "fix-commit-not-in-release", f"Re-audit snapshot SHA `{chk.target_snap_sha}` not found in `{chk.repo}`"))
                return errs
            if not _git_is_ancestor(repo_dir, chk.sha, chk.target_snap_sha):
                errs.append(
                    ValidationIssue(
                        chk.ctx_label,
                        None,
                        "fix-commit-not-in-release",
                        f"Claimed fix commit `{chk.sha[:8]}` is NOT an ancestor of shipped re-audit SHA `{chk.target_snap_sha[:8]}`",
                    )
                )
                return errs
        if not chk.fix_release:
            errs.append(
                ValidationIssue(
                    chk.ctx_label,
                    None,
                    "fix-release-tag-missing",
                    f"Missing declared `fix_release` for `{chk.mode}` verification in `{chk.repo}`",
                )
            )
            return errs
        tag_res = _resolve_local_release_tag(repo_dir, chk.fix_release)
        if not tag_res:
            errs.append(
                ValidationIssue(
                    chk.ctx_label,
                    None,
                    "fix-release-tag-missing",
                    f"Declared release tag `{chk.fix_release}` not found in local repo `{chk.repo}`",
                )
            )
        else:
            tag_sha, tag_date = tag_res
            early_err = _check_early_cvd_release_date(chk, tag_date)
            if early_err is not None:
                errs.append(early_err)
            if not _git_is_ancestor(repo_dir, chk.sha, tag_sha):
                errs.append(
                    ValidationIssue(
                        chk.ctx_label,
                        None,
                        "fix-commit-not-in-release",
                        f"Claimed fix commit `{chk.sha[:8]}` is NOT an ancestor of release tag `{chk.fix_release}` (`{tag_sha[:8]}`)",
                    )
                )
            elif chk.mode == "verified_shipped" and chk.target_snap_sha and tag_sha != chk.target_snap_sha and not _git_is_ancestor(repo_dir, tag_sha, chk.target_snap_sha):
                errs.append(
                    ValidationIssue(
                        chk.ctx_label,
                        None,
                        "fix-commit-not-in-release",
                        f"Re-audit snapshot SHA `{chk.target_snap_sha[:8]}` does not match or descend from release tag `{chk.fix_release}` (`{tag_sha[:8]}`)",
                    )
                )
    elif chk.mode in {"verified_main", "partial_fix"}:
        if not _git_is_ancestor(repo_dir, chk.sha, "HEAD"):
            errs.append(
                ValidationIssue(
                    chk.ctx_label,
                    None,
                    "fix-commit-not-on-default-branch",
                    f"Claimed fix commit `{chk.sha[:8]}` is NOT an ancestor of default branch `HEAD` in `{chk.repo}`",
                )
            )
    return errs


def verify_commit_in_upstream_repo(dest: Path, chk: CommitCheck) -> list[ValidationIssue]:
    """Commit-only (`--filter=tree:0`) inert verification of existence, release tags, and ancestry (fail-closed)."""
    url = f"{forge_base_url(chk.forge)}/{chk.repo}.git"
    dest.mkdir(parents=True, exist_ok=True)
    base_flags = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false"]

    if not (dest / ".git").exists():
        for cmd in (
            ["git", *base_flags, "-C", str(dest), "init", "-q"],
            ["git", *base_flags, "-C", str(dest), "remote", "add", "origin", url],
        ):
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
            if proc.returncode != 0:
                return [ValidationIssue(chk.ctx_label, None, "fix-commit-missing", f"Git init failed for `{chk.repo}`: {proc.stderr.strip()[:200]}")]

    if chk.mode == "exists_only":
        if not _git_has_commit(dest, chk.sha):
            proc = subprocess.run(
                ["git", *base_flags, "-C", str(dest), "fetch", "--depth=1", "--filter=tree:0", "origin", chk.sha],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if proc.returncode != 0 or not _git_has_commit(dest, chk.sha):
                return [ValidationIssue(chk.ctx_label, None, "fix-commit-missing", f"Claimed commit `{chk.sha}` not found in `{chk.forge}/{chk.repo}`")]
        return []

    target_sha: str | None = None
    target_label = "HEAD"

    if chk.mode == "cvd_shipped":
        if not chk.fix_release:
            return [ValidationIssue(chk.ctx_label, None, "fix-release-tag-missing", f"Missing declared `fix_release` for `cvd_shipped` in `{chk.forge}/{chk.repo}`")]
        target_label = f"tag {chk.fix_release}"
        tag_res = _resolve_upstream_release_tag(dest, base_flags, chk.fix_release, chk.cutoff_date)
        if not tag_res:
            return [ValidationIssue(chk.ctx_label, None, "fix-release-tag-missing", f"Declared release tag `{chk.fix_release}` not found in `{chk.forge}/{chk.repo}`")]
        target_sha, tag_date = tag_res
        early_err = _check_early_cvd_release_date(chk, tag_date)
        if early_err is not None:
            return [early_err]
    else:
        if chk.mode == "verified_shipped":
            if not chk.target_snap_sha:
                return [ValidationIssue(chk.ctx_label, None, "fix-commit-not-in-release", f"Missing `verified_snapshot` SHA for `verified_shipped` in `{chk.forge}/{chk.repo}`")]
            if not chk.fix_release:
                return [ValidationIssue(chk.ctx_label, None, "fix-release-tag-missing", f"Missing declared `fix_release` for `verified_shipped` in `{chk.forge}/{chk.repo}`")]
            target_ref = chk.target_snap_sha
        else:
            target_ref = "HEAD"
        target_label = target_ref
        fetch_args = ["git", *base_flags, "-C", str(dest), "fetch", "--filter=tree:0"]
        if chk.cutoff_date:
            fetch_args.append(f"--shallow-since={chk.cutoff_date}")
        fetch_args.extend(["origin", target_ref])
        proc = subprocess.run(fetch_args, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0:
            return [ValidationIssue(chk.ctx_label, None, "fix-commit-missing", f"Failed fetching `{target_ref}` from `{chk.repo}`: {proc.stderr.strip()[:200]}")]
        rev_proc = subprocess.run(["git", "-C", str(dest), "rev-parse", "FETCH_HEAD"], capture_output=True, text=True, check=False)
        target_sha = rev_proc.stdout.strip()
        if rev_proc.returncode != 0 or not SHA40_RE.fullmatch(target_sha):
            return [ValidationIssue(chk.ctx_label, None, "fix-commit-missing", f"Could not resolve fetched `{target_ref}` SHA in `{chk.repo}`")]

    if not _git_has_commit(dest, chk.sha):
        subprocess.run(
            ["git", *base_flags, "-C", str(dest), "fetch", "--unshallow", "--filter=tree:0", "origin", target_sha],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    if not _git_has_commit(dest, chk.sha):
        p2 = subprocess.run(
            ["git", *base_flags, "-C", str(dest), "fetch", "--depth=1", "--filter=tree:0", "origin", chk.sha],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if p2.returncode == 0 and _git_has_commit(dest, chk.sha):
            rule = "fix-commit-not-in-release" if chk.mode in {"verified_shipped", "cvd_shipped"} else "fix-commit-not-on-default-branch"
            return [ValidationIssue(chk.ctx_label, None, rule, f"Claimed fix commit `{chk.sha[:8]}` exists in `{chk.repo}` but is NOT an ancestor of `{target_label}`")]
        return [ValidationIssue(chk.ctx_label, None, "fix-commit-missing", f"Claimed fix commit `{chk.sha}` not found in `{chk.forge}/{chk.repo}`")]

    if not _git_is_ancestor(dest, chk.sha, target_sha):
        rule = "fix-commit-not-in-release" if chk.mode in {"verified_shipped", "cvd_shipped"} else "fix-commit-not-on-default-branch"
        return [ValidationIssue(chk.ctx_label, None, rule, f"Claimed fix commit `{chk.sha[:8]}` is NOT an ancestor of `{target_label}`")]

    # For verified_shipped, also resolve & verify upstream release tag matches/precedes target snapshot and contains fix_commit
    if chk.mode == "verified_shipped":
        tag_res = _resolve_upstream_release_tag(dest, base_flags, str(chk.fix_release), chk.cutoff_date)
        if not tag_res:
            return [ValidationIssue(chk.ctx_label, None, "fix-release-tag-missing", f"Declared release tag `{chk.fix_release}` not found in `{chk.forge}/{chk.repo}`")]
        tag_sha, _ = tag_res
        if not _git_is_ancestor(dest, chk.sha, tag_sha):
            return [ValidationIssue(chk.ctx_label, None, "fix-commit-not-in-release", f"Claimed fix commit `{chk.sha[:8]}` is NOT an ancestor of release tag `{chk.fix_release}`")]
        if tag_sha != target_sha and not _git_is_ancestor(dest, tag_sha, target_sha):
            return [ValidationIssue(chk.ctx_label, None, "fix-commit-not-in-release", f"Re-audit snapshot `{target_sha[:8]}` does not match or descend from release tag `{chk.fix_release}` (`{tag_sha[:8]}`)")]

    # Fail-closed pre-audit ancestry check (never use --depth=1 on orig_snap_sha, which would sever its parents!)
    if not chk.orig_snap_sha:
        return [
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-orig-missing",
                f"Original audited snapshot SHA missing for `{chk.forge}/{chk.repo}`; cannot prove fix postdates audit",
            )
        ]
    if chk.sha == chk.orig_snap_sha:
        return [ValidationIssue(chk.ctx_label, None, "fix-commit-predates-audit", f"Claimed fix commit `{chk.sha[:8]}` equals broken audited SHA")]
    if not _git_has_commit(dest, chk.orig_snap_sha):
        orig_fetch = ["git", *base_flags, "-C", str(dest), "fetch", "--filter=tree:0"]
        if chk.cutoff_date:
            orig_fetch.append(f"--shallow-since={chk.cutoff_date}")
        orig_fetch.extend(["origin", chk.orig_snap_sha])
        subprocess.run(orig_fetch, capture_output=True, text=True, timeout=180, check=False)
    if not _git_has_commit(dest, chk.orig_snap_sha):
        return [
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-orig-missing",
                f"Original audited SHA `{chk.orig_snap_sha[:8]}` could not be fetched from `{chk.forge}/{chk.repo}`; cannot prove fix postdates audit",
            )
        ]
    # Fail closed if shallowness cannot be determined, or if unshallowing fails to produce complete history
    shallow_state = _git_is_shallow_checked(dest)
    if shallow_state is None:
        return [
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-orig-missing",
                f"Could not determine repository shallowness for `{chk.orig_snap_sha[:8]}` in `{chk.forge}/{chk.repo}`; refusing pre-audit check",
            )
        ]
    if shallow_state is True:
        unsh = subprocess.run(
            ["git", *base_flags, "-C", str(dest), "fetch", "--unshallow", "--filter=tree:0", "origin", chk.orig_snap_sha],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        still_shallow = _git_is_shallow_checked(dest)
        if unsh.returncode != 0 or still_shallow is not False:
            return [
                ValidationIssue(
                    chk.ctx_label,
                    None,
                    "fix-commit-orig-missing",
                    f"Could not obtain complete commit ancestry for `{chk.orig_snap_sha[:8]}` in `{chk.forge}/{chk.repo}`; refusing shallow pre-audit check",
                )
            ]
    pre_audit = _git_is_ancestor_checked(dest, chk.sha, chk.orig_snap_sha)
    if pre_audit is None:
        return [
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-orig-missing",
                f"Git ancestry check failed between `{chk.sha[:8]}` and `{chk.orig_snap_sha[:8]}` in `{chk.forge}/{chk.repo}`",
            )
        ]
    if pre_audit:
        return [
            ValidationIssue(
                chk.ctx_label,
                None,
                "fix-commit-predates-audit",
                f"Claimed fix commit `{chk.sha[:8]}` is already an ancestor of broken audited SHA `{chk.orig_snap_sha[:8]}`",
            )
        ]
    return []


def parse_git_diff_z(stdout: str) -> list[tuple[str, str]]:
    """Parse `git diff --no-renames --name-status -z` NUL-delimited status/path pairs."""
    tokens = [t for t in stdout.split("\0") if t]
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        if status[:1] in {"R", "C"} and i + 2 < len(tokens):
            out.append(("D", tokens[i + 1]))
            out.append(("A", tokens[i + 2]))
            i += 3
        elif i + 1 < len(tokens):
            out.append((status[:1], tokens[i + 1]))
            i += 2
        else:
            break
    return out


def check_pr_scope(
    base_ref: str,
    root_dir: Path = ROOT,
    allow_mixed: bool = False,
    allow_correction: bool = False,
) -> list[ValidationIssue]:
    proc = subprocess.run(
        ["git", "-C", str(root_dir), "diff", "--no-renames", "--name-status", "-z", f"{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return [ValidationIssue("git", None, "pr-scope-git-error", f"Could not diff against `{base_ref}`: {proc.stderr.strip()}")]

    changed = parse_git_diff_z(proc.stdout)
    touches_audits = any(p.startswith("audits/") for _, p in changed)
    errs: list[ValidationIssue] = []
    protected_prefixes = ("tools/", ".github/", "data/schema/")

    for status, path in changed:
        if path.startswith("audits/"):
            fname = Path(path).name
            if SNAPSHOT_RE.match(fname) or TRANSLATION_RE.match(fname):
                if status == "A":
                    pass
                elif status == "M" and allow_correction:
                    pass
                else:
                    errs.append(
                        ValidationIssue(
                            path,
                            None,
                            "pr-scope-immutable-snapshot",
                            f"PR performs disallowed `{status}` operation on frozen snapshot `{path}`",
                            "Frozen snapshots may never be deleted, renamed, or overwritten; update `tracking.json` or add a new `YYYY-MM-DD-<newsha>.md` snapshot.",
                        )
                    )

        if touches_audits and not allow_mixed and path.startswith(protected_prefixes):
            errs.append(
                ValidationIssue(
                    path,
                    None,
                    "pr-scope-protected",
                    f"Audit PR modifies protected path `{path}`",
                    "Split tooling/CI/schema changes into a dedicated PR separate from audit snapshots.",
                )
            )
    return errs


def validate_repo(
    targets: list[Path],
    root_dir: Path = ROOT,
    verify_upstream: bool = False,
    local_map: dict[str, Path] | None = None,
    tracking_targets: list[Path] | None = None,
) -> tuple[list[ValidationIssue], int, int, int, int, int]:
    """Validate snapshots, translations, and tracking.json files.

    Returns (errors, snaps_checked, trans_checked, citations_verified, tracking_checked, fix_commits_verified).
    """
    local_map = local_map or {}
    audits_dir = root_dir / "audits"
    schema = load_schema(root_dir / "data" / "schema" / "audit-frontmatter.schema.json")
    tschema_path = root_dir / "data" / "schema" / "tracking.schema.json"

    all_errors: list[ValidationIssue] = []
    total_citations = 0
    snaps_checked = 0
    trans_checked = 0
    tracking_checked = 0
    fix_commits_verified = 0

    parsed_docs: list[tuple[Path, str, dict[str, Any], list[Citation], list[ExcerptBlock]]] = []
    wanted_by_repo_key: dict[tuple[str, str, str], set[str]] = {}
    tracking_commit_checks: list[CommitCheck] = []

    snap_paths: list[Path] = []
    t_paths: list[Path] = list(tracking_targets or [])
    for p in targets:
        if p.name == "tracking.json":
            if p not in t_paths:
                t_paths.append(p)
        else:
            snap_paths.append(p)

    for path in snap_paths:
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

    if t_paths:
        if not tschema_path.is_file():
            all_errors.append(
                ValidationIssue(
                    "data/schema/tracking.schema.json",
                    None,
                    "tracking-schema-missing",
                    f"Missing tracking schema required to validate {len(t_paths)} tracking.json file(s)",
                )
            )
        else:
            tracking_schema = load_schema(tschema_path)
            for tpath in t_paths:
                tracking_checked += 1
                t_errs, t_checks = validate_tracking_file(tpath, audits_dir=audits_dir, tracking_schema=tracking_schema)
                all_errors.extend(t_errs)
                tracking_commit_checks.extend(t_checks)

    cm = tempfile.TemporaryDirectory(prefix="tnr-verify-") if verify_upstream else contextlib.nullcontext(None)
    with cm as td:
        fetched_cache: dict[tuple[str, str, str], Path] = {}

        def resolve_repo_source(forge: str, slug: str, sha: str, alias: str | None, rel_str: str) -> tuple[Path, str | None] | None:
            for lookup in ([alias] if alias else []) + [slug]:
                if lookup in local_map:
                    cand = local_map[lookup]
                    if not SHA40_RE.fullmatch(sha) or not _git_has_commit(cand, sha):
                        all_errors.append(
                            ValidationIssue(rel_str, None, "local-sha-missing", f"Local repo `{lookup}` does not contain pinned commit `{sha}`")
                        )
                        return None
                    # Read exact pinned commit via `git show <sha>:<path>` so HEAD can remain default branch tip
                    return (cand, sha)
            if verify_upstream and td is not None and SHA40_RE.fullmatch(sha):
                key = (forge, slug, sha)
                if key not in fetched_cache:
                    dest = Path(td) / f"{forge}_{slug.replace('/', '__')}_{sha[:8]}"
                    ok, err = inert_fetch_sha(forge, slug, sha, dest, wanted_paths=wanted_by_repo_key.get(key))
                    if not ok:
                        all_errors.append(
                            ValidationIssue(rel_str, None, "upstream-fetch-failed", f"Failed to fetch {forge}/{slug}@{sha}: {err}")
                        )
                        return None
                    fetched_cache[key] = dest
                return (fetched_cache[key], None)
            return None

        for rel, rel_str, fm, citations, excerpts in parsed_docs:
            repo_sources: dict[str, tuple[Path, str | None]] = {}
            slug = str(fm.get("repo", ""))
            sha = str(fm.get("sha", ""))
            forge = str(fm.get("forge", "github"))
            if verify_upstream or slug in local_map:
                rsrc = resolve_repo_source(forge, slug, sha, None, rel_str)
                if rsrc is not None:
                    repo_sources["__main__"] = rsrc
            for comp in parse_companion_repos(fm.get("companion_repos")):
                if verify_upstream or comp["alias"] in local_map or comp["repo"] in local_map:
                    csrc = resolve_repo_source(comp["forge"], comp["repo"], comp["sha"], comp["alias"], rel_str)
                    if csrc is not None:
                        repo_sources[comp["alias"]] = csrc

            if repo_sources:
                c_errs, c_count = verify_against_repo_map(repo_sources, rel, citations, excerpts)
                all_errors.extend(c_errs)
                total_citations += c_count

        # Mechanically verify every claimed fix commit & ancestry relationship (strongest checks first)
        checked_commit_keys: set[tuple[str, str, str, str, str | None, str | None, str | None, str | None]] = set()
        verified_commit_shas: set[tuple[str, str, str]] = set()
        ordered_checks = sorted(tracking_commit_checks, key=lambda c: (c.mode == "exists_only", c.forge, c.repo, c.sha))
        for chk in ordered_checks:
            sha_key = (chk.forge, chk.repo, chk.sha)
            if chk.mode == "exists_only" and sha_key in verified_commit_shas:
                continue
            dedup = (chk.forge, chk.repo, chk.sha, chk.mode, chk.orig_snap_sha, chk.target_snap_sha, chk.fix_release, chk.early_disclosed_at)
            if dedup in checked_commit_keys:
                continue
            if chk.repo in local_map:
                checked_commit_keys.add(dedup)
                c_errs = verify_commit_in_local_repo(local_map[chk.repo], chk)
                all_errors.extend(c_errs)
                if not c_errs:
                    if sha_key not in verified_commit_shas:
                        fix_commits_verified += 1
                    verified_commit_shas.add(sha_key)
            elif verify_upstream and td is not None:
                checked_commit_keys.add(dedup)
                key_hash = hashlib.sha256(f"{chk.forge}\0{chk.repo}".encode("utf-8")).hexdigest()[:12]
                dest = Path(td) / f"fixgraph_{chk.forge}_{key_hash}"
                c_errs = verify_commit_in_upstream_repo(dest, chk)
                all_errors.extend(c_errs)
                if not c_errs:
                    if sha_key not in verified_commit_shas:
                        fix_commits_verified += 1
                    verified_commit_shas.add(sha_key)

    return all_errors, snaps_checked, trans_checked, total_citations, tracking_checked, fix_commits_verified


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate trust-no-readme audit snapshots, translations & tracking.json.")
    ap.add_argument("--verify-upstream", action="store_true", help="Fetch pinned upstream/companion SHAs and fix commits read-only")
    ap.add_argument("--local-repo", action="append", default=[], help="Map owner/repo=/abs/path or alias=/abs/path for offline verification")
    ap.add_argument("--format", choices=["text", "json"], default="text", help="Output format (use json for agent self-repair)")
    ap.add_argument("--check-pr-scope", metavar="BASE_REF", help="Check PR diff against BASE_REF for protected paths / immutable snapshots")
    ap.add_argument("--allow-mixed-pr", action="store_true", help="Allow tooling/schema edits alongside audits (never weakens snapshot immutability)")
    ap.add_argument("--allow-correction", action="store_true", help="Allow in-place edits (`M`) to existing snapshots for confirmed factual corrections (deletions still blocked)")
    ap.add_argument("files", nargs="*", help="Optional subset of snapshot/translation/tracking files to validate")
    args = ap.parse_args()

    local_map: dict[str, Path] = {}
    for entry in args.local_repo:
        if "=" not in entry:
            ap.error("--local-repo expects owner/repo=/path or alias=/path")
        k, v = entry.split("=", 1)
        local_map[k.strip()] = Path(v.strip()).resolve()

    if args.check_pr_scope:
        scope_errs = check_pr_scope(
            args.check_pr_scope,
            root_dir=ROOT,
            allow_mixed=args.allow_mixed_pr,
            allow_correction=args.allow_correction,
        )
        if args.format == "json":
            print(json.dumps({"ok": not scope_errs, "errors": [e.to_dict() for e in scope_errs]}, indent=2, ensure_ascii=False))
        else:
            for e in scope_errs:
                print(e.format_text(), file=sys.stderr)
            if not scope_errs:
                print(f"OK pr-scope: clean against {args.check_pr_scope}.")
        return 1 if scope_errs else 0

    targets: list[Path] = []
    tracking_targets: list[Path] = []
    if args.files:
        for f in args.files:
            p = Path(f).resolve()
            if p.name == "tracking.json":
                tracking_targets.append(p)
            else:
                targets.append(p)
    elif AUDITS_DIR.is_dir():
        for p in sorted(AUDITS_DIR.rglob("*.md")):
            if p.name not in {"README.md", "responses.md"}:
                targets.append(p)
        for tp in sorted(AUDITS_DIR.rglob("tracking.json")):
            tracking_targets.append(tp)

    all_errors, snaps_checked, trans_checked, total_citations, tracking_checked, fix_commits_verified = validate_repo(
        targets,
        root_dir=ROOT,
        verify_upstream=args.verify_upstream,
        local_map=local_map,
        tracking_targets=tracking_targets,
    )

    if args.format == "json":
        payload = {
            "ok": not all_errors,
            "snapshots_checked": snaps_checked,
            "translations_checked": trans_checked,
            "tracking_files_checked": tracking_checked,
            "citations_verified": total_citations,
            "fix_commits_verified": fix_commits_verified,
            "error_count": len(all_errors),
            "errors": [e.to_dict() for e in all_errors],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if all_errors else 0

    if all_errors:
        for e in all_errors:
            print(e.format_text(), file=sys.stderr)
        print(
            f"FAILED validate: {len(all_errors)} error(s) across {snaps_checked} snapshot(s), "
            f"{trans_checked} translation(s), and {tracking_checked} tracking file(s).",
            file=sys.stderr,
        )
        return 1

    extras: list[str] = []
    if total_citations:
        extras.append(f"{total_citations} upstream citation(s)")
    if fix_commits_verified:
        extras.append(f"{fix_commits_verified} fix commit(s)")
    extra_str = f", {' and '.join(extras)} mechanically verified." if extras else "."
    print(
        f"OK validate: {snaps_checked} snapshot(s), {trans_checked} translation(s), and "
        f"{tracking_checked} tracking file(s) valid{extra_str}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
