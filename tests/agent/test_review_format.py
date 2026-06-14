"""Tests for nanobot.agent.review.format module."""
from __future__ import annotations

from nanobot.agent.review.format import SEVERITY_ORDER, Finding, ReviewReport


def test_finding_dataclass_fields() -> None:
    f = Finding(
        severity="high",
        file="src/main.py",
        line=42,
        title="SQL injection",
        impact="Data breach",
        recommendation="Use parameterized queries",
    )
    assert f.severity == "high"
    assert f.file == "src/main.py"
    assert f.line == 42
    assert f.title == "SQL injection"


def test_report_to_json_has_correct_structure() -> None:
    import json

    report = ReviewReport(
        target="https://github.com/test/repo",
        mode="full",
        dimensions=["security", "tests"],
        summary="No critical issues found.",
        findings=[
            Finding("high", "src/auth.py", 10, "Weak hash", "Brute force", "Use bcrypt"),
            Finding("low", "src/utils.py", None, "Unused import", "Dead code", "Remove it"),
        ],
        checks_performed=["security", "tests"],
        checks_skipped=[],
        recommendations=["Fix the weak hash"],
    )
    data = json.loads(report.to_json())

    assert data["target"] == "https://github.com/test/repo"
    assert data["mode"] == "full"
    assert data["statistics"]["high"] == 1
    assert data["statistics"]["low"] == 1
    assert data["statistics"]["critical"] == 0
    assert len(data["findings"]) == 2


def test_max_severity_returns_highest() -> None:
    report = ReviewReport(
        target="repo",
        mode="full",
        dimensions=[],
        summary="",
        findings=[
            Finding("low", "a.py", None, "t", "i", "r"),
            Finding("high", "b.py", None, "t", "i", "r"),
            Finding("medium", "c.py", None, "t", "i", "r"),
        ],
    )
    assert report.max_severity() == "high"


def test_max_severity_empty_returns_none() -> None:
    report = ReviewReport(
        target="repo", mode="full", dimensions=[], summary="", findings=[]
    )
    assert report.max_severity() is None


def test_severity_order_is_descending() -> None:
    assert SEVERITY_ORDER == ("critical", "high", "medium", "low")
