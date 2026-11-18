"""Command-line interface and report formatters for llmsvet."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from llmsvet import __version__
from llmsvet.auditor import audit_target
from llmsvet.llmstxt import parse_llms_txt
from llmsvet.models import AuditReport, BotStatus, Finding
from llmsvet.robots import evaluate_crawlers


class _Style:
    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def bold(self, t: str) -> str:
        return self.wrap("1", t)

    def dim(self, t: str) -> str:
        return self.wrap("2", t)

    def green(self, t: str) -> str:
        return self.wrap("32", t)

    def yellow(self, t: str) -> str:
        return self.wrap("33", t)

    def red(self, t: str) -> str:
        return self.wrap("31", t)

    def cyan(self, t: str) -> str:
        return self.wrap("36", t)


def _bar(score: int, maximum: int, width: int, st: _Style) -> str:
    ratio = max(0.0, min(1.0, score / max(1, maximum)))
    filled = round(width * ratio)
    bar_txt = "█" * filled + "░" * (width - filled)
    if ratio >= 0.8:
        return st.green(bar_txt)
    if ratio >= 0.5:
        return st.yellow(bar_txt)
    return st.red(bar_txt)


def format_table(report: AuditReport, use_color: bool = True) -> str:
    st = _Style(use_color)
    lines: List[str] = []
    grade_colored = (
        st.green(f"{report.total_score}/100 ({report.grade})")
        if report.total_score >= 80
        else (
            st.yellow(f"{report.total_score}/100 ({report.grade})")
            if report.total_score >= 55
            else st.red(f"{report.total_score}/100 ({report.grade})")
        )
    )

    lines.append(
        f"{st.bold('llmsvet')} v{__version__}  |  Target: {st.cyan(report.target)}  |  Policy: {report.policy}"
    )
    lines.append("─" * 74)
    lines.append(f"  Overall Visibility Score : {st.bold(grade_colored)}")
    lines.append(
        f"  AI Crawler Access        : {_bar(report.crawler_score, report.crawler_max, 20, st)}  {report.crawler_score:>2}/{report.crawler_max}"
    )
    lines.append(
        f"  /llms.txt Readiness      : {_bar(report.llms_score, report.llms_max, 20, st)}  {report.llms_score:>2}/{report.llms_max}"
    )
    lines.append(
        f"  Schema & HTML Citability : {_bar(report.schema_score, report.schema_max, 20, st)}  {report.schema_score:>2}/{report.schema_max}"
    )
    lines.append("─" * 74)

    search_bots = [b for b in report.bots if b.role == "search"]
    train_bots = [b for b in report.bots if b.role == "training"]
    s_ok = sum(1 for b in search_bots if b.allowed)
    t_ok = sum(1 for b in train_bots if b.allowed)
    lines.append(
        f"  Search/Citation Bots Allowed : {s_ok}/{len(search_bots)}   |   Training Scrapers Allowed : {t_ok}/{len(train_bots)}"
    )

    lines.append("")
    lines.append(st.bold("Findings:"))
    sev_icon = {
        "error": st.red("✖ ERROR"),
        "warning": st.yellow("▲ WARN "),
        "info": st.cyan("ℹ INFO "),
        "pass": st.green("✔ PASS "),
    }
    for f in report.findings:
        badge = sev_icon.get(f.severity, f.severity.upper())
        lines.append(f"  {badge} [{f.rule_id}] {f.message}")
        if f.remediation and f.severity in ("error", "warning"):
            lines.append(f"           {st.dim('Fix: ' + f.remediation)}")

    if report.emitted_files:
        lines.append("")
        lines.append(st.bold("Generated Ready-to-Deploy Fix Files:"))
        for path in report.emitted_files:
            lines.append(f"  {st.green('+')} {path}")

    return "\n".join(lines)


def format_markdown(report: AuditReport) -> str:
    lines = [
        f"## `llmsvet` Audit Report: `{report.target}`",
        "",
        f"**Overall Score:** **{report.total_score}/100** (Grade **{report.grade}**) | **Policy:** `{report.policy}`",
        "",
        "| Dimension | Score | Max |",
        "|---|---:|---:|",
        f"| AI Crawler Access (`robots.txt` & directives) | {report.crawler_score} | {report.crawler_max} |",
        f"| `/llms.txt` & `/llms-full.txt` Readiness | {report.llms_score} | {report.llms_max} |",
        f"| JSON-LD Schema.org & HTML Citability | {report.schema_score} | {report.schema_max} |",
        "",
        "### Findings",
        "",
    ]
    for f in report.findings:
        icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️", "pass": "✅"}.get(f.severity, "•")
        lines.append(f"- {icon} **`{f.rule_id}`**: {f.message}")
        if f.remediation and f.severity in ("error", "warning"):
            lines.append(f"  - *Remediation:* {f.remediation}")
    return "\n".join(lines) + "\n"


def _format_bot_matrix(bots: List[BotStatus], use_color: bool = True) -> str:
    st = _Style(use_color)
    lines = [
        f"{'USER-AGENT':<22} {'VENDOR':<14} {'ROLE':<10} {'STATUS':<10} {'MATCHED RULE'}",
        "─" * 76,
    ]
    for b in bots:
        status_txt = st.green("ALLOWED   ") if b.allowed else st.red("BLOCKED   ")
        lines.append(
            f"{b.agent:<22} {b.vendor:<14} {b.role:<10} {status_txt} [{b.matched_group}] {b.matched_rule}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llmsvet",
        description="Audit /llms.txt, JSON-LD Schema.org structured data, and AI crawler access rules.",
    )
    parser.add_argument("--version", action="version", version=f"llmsvet {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="Audit a live URL or local static build directory")
    p_audit.add_argument("target", help="URL (https://...) or local folder/file path (./dist)")
    p_audit.add_argument(
        "--policy",
        choices=["search-only", "open", "strict-block"],
        default="search-only",
        help="Target AI crawler policy (default: search-only)",
    )
    p_audit.add_argument(
        "--format",
        choices=["table", "json", "markdown"],
        default="table",
        help="Output report format (default: table)",
    )
    p_audit.add_argument(
        "--emit-fixes",
        metavar="DIR",
        default=None,
        help="Write ready-to-deploy robots.txt, llms.txt, and schema.jsonld.html into DIR",
    )
    p_audit.add_argument(
        "--fail-under",
        type=int,
        default=None,
        metavar="SCORE",
        help="Exit with code 2 if overall score is below SCORE (for CI gates)",
    )
    p_audit.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="HTTP request timeout in seconds for remote URLs (default: 8.0)",
    )

    p_llms = sub.add_parser("check-llms", help="Validate a standalone llms.txt file")
    p_llms.add_argument("path", help="Path to llms.txt file")
    p_llms.add_argument("--format", choices=["table", "json"], default="table")

    p_rob = sub.add_parser("check-robots", help="Inspect how a robots.txt file treats all 18 AI crawlers")
    p_rob.add_argument("path", help="Path to robots.txt file")
    p_rob.add_argument(
        "--policy",
        choices=["search-only", "open", "strict-block"],
        default="search-only",
    )
    p_rob.add_argument("--format", choices=["table", "json"], default="table")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    use_color = sys.stdout.isatty() and ("NO_COLOR" not in os.environ)

    if args.command == "audit":
        report = audit_target(
            args.target,
            policy=args.policy,
            emit_fixes_dir=args.emit_fixes,
            timeout=args.timeout,
        )
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        elif args.format == "markdown":
            print(format_markdown(report), end="")
        else:
            print(format_table(report, use_color=use_color))

        if args.fail_under is not None and report.total_score < args.fail_under:
            return 2
        return 0

    if args.command == "check-llms":
        p = Path(args.path)
        if not p.is_file():
            print(f"Error: file not found: {args.path}", file=sys.stderr)
            return 1
        score, res, findings = parse_llms_txt(p.read_text(encoding="utf-8", errors="replace"))
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "score": score,
                        "max": 35,
                        "metadata": res.to_meta(),
                        "findings": [f.to_dict() for f in findings],
                    },
                    indent=2,
                )
            )
        else:
            st = _Style(use_color)
            print(f"{st.bold('llms.txt check')}: {score}/35  (~{res.estimated_tokens} tokens, {len(res.links)} links)")
            for f in findings:
                print(f"  [{f.severity.upper()}] {f.rule_id}: {f.message}")
        return 0 if not any(f.severity == "error" for f in findings) else 2

    if args.command == "check-robots":
        p = Path(args.path)
        if not p.is_file():
            print(f"Error: file not found: {args.path}", file=sys.stderr)
            return 1
        score, bots, findings = evaluate_crawlers(
            p.read_text(encoding="utf-8", errors="replace"), [], policy=args.policy
        )
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "score": score,
                        "max": 35,
                        "bots": [b.to_dict() for b in bots],
                        "findings": [f.to_dict() for f in findings],
                    },
                    indent=2,
                )
            )
        else:
            print(_format_bot_matrix(bots, use_color=use_color))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
