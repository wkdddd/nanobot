"""Shared code-review data types."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

SEVERITY_ORDER = ("critical", "high", "medium", "low")


class ReviewMetaKey:
    """Session metadata keys for code review state."""

    MODE = "review_mode"
    TARGET = "review_target"
    TARGET_TYPE = "review_target_type"
    MODE_VARIANT = "review_mode_variant"
    ACTION = "review_action"
    FOCUS = "review_focus"
    TARGET_PATHS = "review_target_paths"
    MAX_SUBAGENTS = "review_max_subagents"
    EVIDENCE_PROVIDER = "_review_evidence_service"

ReviewTargetType = Literal["auto", "github", "local"]
ReviewDepth = Literal["quick", "full", "deep"]


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
    depth: ReviewDepth
    roles: list[ReviewRole]
    forced_focus: bool
    max_subagents: int
    user_requirements: str = ""
    target_repo: str | None = None
    pr_number: int | None = None
    target_paths: list[str] = field(default_factory=list)
    prefetch_summary: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewFindingCandidate:
    """A candidate finding produced by a dimension subagent."""

    severity: str
    dimension: str
    file: str
    line: int | None
    title: str
    evidence: str
    impact: str
    recommendation: str
    confidence: str = "high"
    source: str = ""


class FindingVerdict(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ReviewFindingVerdict:
    """Verdict on a candidate finding after validation or semantic review."""

    verdict: FindingVerdict
    reason: str = ""
    missing_evidence: str = ""
    suggested_verification: str = ""


@dataclass
class ReviewDimensionResult:
    """Aggregated result for one review dimension."""

    dimension: str
    status: str = "pending"
    candidates: list[ReviewFindingCandidate] = field(default_factory=list)
    accepted: list[ReviewFindingCandidate] = field(default_factory=list)
    rejected: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = field(
        default_factory=list
    )
    uncertain: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = field(
        default_factory=list
    )
    errors: list[str] = field(default_factory=list)


@runtime_checkable
class ReviewEvidenceProvider(Protocol):
    """Structural protocol for evidence retrieval used by prefetch and loop."""

    async def local_context(
        self, *, review_query: str | None, max_results: int, include_tests: bool | None
    ) -> str: ...

    async def local_changed_context(
        self,
        *,
        review_query: str | None,
        target_paths: list[str],
        max_results: int,
        include_tests: bool | None,
    ) -> str: ...

    async def local_targeted_context(
        self,
        *,
        review_query: str | None,
        target_paths: list[str],
        max_results: int,
        include_tests: bool | None,
    ) -> str: ...

    async def github_context(
        self,
        *,
        repo: str,
        ref: str | None,
        tree_pattern: str | None,
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
        trace_id: str,
    ) -> str: ...

    async def github_diff_context(
        self,
        *,
        repo: str,
        pr_number: int,
        target_paths: list[str],
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
        trace_id: str,
    ) -> str: ...
