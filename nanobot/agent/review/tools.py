"""Review-specific tools for structured finding reporting."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema


@dataclass
class ReviewFinding:
    severity: str
    file: str
    line: int | None
    title: str
    impact: str
    recommendation: str
    knowledge_note: str = ""
    verification: str = ""


@dataclass
class ReviewReport:
    """Accumulates findings from tool calls."""
    executive_summary: str = ""
    findings: list[ReviewFinding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    checks_recommended: list[str] = field(default_factory=list)

    _SEVERITY_ORDER: dict[str, int] = field(
        default_factory=lambda: {"critical": 0, "high": 1, "medium": 2, "low": 3},
        init=False, repr=False,
    )

    def sorted_findings(self) -> list[ReviewFinding]:
        return sorted(self.findings, key=lambda f: self._SEVERITY_ORDER.get(f.severity, 99))

    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


_active_report: ReviewReport | None = None


def set_active_report(report: ReviewReport) -> None:
    global _active_report
    _active_report = report


def get_active_report() -> ReviewReport | None:
    return _active_report


def clear_active_report() -> None:
    global _active_report
    _active_report = None


@tool_parameters(
    tool_parameters_schema(
        severity=StringSchema(
            "Finding severity: critical, high, medium, or low",
            enum=["critical", "high", "medium", "low"],
        ),
        file=StringSchema("File path relative to repository root"),
        line=StringSchema("Line number (optional)", nullable=True),
        title=StringSchema("Short title of the finding"),
        impact=StringSchema("Description of the impact"),
        recommendation=StringSchema("What should be done to fix it"),
        knowledge_note=StringSchema("Explanation of the concept for learning (optional)", nullable=True),
        verification=StringSchema("How to verify the fix (optional)", nullable=True),
        required=["severity", "file", "title", "impact", "recommendation"],
    )
)
class ReportFindingTool(Tool):
    """Report a single code review finding with structured metadata."""
    _scopes = {"core", "subagent"}
    _plugin_discoverable = False

    @property
    def name(self) -> str:
        return "report_finding"

    @property
    def description(self) -> str:
        return (
            "Report a code review finding. Call once per finding with severity, "
            "file location, impact, and recommendation. Findings are collected and "
            "rendered into the final structured report."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        report = get_active_report()
        if report is None:
            return "Error: no active review session"
        finding = ReviewFinding(
            severity=kwargs["severity"].lower(),
            file=kwargs["file"],
            line=int(kwargs["line"]) if kwargs.get("line") else None,
            title=kwargs["title"],
            impact=kwargs["impact"],
            recommendation=kwargs["recommendation"],
            knowledge_note=kwargs.get("knowledge_note") or "",
            verification=kwargs.get("verification") or "",
        )
        report.findings.append(finding)
        return f"Finding recorded: [{finding.severity}] {finding.title}"


@tool_parameters(
    tool_parameters_schema(
        executive_summary=StringSchema("2-3 sentence overview of the review"),
        checks_run=StringSchema("Comma-separated list of checks that were run", nullable=True),
        checks_recommended=StringSchema(
            "Comma-separated list of checks recommended", nullable=True
        ),
        required=["executive_summary"],
    )
)
class ReportSummaryTool(Tool):
    """Finalize the review with an executive summary and verification info."""
    _scopes = {"core", "subagent"}
    _plugin_discoverable = False

    @property
    def name(self) -> str:
        return "report_summary"

    @property
    def description(self) -> str:
        return (
            "Finalize the code review report. Call ONCE at the end after all "
            "findings have been reported. Provide the executive summary and "
            "any checks that were run or recommended."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        report = get_active_report()
        if report is None:
            return "Error: no active review session"
        report.executive_summary = kwargs["executive_summary"]
        if kwargs.get("checks_run"):
            report.checks_run = [c.strip() for c in kwargs["checks_run"].split(",")]
        if kwargs.get("checks_recommended"):
            report.checks_recommended = [
                c.strip() for c in kwargs["checks_recommended"].split(",")
            ]
        count = len(report.findings)
        return f"Review finalized: {count} findings recorded."
