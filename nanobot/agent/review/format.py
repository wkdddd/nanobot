"""Review report data structures and severity utilities."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

SEVERITY_ORDER = ("critical", "high", "medium", "low")


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    file: str
    line: int | None
    title: str
    impact: str
    recommendation: str


@dataclass
class ReviewReport:
    target: str
    mode: str
    dimensions: list[str]
    summary: str
    findings: list[Finding] = field(default_factory=list)
    checks_performed: list[str] = field(default_factory=list)
    checks_skipped: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self._to_dict(), indent=2, ensure_ascii=False)

    def _to_dict(self) -> dict:
        stats = {s: 0 for s in SEVERITY_ORDER}
        for f in self.findings:
            if f.severity in stats:
                stats[f.severity] += 1
        return {
            "target": self.target,
            "mode": self.mode,
            "dimensions": self.dimensions,
            "summary": self.summary,
            "statistics": stats,
            "findings": [
                {
                    "severity": f.severity,
                    "file": f.file,
                    "line": f.line,
                    "title": f.title,
                    "impact": f.impact,
                    "recommendation": f.recommendation,
                }
                for f in self.findings
            ],
            "checks_performed": self.checks_performed,
            "checks_skipped": self.checks_skipped,
            "recommendations": self.recommendations,
        }

    def max_severity(self) -> str | None:
        if not self.findings:
            return None
        for sev in SEVERITY_ORDER:
            if any(f.severity == sev for f in self.findings):
                return sev
        return None
