"""Comprehensive unit and CLI end-to-end tests for llmsvet."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmsvet.auditor import audit_target
from llmsvet.cli import main
from llmsvet.llmstxt import check_local_llms_links, parse_llms_txt
from llmsvet.robots import RobotsTxt, evaluate_crawlers
from llmsvet.schema import inspect_html_and_schema


class TestRobotsParser(unittest.TestCase):
    def test_wildcard_blocks_search_bots_when_not_overridden(self) -> None:
        raw = """
        User-agent: *
        Disallow: /
        """
        score, bots, findings = evaluate_crawlers(raw, [], policy="search-only")
        self.assertLess(score, 15)
        self.assertTrue(any(f.rule_id == "ROB-003" for f in findings))
        self.assertTrue(all(not b.allowed for b in bots))

    def test_split_policy_allows_search_blocks_training(self) -> None:
        raw = """
        User-agent: OAI-SearchBot
        User-agent: ChatGPT-User
        User-agent: PerplexityBot
        User-agent: Perplexity-User
        User-agent: Claude-SearchBot
        User-agent: Claude-User
        User-agent: DuckAssistBot
        User-agent: YouBot
        User-agent: MistralAI-User
        Allow: /

        User-agent: GPTBot
        User-agent: CCBot
        User-agent: ClaudeBot
        Disallow: /
        """
        score, bots, findings = evaluate_crawlers(raw, [], policy="search-only")
        self.assertEqual(score, 35)
        by_name = {b.agent: b.allowed for b in bots}
        self.assertTrue(by_name["OAI-SearchBot"])
        self.assertTrue(by_name["PerplexityBot"])
        self.assertFalse(by_name["GPTBot"])
        self.assertFalse(by_name["CCBot"])
        self.assertTrue(any(f.rule_id == "ROB-004" for f in findings))

    def test_rfc9309_longest_match_and_end_anchor(self) -> None:
        r = RobotsTxt(
            "User-agent: OAI-SearchBot\nDisallow: /private\nAllow: /private/public-doc$\n"
        )
        self.assertFalse(r.check_access("OAI-SearchBot", "/private/secret")[0])
        self.assertTrue(r.check_access("OAI-SearchBot", "/private/public-doc")[0])
        self.assertFalse(r.check_access("OAI-SearchBot", "/private/public-doc/extra")[0])


class TestLlmsTxtValidator(unittest.TestCase):
    def test_valid_llms_txt_scores_high(self) -> None:
        doc = (
            "# Acme SDK\n\n"
            "> Fast streaming client for deterministic event pipelines.\n\n"
            "## Core Docs\n\n"
            "- [Quickstart](https://acme.dev/quickstart.md): Install and ship in 3 minutes.\n"
            "- [API](https://acme.dev/api.md): Full client reference.\n"
        )
        score, res, findings = parse_llms_txt(doc, has_full=True)
        self.assertEqual(score, 35)
        self.assertEqual(res.h1_title, "Acme SDK")
        self.assertEqual(len(res.links), 2)
        self.assertFalse(any(f.severity == "error" for f in findings))

    def test_html_soft_404_rejected(self) -> None:
        score, res, findings = parse_llms_txt("<!DOCTYPE html><html><body>404</body></html>")
        self.assertEqual(score, 0)
        self.assertFalse(res.exists)
        self.assertTrue(any(f.rule_id == "LLM-003" for f in findings))

    def test_broken_local_relative_link_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "exists.md").write_text("# Exists\n", encoding="utf-8")
            doc = "# Site\n\n> Summary text exceeding fifteen chars.\n\n## Docs\n- [Ok](/exists.md): ok\n- [Dead](/missing.md): broken\n"
            _, res, _ = parse_llms_txt(doc)
            errs = check_local_llms_links(root, res.links)
            self.assertEqual(len(errs), 1)
            self.assertEqual(errs[0].rule_id, "LLM-013")


class TestSitemapCoverage(unittest.TestCase):
    llms = "# Example\n\n> Documentation for the example service.\n\n## Guides\n"

    def sitemap(self, count):
        entries = "".join(
            f"<url><loc>https://example.com/section-{index}/overview</loc></url>"
            for index in range(count)
        )
        return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'

    def test_local_coverage_threshold_is_strict_and_does_not_change_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "llms.txt").write_text(
                self.llms + "- [Overview](/section-0/overview?version=2#start): A guide.\n",
                encoding="utf-8",
            )
            baseline = audit_target(tmp)
            sitemap = root / "sitemap.xml"
            sitemap.write_text(self.sitemap(10), encoding="utf-8")
            self.assertFalse(any(f.rule_id == "LLM-014" for f in audit_target(tmp).findings))
            sitemap.write_text(self.sitemap(11), encoding="utf-8")
            report = audit_target(tmp)
            findings = [f for f in report.findings if f.rule_id == "LLM-014"]
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "info")
            self.assertEqual(report.total_score, baseline.total_score)

    def test_remote_links_from_other_hosts_do_not_count(self):
        files = {
            "/llms.txt": self.llms + "- [Other](https://other.example/section-0/overview): A guide.\n",
            "/sitemap.xml": self.sitemap(1),
        }

        def fetch(url, timeout):
            return files.get(url.removeprefix("https://example.com")), []

        with patch("llmsvet.auditor._fetch_url_text", side_effect=fetch):
            report = audit_target("https://example.com/start")
        self.assertTrue(any(f.rule_id == "LLM-014" for f in report.findings))

    def test_missing_malformed_or_index_sitemap_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "llms.txt").write_text(self.llms, encoding="utf-8")
            for content in (None, "<urlset>", "<sitemapindex><loc>https://example.com/other.xml</loc></sitemapindex>"):
                with self.subTest(content=content):
                    if content is not None:
                        (root / "sitemap.xml").write_text(content, encoding="utf-8")
                    self.assertFalse(any(f.rule_id == "LLM-014" for f in audit_target(tmp).findings))


class TestSchemaAndEndToEnd(unittest.TestCase):
    def test_jsonld_graph_extraction(self) -> None:
        html = """<!doctype html>
        <html lang="en">
        <head>
          <title>Acme CLI Documentation</title>
          <meta name="description" content="Audit /llms.txt, Schema.org JSON-LD, and AI crawler policies before deploying.">
          <link rel="canonical" href="https://acme.dev/">
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@graph": [
              {"@type": "WebSite", "name": "Acme", "url": "https://acme.dev/"},
              {"@type": "SoftwareApplication", "name": "Acme CLI", "applicationCategory": "DeveloperApplication"}
            ]
          }
          </script>
        </head>
        <body><h1>Acme CLI</h1></body>
        </html>"""
        score, meta, findings = inspect_html_and_schema(html)
        self.assertEqual(score, 30)
        self.assertIn("SoftwareApplication", meta["schema_types"])
        self.assertFalse(any(f.severity == "error" for f in findings))

    def test_emit_fixes_heals_broken_site_to_grade_a(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site"
            fixes = Path(tmp) / "fixes"
            site.mkdir()
            # Broken site: blocks wildcard (killing OAI-SearchBot), no llms.txt, no JSON-LD
            (site / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
            (site / "index.html").write_text(
                "<!doctype html><html><head><title>Demo Docs</title>"
                '<meta name="description" content="Developer documentation for Demo SDK and CLI tools.">'
                '<link rel="canonical" href="https://demo.example.com/">'
                "</head><body><h1>Demo Docs</h1></body></html>",
                encoding="utf-8",
            )

            before = audit_target(str(site), policy="search-only", emit_fixes_dir=str(fixes))
            self.assertLess(before.total_score, 30)
            self.assertEqual(len(before.emitted_files), 3)

            # Apply generated fixes back into site
            (site / "robots.txt").write_text((fixes / "robots.txt").read_text(encoding="utf-8"), encoding="utf-8")
            (site / "llms.txt").write_text((fixes / "llms.txt").read_text(encoding="utf-8"), encoding="utf-8")
            schema_tag = (fixes / "schema.jsonld.html").read_text(encoding="utf-8")
            patched_html = (site / "index.html").read_text(encoding="utf-8").replace("</head>", f"{schema_tag}</head>")
            (site / "index.html").write_text(patched_html, encoding="utf-8")

            after = audit_target(str(site), policy="search-only")
            self.assertGreaterEqual(after.total_score, 95)
            self.assertEqual(after.grade, "A+")

    def test_cli_json_and_fail_under_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "index.html").write_text("<html><head><title>X</title></head><body></body></html>", encoding="utf-8")
            rc_fail = main(["audit", str(site), "--format", "json", "--fail-under", "80"])
            self.assertEqual(rc_fail, 2)
            rc_pass = main(["audit", str(site), "--format", "markdown", "--fail-under", "1"])
            self.assertEqual(rc_pass, 0)


if __name__ == "__main__":
    unittest.main()
