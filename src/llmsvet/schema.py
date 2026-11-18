"""HTML metadata and JSON-LD Schema.org structured data inspector."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Set, Tuple

from llmsvet.models import Finding

HIGH_VALUE_SCHEMAS: Set[str] = {
    "SoftwareApplication",
    "WebApplication",
    "SoftwareSourceCode",
    "TechArticle",
    "Article",
    "BlogPosting",
    "NewsArticle",
    "FAQPage",
    "QAPage",
    "HowTo",
    "Product",
    "Organization",
    "WebSite",
    "WebPage",
    "BreadcrumbList",
    "Documentation",
    "APIReference",
}

REQUIRED_FIELDS_BY_TYPE: Dict[str, Tuple[str, ...]] = {
    "SoftwareApplication": ("name", "applicationCategory"),
    "WebApplication": ("name",),
    "TechArticle": ("headline",),
    "Article": ("headline",),
    "BlogPosting": ("headline",),
    "FAQPage": ("mainEntity",),
    "Organization": ("name", "url"),
    "WebSite": ("name", "url"),
    "Product": ("name",),
}


class _DocumentInspectParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.in_h1 = False
        self.in_jsonld = False
        self.title_parts: List[str] = []
        self.h1_list: List[str] = []
        self._current_h1: List[str] = []
        self._current_jsonld: List[str] = []
        self.jsonld_blocks: List[str] = []
        self.meta_description: Optional[str] = None
        self.meta_robots: List[str] = []
        self.canonical_url: Optional[str] = None
        self.html_lang: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if t == "html" and attr_map.get("lang"):
            self.html_lang = attr_map["lang"].strip()
        elif t == "title":
            self.in_title = True
        elif t == "h1":
            self.in_h1 = True
            self._current_h1 = []
        elif t == "meta":
            name = attr_map.get("name", "").lower()
            prop = attr_map.get("property", "").lower()
            content = attr_map.get("content", "").strip()
            if name == "description" and content and not self.meta_description:
                self.meta_description = content
            elif prop == "og:description" and content and not self.meta_description:
                self.meta_description = content
            elif name in ("robots", "googlebot") and content:
                self.meta_robots.append(content)
        elif t == "link":
            rels = {r.lower() for r in attr_map.get("rel", "").split()}
            if "canonical" in rels and attr_map.get("href"):
                self.canonical_url = attr_map["href"].strip()
        elif t == "script":
            stype = attr_map.get("type", "").split(";", 1)[0].strip().lower()
            if stype == "application/ld+json":
                self.in_jsonld = True
                self._current_jsonld = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self.in_title = False
        elif t == "h1" and self.in_h1:
            self.in_h1 = False
            text = " ".join("".join(self._current_h1).split())
            if text:
                self.h1_list.append(text)
        elif t == "script" and self.in_jsonld:
            self.in_jsonld = False
            raw = "".join(self._current_jsonld).strip()
            if raw:
                self.jsonld_blocks.append(raw)

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.in_h1:
            self._current_h1.append(data)
        if self.in_jsonld:
            self._current_jsonld.append(data)


def _flatten_jsonld_nodes(node: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if isinstance(node, list):
        for el in node:
            items.extend(_flatten_jsonld_nodes(el))
    elif isinstance(node, dict):
        if "@graph" in node and isinstance(node["@graph"], list):
            items.extend(_flatten_jsonld_nodes(node["@graph"]))
        if "@type" in node:
            items.append(node)
    return items


def inspect_html_and_schema(html: Optional[str]) -> Tuple[int, Dict[str, Any], List[Finding]]:
    """Inspect HTML citability essentials and JSON-LD Schema.org markup (max 30 points)."""
    findings: List[Finding] = []
    if html is None:
        findings.append(
            Finding(
                rule_id="SCH-001",
                severity="error",
                category="schema",
                message="No entry HTML document (index.html) found to inspect metadata and JSON-LD structured data.",
                remediation="Ensure your build directory contains index.html or pass a valid page URL.",
            )
        )
        return 0, {"html_found": False, "schema_types": []}, findings

    parser = _DocumentInspectParser()
    parser.feed(html)
    title = " ".join("".join(parser.title_parts).split())

    score = 0
    # 1. HTML citability fundamentals (12 pts)
    if title:
        score += 4
    else:
        findings.append(
            Finding(
                rule_id="SCH-002",
                severity="error",
                category="schema",
                message="HTML page is missing a `<title>` element.",
                remediation="Add a descriptive `<title>` so AI search citations display an accurate source title.",
            )
        )

    if parser.meta_description and len(parser.meta_description) >= 30:
        score += 4
    elif parser.meta_description:
        score += 2
        findings.append(
            Finding(
                rule_id="SCH-003",
                severity="warning",
                category="schema",
                message=f"Meta description is too brief ({len(parser.meta_description)} chars).",
                remediation="Provide a 50–160 character `<meta name=\"description\">` summarizing concrete facts.",
            )
        )
    else:
        findings.append(
            Finding(
                rule_id="SCH-003",
                severity="warning",
                category="schema",
                message="Missing `<meta name=\"description\">` tag.",
                remediation="Add `<meta name=\"description\" content=\"...\">` for answer engine snippet extraction.",
            )
        )

    if parser.canonical_url:
        score += 2
    else:
        findings.append(
            Finding(
                rule_id="SCH-004",
                severity="info",
                category="schema",
                message="Missing `<link rel=\"canonical\">` URL; duplicate previews or query URLs may split citations.",
                remediation="Add `<link rel=\"canonical\" href=\"https://...\">` in `<head>`.",
            )
        )

    if parser.h1_list:
        score += 2
    else:
        findings.append(
            Finding(
                rule_id="SCH-005",
                severity="warning",
                category="schema",
                message="No `<h1>` heading found in HTML body.",
                remediation="Include a clear primary `<h1>` heading matching the page topic.",
            )
        )

    # 2. JSON-LD Schema.org inspection (18 pts)
    discovered_types: List[str] = []
    invalid_blocks = 0
    missing_prop_notes: List[str] = []

    for idx, raw_block in enumerate(parser.jsonld_blocks, start=1):
        try:
            parsed = json.loads(raw_block)
        except json.JSONDecodeError as exc:
            invalid_blocks += 1
            findings.append(
                Finding(
                    rule_id="SCH-006",
                    severity="error",
                    category="schema",
                    message=f"Invalid JSON syntax in `<script type=\"application/ld+json\">` block #{idx}: {exc.msg} (line {exc.lineno}).",
                    remediation="Fix trailing commas or unescaped quotes inside the JSON-LD script block.",
                )
            )
            continue

        for entity in _flatten_jsonld_nodes(parsed):
            raw_type = entity.get("@type")
            types = [raw_type] if isinstance(raw_type, str) else (raw_type if isinstance(raw_type, list) else [])
            for stype in types:
                if not isinstance(stype, str):
                    continue
                clean_t = stype.rsplit("/", 1)[-1]
                discovered_types.append(clean_t)
                req_fields = REQUIRED_FIELDS_BY_TYPE.get(clean_t, ())
                for rf in req_fields:
                    if not entity.get(rf):
                        missing_prop_notes.append(f"{clean_t}.{rf}")

    unique_types = sorted(set(discovered_types))
    high_value_matches = [t for t in unique_types if t in HIGH_VALUE_SCHEMAS]

    if not parser.jsonld_blocks:
        findings.append(
            Finding(
                rule_id="SCH-007",
                severity="error",
                category="schema",
                message="No `<script type=\"application/ld+json\">` Schema.org structured data found.",
                remediation="Embed JSON-LD (`SoftwareApplication`, `TechArticle`, `FAQPage`, or `Organization`) in `<head>` (or generate one via `--emit-fixes`).",
            )
        )
    elif unique_types:
        score += 10
        if high_value_matches:
            score += 5
        if len(high_value_matches) >= 2 and invalid_blocks == 0 and not missing_prop_notes:
            score += 3
        elif invalid_blocks == 0 and not missing_prop_notes:
            score += 2

        if missing_prop_notes:
            findings.append(
                Finding(
                    rule_id="SCH-008",
                    severity="warning",
                    category="schema",
                    message=f"JSON-LD entity missing recommended field(s): {', '.join(missing_prop_notes[:5])}.",
                    remediation="Populate core properties (`name`, `headline`, `url`, `mainEntity`) so answer engines trust the entity graph.",
                )
            )
        else:
            findings.append(
                Finding(
                    rule_id="SCH-009",
                    severity="pass",
                    category="schema",
                    message=f"Valid JSON-LD structured data detected: {', '.join(unique_types)}.",
                )
            )

    meta = {
        "html_found": True,
        "title": title or None,
        "meta_description": parser.meta_description,
        "canonical_url": parser.canonical_url,
        "html_lang": parser.html_lang,
        "h1_count": len(parser.h1_list),
        "meta_robots": parser.meta_robots,
        "jsonld_block_count": len(parser.jsonld_blocks),
        "schema_types": unique_types,
    }
    return min(30, max(0, score)), meta, findings


def render_schema_fix(title: str, description: str, url: str) -> str:
    payload = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "name": title or "Documentation",
                "url": url or "https://example.com/",
                "description": description or "Official project documentation and technical guides.",
            },
            {
                "@type": "SoftwareApplication",
                "name": title or "Application",
                "applicationCategory": "DeveloperApplication",
                "operatingSystem": "Linux, macOS, Windows",
                "url": url or "https://example.com/",
                "description": description or "Developer tool and documentation.",
            },
        ],
    }
    pretty = json.dumps(payload, indent=2, ensure_ascii=False)
    return f'<script type="application/ld+json">\n{pretty}\n</script>\n'
