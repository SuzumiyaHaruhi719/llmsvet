"""Site loader, orchestrator, and remediation file emitter for llmsvet."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from llmsvet import __version__
from llmsvet.llmstxt import check_local_llms_links, parse_llms_txt, render_llms_txt_fix
from llmsvet.models import AuditReport, Finding, compute_grade
from llmsvet.robots import evaluate_crawlers, render_robots_fix
from llmsvet.schema import inspect_html_and_schema, render_schema_fix

MAX_FETCH_BYTES = 2 * 1024 * 1024  # 2 MB cap per fetched text asset


def _fetch_url_text(url: str, timeout: float = 8.0) -> Tuple[Optional[str], List[str]]:
    headers = {
        "User-Agent": f"llmsvet/{__version__} (+https://github.com/SuzumiyaHaruhi719/llmsvet)",
        "Accept": "text/html,text/plain,text/markdown,*/*;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return None, []
            x_robots = resp.headers.get_all("X-Robots-Tag") or []
            raw = resp.read(MAX_FETCH_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), list(x_robots)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None, []


def _read_optional_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _discover_local_pages(root: Path) -> List[Tuple[str, str]]:
    pages: List[Tuple[str, str]] = []
    for p in sorted(root.rglob("*.html")):
        rel = p.relative_to(root).as_posix()
        if rel == "index.html":
            pages.insert(0, ("Home", "/"))
        elif len(pages) < 8:
            clean = rel[:-10] if rel.endswith("/index.html") else rel[:-5]
            label = clean.replace("-", " ").replace("/", " / ").title() or "Page"
            pages.append((label, f"/{clean}"))
    return pages


def audit_target(
    target: str,
    policy: str = "search-only",
    emit_fixes_dir: Optional[str] = None,
    timeout: float = 8.0,
) -> AuditReport:
    is_remote = target.startswith(("http://", "https://"))
    x_robots_headers: List[str] = []
    discovered_pages: List[Tuple[str, str]] = []
    base_url = ""

    if is_remote:
        parsed = urllib.parse.urlparse(target)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        base_url = origin
        page_url = target if parsed.path else f"{origin}/"
        html_text, x_robots_headers = _fetch_url_text(page_url, timeout=timeout)
        robots_text, _ = _fetch_url_text(f"{origin}/robots.txt", timeout=timeout)
        llms_text, _ = _fetch_url_text(f"{origin}/llms.txt", timeout=timeout)
        llms_full_text, _ = _fetch_url_text(f"{origin}/llms-full.txt", timeout=timeout)
        has_full = bool(
            llms_full_text
            and not llms_full_text.lstrip().lower().startswith(("<!doctype html", "<html"))
        )
        site_root: Optional[Path] = None
    else:
        p = Path(target)
        if p.is_file():
            site_root = p.parent
            html_text = _read_optional_file(p)
        else:
            site_root = p
            html_text = _read_optional_file(site_root / "index.html")
        robots_text = _read_optional_file(site_root / "robots.txt")
        llms_text = _read_optional_file(site_root / "llms.txt")
        has_full = (site_root / "llms-full.txt").is_file() or (site_root / "llms-full.md").is_file()
        if site_root.is_dir():
            discovered_pages = _discover_local_pages(site_root)

    schema_score, html_meta, schema_findings = inspect_html_and_schema(html_text)
    combined_meta_robots = list(html_meta.get("meta_robots", [])) + x_robots_headers
    crawler_score, bots, crawler_findings = evaluate_crawlers(
        robots_text, combined_meta_robots, policy=policy
    )
    llms_score, llms_res, llms_findings = parse_llms_txt(llms_text, has_full=has_full)

    if site_root is not None and llms_res.links:
        broken_findings = check_local_llms_links(site_root, llms_res.links)
        if broken_findings:
            llms_score = max(0, llms_score - 5)
            llms_findings.extend(broken_findings)

    all_findings: List[Finding] = crawler_findings + llms_findings + schema_findings
    total = crawler_score + llms_score + schema_score
    grade = compute_grade(total)

    if not base_url:
        base_url = str(html_meta.get("canonical_url") or "https://example.com")

    emitted: List[str] = []
    if emit_fixes_dir:
        out = Path(emit_fixes_dir)
        out.mkdir(parents=True, exist_ok=True)
        title_hint = str(llms_res.h1_title or html_meta.get("title") or "Project Documentation")
        desc_hint = str(
            llms_res.blockquote_summary
            or html_meta.get("meta_description")
            or "Technical documentation and API guides."
        )

        robots_out = out / "robots.txt"
        robots_out.write_text(render_robots_fix(policy=policy, base_url=base_url), encoding="utf-8")
        emitted.append(str(robots_out))

        llms_out = out / "llms.txt"
        llms_out.write_text(
            render_llms_txt_fix(title_hint, desc_hint, base_url, discovered_pages),
            encoding="utf-8",
        )
        emitted.append(str(llms_out))

        schema_out = out / "schema.jsonld.html"
        schema_out.write_text(
            render_schema_fix(title_hint, desc_hint, base_url),
            encoding="utf-8",
        )
        emitted.append(str(schema_out))

    metadata = {
        "html": html_meta,
        "llms_txt": llms_res.to_meta(),
        "robots_txt_exists": robots_text is not None,
    }

    return AuditReport(
        target=target,
        mode="remote" if is_remote else "local",
        policy=policy,
        total_score=total,
        grade=grade,
        crawler_score=crawler_score,
        crawler_max=35,
        llms_score=llms_score,
        llms_max=35,
        schema_score=schema_score,
        schema_max=30,
        bots=bots,
        findings=all_findings,
        metadata=metadata,
        emitted_files=emitted,
    )
