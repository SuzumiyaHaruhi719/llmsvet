"""RFC 9309 robots.txt parser and 18-agent AI crawler policy evaluator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from llmsvet.models import BotStatus, Finding

AI_BOTS: List[Tuple[str, str, str]] = [
    # (User-Agent token, Vendor, Role: "search" | "training")
    ("OAI-SearchBot", "OpenAI", "search"),
    ("ChatGPT-User", "OpenAI", "search"),
    ("PerplexityBot", "Perplexity", "search"),
    ("Perplexity-User", "Perplexity", "search"),
    ("Claude-SearchBot", "Anthropic", "search"),
    ("Claude-User", "Anthropic", "search"),
    ("DuckAssistBot", "DuckDuckGo", "search"),
    ("YouBot", "You.com", "search"),
    ("MistralAI-User", "Mistral", "search"),
    ("GPTBot", "OpenAI", "training"),
    ("ClaudeBot", "Anthropic", "training"),
    ("Google-Extended", "Google", "training"),
    ("Applebot-Extended", "Apple", "training"),
    ("CCBot", "Common Crawl", "training"),
    ("Bytespider", "ByteDance", "training"),
    ("Meta-ExternalAgent", "Meta", "training"),
    ("Amazonbot", "Amazon", "training"),
    ("Cohere-ai", "Cohere", "training"),
]


@dataclass
class RobotRule:
    allow: bool
    pattern: str


class RobotsTxt:
    """RFC 9309 compliant robots.txt rule matcher."""

    def __init__(self, content: Optional[str]) -> None:
        self.exists = content is not None
        self.groups: Dict[str, List[RobotRule]] = {}
        self.sitemaps: List[str] = []
        if content is not None:
            self._parse(content)

    def _parse(self, content: str) -> None:
        current_agents: List[str] = []
        seen_rule_in_block = False

        for raw_line in content.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field_name, value = line.split(":", 1)
            key = field_name.strip().lower()
            val = value.strip()

            if key == "user-agent":
                agent_key = val.lower()
                if seen_rule_in_block:
                    current_agents = [agent_key]
                    seen_rule_in_block = False
                else:
                    current_agents.append(agent_key)
                self.groups.setdefault(agent_key, [])
            elif key in ("allow", "disallow"):
                if not current_agents:
                    continue
                seen_rule_in_block = True
                if not val:
                    # Empty Disallow means allow everything; RFC 9309 ignores empty pattern rules
                    continue
                for ag in current_agents:
                    self.groups.setdefault(ag, []).append(
                        RobotRule(allow=(key == "allow"), pattern=val)
                    )
            elif key == "sitemap" and val:
                self.sitemaps.append(val)

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        anchored_end = pattern.endswith("$")
        core = pattern[:-1] if anchored_end else pattern
        parts = [re.escape(p) for p in core.split("*")]
        regex = "^" + ".*".join(parts) + ("$" if anchored_end else "")
        return re.match(regex, path) is not None

    def check_access(self, user_agent: str, path: str = "/") -> Tuple[bool, str, str]:
        """Return (allowed, matched_group, matched_rule_display)."""
        if not self.exists:
            return True, "(no robots.txt)", "implicit allow"

        ua_lower = user_agent.lower()
        matched_group = None
        # RFC 9309: most specific user-agent product token match wins over '*'
        best_len = 0
        for group_ua in self.groups:
            if group_ua == "*":
                continue
            if group_ua in ua_lower and len(group_ua) > best_len:
                matched_group = group_ua
                best_len = len(group_ua)

        if matched_group is None:
            if "*" in self.groups:
                matched_group = "*"
            else:
                return True, "(unmatched)", "implicit allow"

        rules = self.groups.get(matched_group, [])
        best_rule: Optional[RobotRule] = None
        best_rule_len = -1

        for rule in rules:
            if self._matches(rule.pattern, path):
                pat_len = len(rule.pattern.rstrip("$"))
                # Longest pattern wins; on tie, Allow wins over Disallow (RFC 9309 §2.2.2)
                if pat_len > best_rule_len or (
                    pat_len == best_rule_len and rule.allow and best_rule and not best_rule.allow
                ):
                    best_rule = rule
                    best_rule_len = pat_len

        if best_rule is None:
            return True, matched_group, "implicit allow"

        verb = "Allow" if best_rule.allow else "Disallow"
        return best_rule.allow, matched_group, f"{verb}: {best_rule.pattern}"


def evaluate_crawlers(
    robots_content: Optional[str],
    meta_robots: List[str],
    policy: str = "search-only",
) -> Tuple[int, List[BotStatus], List[Finding]]:
    """Score AI crawler visibility out of 35 points."""
    robots = RobotsTxt(robots_content)
    bots: List[BotStatus] = []
    findings: List[Finding] = []

    meta_tokens = {t.strip().lower() for entry in meta_robots for t in entry.split(",")}
    global_noindex = "noindex" in meta_tokens

    for agent, vendor, role in AI_BOTS:
        allowed, group, rule_str = robots.check_access(agent, "/")
        if global_noindex and role == "search":
            allowed = False
            rule_str = "meta robots: noindex"
        bots.append(
            BotStatus(
                agent=agent,
                vendor=vendor,
                role=role,
                allowed=allowed,
                matched_group=group,
                matched_rule=rule_str,
            )
        )

    search_bots = [b for b in bots if b.role == "search"]
    training_bots = [b for b in bots if b.role == "training"]
    allowed_search = [b for b in search_bots if b.allowed]
    blocked_search = [b for b in search_bots if not b.allowed]
    allowed_training = [b for b in training_bots if b.allowed]
    blocked_training = [b for b in training_bots if not b.allowed]

    score = 0
    if not robots.exists:
        score += 5
        findings.append(
            Finding(
                rule_id="ROB-001",
                severity="warning",
                category="crawlers",
                message="Missing /robots.txt file; AI crawlers fall back to implicit defaults without an explicit policy or Sitemap pointer.",
                remediation="Add a /robots.txt that explicitly declares rules for AI search bots (OAI-SearchBot, PerplexityBot, Claude-SearchBot) and training bots.",
            )
        )
    else:
        score += 8

    if global_noindex:
        findings.append(
            Finding(
                rule_id="ROB-002",
                severity="error",
                category="crawlers",
                message="HTML contains `<meta name=\"robots\" content=\"noindex\">`, preventing search and answer engines from indexing citations.",
                remediation="Remove `noindex` from public documentation and landing pages.",
            )
        )

    if policy == "search-only":
        ratio = len(allowed_search) / max(1, len(search_bots))
        score += round(22 * ratio)
        # Explicit policy clarity bonus (either explicitly naming AI bots or cleanly separating roles)
        has_explicit_ai_ua = any(b.matched_group not in ("*", "(unmatched)", "(no robots.txt)") for b in bots)
        if has_explicit_ai_ua:
            score += 5
        elif len(blocked_search) == 0:
            score += 2

        if blocked_search:
            names = ", ".join(b.agent for b in blocked_search[:5])
            extra = f" (+{len(blocked_search) - 5} more)" if len(blocked_search) > 5 else ""
            # Classic footgun: blocking wildcard or all AI bots when trying to block training
            findings.append(
                Finding(
                    rule_id="ROB-003",
                    severity="error",
                    category="crawlers",
                    message=f"{len(blocked_search)}/{len(search_bots)} AI search/citation crawlers are blocked ({names}{extra}). Your pages will be omitted from live AI search answers.",
                    remediation="Explicitly `Allow: /` for OAI-SearchBot, ChatGPT-User, PerplexityBot, Claude-SearchBot, and DuckAssistBot even if you block GPTBot/CCBot.",
                )
            )
        else:
            findings.append(
                Finding(
                    rule_id="ROB-004",
                    severity="pass",
                    category="crawlers",
                    message=f"All {len(search_bots)} AI search/citation crawlers are allowed to cite your pages.",
                )
            )

        if blocked_search and allowed_training:
            findings.append(
                Finding(
                    rule_id="ROB-005",
                    severity="warning",
                    category="crawlers",
                    message="Inverted policy detected: some search/citation bots are blocked while training scrapers remain allowed.",
                    remediation="Run `llmsvet audit <target> --emit-fixes ./fixes --policy search-only` to generate a clean split policy.",
                )
            )
    elif policy == "open":
        ratio = len([b for b in bots if b.allowed]) / max(1, len(bots))
        score += round(27 * ratio)
        if blocked_search or blocked_training:
            blocked_names = ", ".join(b.agent for b in (blocked_search + blocked_training)[:6])
            findings.append(
                Finding(
                    rule_id="ROB-006",
                    severity="warning",
                    category="crawlers",
                    message=f"Under `--policy open`, {len(blocked_search) + len(blocked_training)} AI crawlers are currently blocked ({blocked_names}).",
                    remediation="Allow AI search and training user-agents in /robots.txt if you want maximum model + search visibility.",
                )
            )
    elif policy == "strict-block":
        blocked_all = len(blocked_search) + len(blocked_training)
        ratio = blocked_all / max(1, len(bots))
        score += round(27 * ratio)
        still_open = [b.agent for b in bots if b.allowed]
        if still_open:
            findings.append(
                Finding(
                    rule_id="ROB-007",
                    severity="error",
                    category="crawlers",
                    message=f"Under `--policy strict-block`, {len(still_open)} AI crawlers can still access `/` ({', '.join(still_open[:5])}).",
                    remediation="Add explicit `Disallow: /` rules for all 18 AI user-agents.",
                )
            )

    return min(35, max(0, score)), bots, findings


def render_robots_fix(policy: str = "search-only", base_url: Optional[str] = None) -> str:
    lines = [
        "# Generated by llmsvet (https://github.com/SuzumiyaHaruhi719/llmsvet)",
        f"# Policy: {policy}",
        "",
    ]
    search_agents = [a for a, _, r in AI_BOTS if r == "search"]
    training_agents = [a for a, _, r in AI_BOTS if r == "training"]

    if policy == "search-only":
        lines.append("# 1. Allow AI Search & Citation Assistants (live answers with attribution)")
        for ag in search_agents:
            lines.append(f"User-agent: {ag}")
        lines.append("Allow: /")
        lines.append("")
        lines.append("# 2. Opt out of Foundation Model Training Scrapers")
        for ag in training_agents:
            lines.append(f"User-agent: {ag}")
        lines.append("Disallow: /")
    elif policy == "open":
        lines.append("# Allow both AI Search Assistants and Model Training Crawlers")
        for ag in search_agents + training_agents:
            lines.append(f"User-agent: {ag}")
        lines.append("Allow: /")
    else:
        lines.append("# Block all AI Search and Model Training Crawlers")
        for ag in search_agents + training_agents:
            lines.append(f"User-agent: {ag}")
        lines.append("Disallow: /")

    if base_url:
        lines.append("")
        lines.append(f"Sitemap: {base_url.rstrip('/')}/sitemap.xml")
    return "\n".join(lines) + "\n"
