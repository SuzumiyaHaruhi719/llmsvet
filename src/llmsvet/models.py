"""Data models for llmsvet audit results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Finding:
    rule_id: str
    severity: str  # "error" | "warning" | "info" | "pass"
    category: str  # "crawlers" | "llms_txt" | "schema"
    message: str
    remediation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BotStatus:
    agent: str
    vendor: str
    role: str  # "search" | "training"
    allowed: bool
    matched_group: str
    matched_rule: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AuditReport:
    target: str
    mode: str  # "local" | "remote"
    policy: str  # "search-only" | "open" | "strict-block"
    total_score: int
    grade: str
    crawler_score: int
    crawler_max: int
    llms_score: int
    llms_max: int
    schema_score: int
    schema_max: int
    bots: List[BotStatus] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    emitted_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "mode": self.mode,
            "policy": self.policy,
            "score": {
                "total": self.total_score,
                "max": 100,
                "grade": self.grade,
                "crawlers": {"score": self.crawler_score, "max": self.crawler_max},
                "llms_txt": {"score": self.llms_score, "max": self.llms_max},
                "schema": {"score": self.schema_score, "max": self.schema_max},
            },
            "bots": [b.to_dict() for b in self.bots],
            "findings": [f.to_dict() for f in self.findings],
            "metadata": self.metadata,
            "emitted_files": list(self.emitted_files),
        }


def compute_grade(score: int) -> str:
    if score >= 95:
        return "A+"
    if score >= 85:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 45:
        return "D"
    return "F"
