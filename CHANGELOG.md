# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-18

### Added
- `llmsvet audit <url-or-dir>` command supporting both live websites and offline static build folders (`./dist`, `./public`).
- RFC 9309 `robots.txt` matcher with wildcard (`*`), end-anchor (`$`), and longest-match precedence across 18 AI crawlers split into **search/citation** vs. **training** roles.
- `/llms.txt` and `/llms-full.txt` conformance checker (`H1`, summary blockquote, `H2` link sections, broken local link verification, token estimate).
- HTML citability and `<script type="application/ld+json">` `@graph` Schema.org validator.
- `--emit-fixes <dir>` generator producing ready-to-deploy `robots.txt`, `llms.txt`, and `schema.jsonld.html`.
- `--format table|json|markdown` and `--fail-under <score>` CI gate flag.
- Standalone `check-llms` and `check-robots` subcommands.
