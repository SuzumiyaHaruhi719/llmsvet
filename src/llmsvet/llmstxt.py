"""Parser and validator for the llmstxt.org (/llms.txt & /llms-full.txt) specification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from llmsvet.models import Finding

LINK_LINE_RE = re.compile(r"^-\s+\[([^\]]+)\]\(([^)]+)\)(?:\s*:\s*(.*))?$")


@dataclass
class LlmsLink:
    section: str
    title: str
    url: str
    description: str = ""
    line_no: int = 0


@dataclass
class LlmsTxtResult:
    exists: bool
    has_full: bool = False
    h1_title: Optional[str] = None
    blockquote_summary: Optional[str] = None
    sections: List[str] = field(default_factory=list)
    links: List[LlmsLink] = field(default_factory=list)
    estimated_tokens: int = 0

    def to_meta(self) -> Dict[str, Any]:
        return {
            "exists": self.exists,
            "llms_full_exists": self.has_full,
            "h1_title": self.h1_title,
            "blockquote_summary": self.blockquote_summary,
            "sections": list(self.sections),
            "link_count": len(self.links),
            "has_optional_section": any(s.strip().lower() == "optional" for s in self.sections),
            "estimated_tokens": self.estimated_tokens,
        }


def parse_llms_txt(content: Optional[str], has_full: bool = False) -> Tuple[int, LlmsTxtResult, List[Finding]]:
    """Validate /llms.txt against llmstxt.org and score out of 35 points."""
    findings: List[Finding] = []
    if content is None:
        findings.append(
            Finding(
                rule_id="LLM-001",
                severity="error",
                category="llms_txt",
                message="Missing /llms.txt at site root. Coding agents and AI answer engines have no curated Markdown index of your docs.",
                remediation="Create /llms.txt with an `# H1` project name, `> summary` blockquote, and `## Docs` Markdown link list (or run `--emit-fixes`).",
            )
        )
        return 0, LlmsTxtResult(exists=False, has_full=has_full), findings

    stripped = content.strip()
    if not stripped:
        findings.append(
            Finding(
                rule_id="LLM-002",
                severity="error",
                category="llms_txt",
                message="/llms.txt exists but is empty.",
                remediation="Populate /llms.txt with an H1 title, summary blockquote, and H2 link sections.",
            )
        )
        return 2, LlmsTxtResult(exists=True, has_full=has_full), findings

    # Guard against HTML 404 soft-fallback served as 200 OK
    if stripped.lower().startswith("<!doctype html") or stripped.lower().startswith("<html"):
        findings.append(
            Finding(
                rule_id="LLM-003",
                severity="error",
                category="llms_txt",
                message="/llms.txt returns an HTML document instead of Markdown (likely an SPA soft-404 fallback).",
                remediation="Serve /llms.txt as plain text/markdown static asset instead of routing it to index.html.",
            )
        )
        return 0, LlmsTxtResult(exists=False, has_full=has_full), findings

    lines = content.splitlines()
    result = LlmsTxtResult(
        exists=True,
        has_full=has_full,
        estimated_tokens=max(1, round(len(content) / 4)),
    )

    score = 10  # Base score for serving non-empty Markdown /llms.txt
    first_non_empty_idx: Optional[int] = None
    h1_line_idx: Optional[int] = None
    blockquote_lines: List[str] = []
    current_section: Optional[str] = None
    malformed_bullets: List[int] = []
    in_code_fence = False

    for idx, raw in enumerate(lines, start=1):
        s = raw.strip()
        if s.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence or not s:
            continue

        if first_non_empty_idx is None:
            first_non_empty_idx = idx

        if s.startswith("# ") and not s.startswith("## "):
            if result.h1_title is None:
                result.h1_title = s[2:].strip()
                h1_line_idx = idx
            else:
                findings.append(
                    Finding(
                        rule_id="LLM-005",
                        severity="warning",
                        category="llms_txt",
                        message=f"Multiple H1 headings found in /llms.txt (line {idx}); the llmstxt.org spec mandates a single top-level H1.",
                        remediation="Demote secondary `#` headings to `##` sections.",
                    )
                )
            continue

        if s.startswith("## ") and not s.startswith("### "):
            current_section = s[3:].strip()
            result.sections.append(current_section)
            continue

        if s.startswith(">") and current_section is None:
            blockquote_lines.append(s.lstrip(">").strip())
            continue

        if s.startswith("- ") or s.startswith("* "):
            bullet_norm = "- " + s[2:]
            m = LINK_LINE_RE.match(bullet_norm)
            if m and current_section is not None:
                result.links.append(
                    LlmsLink(
                        section=current_section,
                        title=m.group(1).strip(),
                        url=m.group(2).strip(),
                        description=(m.group(3) or "").strip(),
                        line_no=idx,
                    )
                )
            elif current_section is not None:
                malformed_bullets.append(idx)

    if blockquote_lines:
        result.blockquote_summary = " ".join(blockquote_lines).strip()

    # Check 1: Single H1 at top (6 pts)
    if result.h1_title and h1_line_idx == first_non_empty_idx:
        score += 6
    elif result.h1_title:
        score += 3
        findings.append(
            Finding(
                rule_id="LLM-004",
                severity="warning",
                category="llms_txt",
                message=f"H1 title `{result.h1_title}` appears on line {h1_line_idx}, not as the first element of /llms.txt.",
                remediation="Place `# Project Title` on line 1 per llmstxt.org.",
            )
        )
    else:
        findings.append(
            Finding(
                rule_id="LLM-004",
                severity="error",
                category="llms_txt",
                message="/llms.txt is missing the required top-level `# Title` H1 heading.",
                remediation="Start /llms.txt with `# Your Project or Site Name`.",
            )
        )

    # Check 2: Summary blockquote (6 pts)
    if result.blockquote_summary and len(result.blockquote_summary) >= 15:
        score += 6
    elif result.blockquote_summary:
        score += 3
        findings.append(
            Finding(
                rule_id="LLM-006",
                severity="warning",
                category="llms_txt",
                message="Blockquote summary in /llms.txt is very short (< 15 chars).",
                remediation="Expand the `> ...` summary so LLMs understand what the product does and who it serves.",
            )
        )
    else:
        findings.append(
            Finding(
                rule_id="LLM-006",
                severity="warning",
                category="llms_txt",
                message="/llms.txt is missing the `> Summary` blockquote immediately following the H1 heading.",
                remediation="Add a concise `> One-sentence product summary...` blockquote under the H1.",
            )
        )

    # Check 3: H2 sections + valid links (9 pts)
    if result.sections and result.links:
        score += 7
        with_desc = [lnk for lnk in result.links if lnk.description]
        if len(with_desc) >= max(1, len(result.links) // 2):
            score += 2
        else:
            findings.append(
                Finding(
                    rule_id="LLM-008",
                    severity="info",
                    category="llms_txt",
                    message=f"Only {len(with_desc)}/{len(result.links)} links in /llms.txt include `: description` annotations.",
                    remediation="Format entries as `- [Page Title](https://.../page.md): Concise note on when an LLM should fetch this file`.",
                )
            )
    else:
        findings.append(
            Finding(
                rule_id="LLM-007",
                severity="error",
                category="llms_txt",
                message="/llms.txt has no `## Section` headings containing `- [Title](URL)` link items.",
                remediation="Add at least one `## Docs` section with `- [Quickstart](URL): How to install and run` links.",
            )
        )

    if malformed_bullets:
        sample = ", ".join(str(n) for n in malformed_bullets[:4])
        score = max(0, score - 2)
        findings.append(
            Finding(
                rule_id="LLM-009",
                severity="warning",
                category="llms_txt",
                message=f"{len(malformed_bullets)} list item(s) under H2 sections do not match `- [Title](URL): description` format (lines: {sample}).",
                remediation="Keep H2 lists strictly in `- [Title](URL): description` format so deterministic parsers can extract URLs.",
            )
        )

    # Check 4: Token footprint & /llms-full.txt bonus (4 pts)
    if result.estimated_tokens > 8000:
        findings.append(
            Finding(
                rule_id="LLM-010",
                severity="warning",
                category="llms_txt",
                message=f"/llms.txt is ~{result.estimated_tokens:,} tokens; oversized index files waste context windows.",
                remediation="Keep /llms.txt concise as an index (< 2,500 tokens) and move full inline documentation into /llms-full.txt.",
            )
        )
    else:
        score += 2

    if has_full:
        score += 2
        findings.append(
            Finding(
                rule_id="LLM-011",
                severity="pass",
                category="llms_txt",
                message="Both /llms.txt and /llms-full.txt are available.",
            )
        )
    else:
        findings.append(
            Finding(
                rule_id="LLM-011",
                severity="info",
                category="llms_txt",
                message="Optional /llms-full.txt not detected (+2 bonus points when provided for single-file RAG ingestion).",
                remediation="Consider generating /llms-full.txt concatenating core documentation pages.",
            )
        )

    if score >= 31:
        findings.append(
            Finding(
                rule_id="LLM-012",
                severity="pass",
                category="llms_txt",
                message=f"/llms.txt conforms to llmstxt.org ({len(result.sections)} sections, {len(result.links)} curated links, ~{result.estimated_tokens} tokens).",
            )
        )

    return min(35, max(0, score)), result, findings


def check_local_llms_links(site_root: Path, links: List[LlmsLink]) -> List[Finding]:
    """Verify relative links inside a local build directory actually exist on disk."""
    broken: List[str] = []
    for lnk in links:
        u = lnk.url.strip()
        if u.startswith(("http://", "https://", "mailto:", "#")):
            continue
        clean = u.split("#", 1)[0].split("?", 1)[0].lstrip("/")
        if not clean:
            continue
        candidate = site_root / clean
        if not (
            candidate.exists()
            or (site_root / f"{clean}.html").exists()
            or (site_root / clean / "index.html").exists()
        ):
            broken.append(f"{lnk.url} (line {lnk.line_no})")

    if broken:
        shown = ", ".join(broken[:4])
        return [
            Finding(
                rule_id="LLM-013",
                severity="error",
                category="llms_txt",
                message=f"{len(broken)} relative link(s) in /llms.txt do not resolve in `{site_root}`: {shown}.",
                remediation="Fix dead paths in /llms.txt or ensure Markdown siblings are copied into your build output directory.",
            )
        ]
    return []


def render_llms_txt_fix(
    title: str,
    description: str,
    base_url: str,
    discovered_pages: Optional[List[Tuple[str, str]]] = None,
) -> str:
    clean_title = title.strip() or "Project Documentation"
    clean_desc = (
        description.strip()
        or "Official documentation, API reference, and guides structured for language models and developers."
    )
    root = base_url.rstrip("/") if base_url else "https://example.com"
    lines = [
        f"# {clean_title}",
        "",
        f"> {clean_desc}",
        "",
        "## Core Documentation",
        "",
    ]
    if discovered_pages:
        for page_title, rel_url in discovered_pages[:8]:
            full_url = f"{root}/{rel_url.lstrip('/')}"
            lines.append(f"- [{page_title}]({full_url}): Reference page from site build.")
    else:
        lines.extend(
            [
                f"- [Overview]({root}/): Product overview, core architecture, and quickstart.",
                f"- [Getting Started]({root}/docs/quickstart): Installation, configuration, and first run.",
                f"- [API Reference]({root}/docs/api): Endpoints, CLI flags, and return schemas.",
            ]
        )
    lines.extend(
        [
            "",
            "## Optional",
            "",
            f"- [Changelog]({root}/changelog): Release history and migration notes.",
        ]
    )
    return "\n".join(lines) + "\n"
