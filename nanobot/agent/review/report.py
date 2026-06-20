"""Fixed Markdown report renderer for code review results."""
from __future__ import annotations

from nanobot.agent.review.types import (
    SEVERITY_ORDER,
    ReviewDimensionResult,
    ReviewFindingCandidate,
    ReviewFindingVerdict,
)


def render_review_report(
    target_name: str,
    dimensions: list[ReviewDimensionResult],
) -> str:
    """Render final Markdown report from validated dimension results."""
    all_accepted = _collect_accepted(dimensions)
    all_uncertain = _collect_uncertain(dimensions)
    all_rejected = _collect_rejected(dimensions)

    stats = _severity_stats(all_accepted)
    summary = _build_summary(target_name, stats, dimensions)

    sections: list[str] = []
    sections.append(f"## Code Review Report: {target_name}\n")
    sections.append(f"### Executive Summary\n\n{summary}\n")
    sections.append(_render_findings(all_accepted))
    sections.append(_render_checks_performed(dimensions))
    if all_uncertain:
        sections.append(_render_needs_confirmation(all_uncertain))
    if all_rejected:
        sections.append(_render_rejected_summary(all_rejected))
    sections.append(_render_recommendations(all_accepted))
    return "\n".join(sections)


def _collect_accepted(dims: list[ReviewDimensionResult]) -> list[ReviewFindingCandidate]:
    findings: list[ReviewFindingCandidate] = []
    for d in dims:
        findings.extend(d.accepted)
    findings.sort(key=lambda f: SEVERITY_ORDER.index(f.severity) if f.severity in SEVERITY_ORDER else 99)
    return findings


def _collect_uncertain(
    dims: list[ReviewDimensionResult],
) -> list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]]:
    items: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = []
    for d in dims:
        items.extend(d.uncertain)
    return items


def _collect_rejected(
    dims: list[ReviewDimensionResult],
) -> list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]]:
    items: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = []
    for d in dims:
        items.extend(d.rejected)
    return items


def _severity_stats(findings: list[ReviewFindingCandidate]) -> dict[str, int]:
    stats = {s: 0 for s in SEVERITY_ORDER}
    for f in findings:
        if f.severity in stats:
            stats[f.severity] += 1
    return stats


def _build_summary(
    target: str, stats: dict[str, int], dims: list[ReviewDimensionResult]
) -> str:
    total = sum(stats.values())
    if total == 0:
        return "No actionable issues found."
    parts: list[str] = []
    for sev in SEVERITY_ORDER:
        if stats[sev] > 0:
            parts.append(f"{stats[sev]} {sev}")
    dim_names = ", ".join(d.dimension for d in dims if d.status == "validated")
    return f"Found {total} issues ({', '.join(parts)}). Dimensions reviewed: {dim_names}."


def _render_findings(findings: list[ReviewFindingCandidate]) -> str:
    if not findings:
        return "### Findings\n\nNo actionable issues found.\n"
    lines = ["### Findings\n"]
    current_sev = ""
    idx = 0
    for f in findings:
        if f.severity != current_sev:
            current_sev = f.severity
            lines.append(f"#### {current_sev.capitalize()}\n")
            lines.append("| # | File | Issue | Impact |")
            lines.append("|---|------|-------|--------|")
        idx += 1
        loc = f"{f.file}:{f.line}" if f.line else f.file
        lines.append(f"| {idx} | {loc} | {f.title} | {f.impact} |")
    lines.append("")
    lines.append("**Details:**\n")
    for i, f in enumerate(findings, 1):
        loc = f"{f.file}:{f.line}" if f.line else f.file
        lines.append(f"{i}. **{f.title}** ({loc})")
        lines.append(f"   - Impact: {f.impact}")
        lines.append(f"   - Recommendation: {f.recommendation}")
    lines.append("")
    return "\n".join(lines)


def _render_checks_performed(dims: list[ReviewDimensionResult]) -> str:
    lines = ["### Checks Performed\n"]
    for d in dims:
        if d.status == "validated":
            lines.append(f"- [x] {d.dimension}")
        else:
            lines.append(f"- [ ] {d.dimension} - {d.status}")
    lines.append("")
    return "\n".join(lines)


def _render_needs_confirmation(
    items: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]],
) -> str:
    lines = ["### Needs Confirmation\n"]
    lines.append("The following items could not be definitively verified:\n")
    for c, v in items:
        loc = f"{c.file}:{c.line}" if c.line else c.file
        lines.append(f"- **{c.title}** ({loc}) — {v.reason}")
    lines.append("")
    return "\n".join(lines)


def _render_rejected_summary(
    items: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]],
) -> str:
    lines = ["### Rejected/Skipped Summary\n"]
    lines.append(f"{len(items)} candidates rejected during validation:\n")
    for c, v in items[:10]:
        lines.append(f"- {c.title} ({c.file}) — {v.reason}")
    if len(items) > 10:
        lines.append(f"- ... and {len(items) - 10} more")
    lines.append("")
    return "\n".join(lines)


def _render_recommendations(findings: list[ReviewFindingCandidate]) -> str:
    lines = ["### Recommendations\n"]
    if not findings:
        lines.append("No priority fixes needed.\n")
        return "\n".join(lines)
    critical_high = [f for f in findings if f.severity in ("critical", "high")]
    for i, f in enumerate(critical_high[:5], 1):
        lines.append(f"{i}. {f.recommendation}")
    if not critical_high:
        lines.append("1. Address medium-severity findings when convenient.")
    lines.append("")
    return "\n".join(lines)
