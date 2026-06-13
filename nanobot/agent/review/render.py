"""Render a ReviewReport into Markdown or JSON."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from nanobot.agent.review.tools import ReviewReport


def render_markdown(report: ReviewReport, target_name: str = "") -> str:
    lines: list[str] = []
    lines.append(f"# Code Review: {target_name}\n")
    lines.append(f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n")

    lines.append("## Executive Summary\n")
    lines.append(report.executive_summary or "_No summary provided._")
    lines.append("")

    lines.append("## Findings\n")
    if not report.findings:
        lines.append("_No findings._\n")
    else:
        for severity in ("critical", "high", "medium", "low"):
            group = [f for f in report.sorted_findings() if f.severity == severity]
            if not group:
                continue
            lines.append(f"### {severity.title()} ({len(group)})\n")
            for f in group:
                loc = f"`{f.file}:{f.line}`" if f.line else f"`{f.file}`"
                lines.append(f"#### {f.title}")
                lines.append(f"- **Location:** {loc}")
                lines.append(f"- **Impact:** {f.impact}")
                lines.append(f"- **Recommendation:** {f.recommendation}")
                if f.knowledge_note:
                    lines.append(f"- **Note:** {f.knowledge_note}")
                if f.verification:
                    lines.append(f"- **Verification:** {f.verification}")
                lines.append("")

    lines.append("## Verification\n")
    if report.checks_run:
        lines.append("**Checks run:**")
        for c in report.checks_run:
            lines.append(f"- {c}")
        lines.append("")
    if report.checks_recommended:
        lines.append("**Checks recommended:**")
        for c in report.checks_recommended:
            lines.append(f"- {c}")
        lines.append("")
    if not report.checks_run and not report.checks_recommended:
        lines.append("_No verification info provided._\n")

    total = len(report.findings)
    by_sev: dict[str, int] = {}
    for f in report.findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    stats = ", ".join(f"{s}: {n}" for s, n in sorted(by_sev.items()))
    lines.append(f"---\n*Total findings: {total} ({stats})*\n")

    return "\n".join(lines)


def render_json(report: ReviewReport, target_name: str = "") -> str:
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": target_name,
        "executive_summary": report.executive_summary,
        "findings": [
            {
                "severity": f.severity,
                "file": f.file,
                "line": f.line,
                "title": f.title,
                "impact": f.impact,
                "recommendation": f.recommendation,
                "knowledge_note": f.knowledge_note or None,
                "verification": f.verification or None,
            }
            for f in report.sorted_findings()
        ],
        "stats": {
            "total": len(report.findings),
            "critical": sum(1 for f in report.findings if f.severity == "critical"),
            "high": sum(1 for f in report.findings if f.severity == "high"),
            "medium": sum(1 for f in report.findings if f.severity == "medium"),
            "low": sum(1 for f in report.findings if f.severity == "low"),
        },
        "verification": {
            "checks_run": report.checks_run,
            "checks_recommended": report.checks_recommended,
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
