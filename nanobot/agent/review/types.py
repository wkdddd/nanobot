"""Shared code-review data types."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

SEVERITY_ORDER = ("critical", "high", "medium", "low")

ReviewTargetType = Literal["auto", "github", "local"]
ReviewMode = Literal["quick", "full", "deep"]


class ReviewAction(StrEnum):
    FULL_REPO = "full_repo"
    PR_DIFF = "pr_diff"
    LOCAL_CHANGED = "local_changed"


def review_action_values() -> tuple[str, ...]:
    return tuple(action.value for action in ReviewAction)


@dataclass(frozen=True, slots=True)
class ReviewRole:
    name: str
    label: str
    description: str


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

    def _to_dict(self) -> dict[str, object]:
        stats = {s: 0 for s in SEVERITY_ORDER}
        for finding in self.findings:
            if finding.severity in stats:
                stats[finding.severity] += 1
        return {
            "target": self.target,
            "mode": self.mode,
            "dimensions": self.dimensions,
            "summary": self.summary,
            "statistics": stats,
            "findings": [
                {
                    "severity": finding.severity,
                    "file": finding.file,
                    "line": finding.line,
                    "title": finding.title,
                    "impact": finding.impact,
                    "recommendation": finding.recommendation,
                }
                for finding in self.findings
            ],
            "checks_performed": self.checks_performed,
            "checks_skipped": self.checks_skipped,
            "recommendations": self.recommendations,
        }

    def max_severity(self) -> str | None:
        if not self.findings:
            return None
        for severity in SEVERITY_ORDER:
            if any(finding.severity == severity for finding in self.findings):
                return severity
        return None


DEFAULT_REVIEW_ROLES: dict[str, ReviewRole] = {
    "security": ReviewRole(
        name="security",
        label="Security Reviewer",
        description=(
            "Review authentication, authorization, injection risks, secret handling, "
            "unsafe deserialization, path traversal, SSRF, dependency risks, and data exposure."
        ),
    ),
    "tests": ReviewRole(
        name="tests",
        label="Test Reviewer",
        description=(
            "Review test coverage, missing edge cases, brittle tests, regression risk, "
            "testability, and suggested verification commands."
        ),
    ),
    "architecture": ReviewRole(
        name="architecture",
        label="Architecture Reviewer",
        description=(
            "Review module boundaries, coupling, maintainability, data flow, abstractions, "
            "configuration shape, and long-term design risks."
        ),
    ),
    "performance": ReviewRole(
        name="performance",
        label="Performance Reviewer",
        description=(
            "Review algorithmic complexity, I/O hot paths, concurrency, caching, memory use, "
            "database/query behavior, and scalability risks."
        ),
    ),
}

OPTIONAL_REVIEW_ROLES: dict[str, ReviewRole] = {
    "bug-risk": ReviewRole(
        name="bug-risk",
        label="Bug Risk Reviewer",
        description=(
            "Review logic errors, boundary conditions, null/undefined handling, exception paths, "
            "state inconsistency, race conditions, and off-by-one errors."
        ),
    ),
    "maintainability": ReviewRole(
        name="maintainability",
        label="Maintainability Reviewer",
        description=(
            "Review code duplication, function complexity, unclear abstractions, naming clarity, "
            "readability, and long-term maintenance burden."
        ),
    ),
    "dependency": ReviewRole(
        name="dependency",
        label="Dependency Reviewer",
        description=(
            "Review dependency versions, known vulnerabilities, license risks, supply chain "
            "security, unnecessary dependencies, and version pinning."
        ),
    ),
}

ALL_REVIEW_ROLES: dict[str, ReviewRole] = {**DEFAULT_REVIEW_ROLES, **OPTIONAL_REVIEW_ROLES}


@dataclass(frozen=True, slots=True)
class ReviewPlan:
    target: str | None
    target_name: str | None
    target_type: ReviewTargetType
    action: ReviewAction
    mode: ReviewMode
    roles: list[ReviewRole]
    forced_focus: bool
    output_format: str
    max_subagents: int
    user_requirements: str = ""
    target_repo: str | None = None
    pr_number: int | None = None
    target_paths: list[str] = field(default_factory=list)
    prefetch_summary: str | None = None
