"""Tests for ReviewFinalizer parsing and orchestration."""
from __future__ import annotations

import json
import os

import pytest

from nanobot.agent.review.finalizer import ReviewFinalizer
from nanobot.agent.review.types import FindingVerdict, ReviewFindingCandidate


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
    return str(tmp_path)


class TestParsingJsonArray:
    def test_parse_json_array(self, workspace):
        raw = json.dumps([{
            "severity": "high",
            "file": "src/app.py",
            "line": 1,
            "title": "Injection",
            "evidence": "query = ...",
            "impact": "RCE",
            "recommendation": "Parameterize",
        }])
        f = ReviewFinalizer(workspace)
        result = f.ingest_subagent_output("security", raw)
        assert len(result.accepted) == 1
        assert result.accepted[0].title == "Injection"

    def test_parse_empty_array(self, workspace):
        f = ReviewFinalizer(workspace)
        result = f.ingest_subagent_output("security", "[]")
        assert result.status == "no_findings"


class TestParsingJsonl:
    def test_parse_jsonl_lines(self, workspace):
        lines = [
            json.dumps({"severity": "medium", "file": "src/app.py", "line": 2,
                        "title": "Issue1", "evidence": "x", "impact": "y", "recommendation": "z"}),
            "some non-json text",
            json.dumps({"severity": "low", "file": "src/app.py", "line": 3,
                        "title": "Issue2", "evidence": "a", "impact": "b", "recommendation": "c"}),
        ]
        f = ReviewFinalizer(workspace)
        result = f.ingest_subagent_output("tests", "\n".join(lines))
        assert len(result.accepted) == 2


class TestSemanticVerdicts:
    def test_no_review_needed_when_no_uncertain(self, workspace):
        raw = json.dumps([{
            "severity": "high", "file": "src/app.py", "line": 1,
            "title": "Clear bug", "evidence": "proof", "impact": "bad", "recommendation": "fix",
        }])
        f = ReviewFinalizer(workspace)
        f.ingest_subagent_output("security", raw)
        assert f.get_needs_review() == []

    def test_apply_accept_verdict(self, workspace):
        raw = json.dumps([{
            "severity": "high", "file": "src/app.py", "line": 1,
            "title": "Maybe", "evidence": "", "impact": "unknown", "recommendation": "check",
        }])
        f = ReviewFinalizer(workspace)
        f.ingest_subagent_output("security", raw)
        needs = f.get_needs_review()
        assert len(needs) == 1
        f.apply_semantic_verdicts([(needs[0][0], FindingVerdict.ACCEPTED)])
        result = f.finalize("test")
        assert "Maybe" in result.report_markdown
        assert "### Needs Confirmation" not in result.report_markdown

    def test_apply_reject_verdict(self, workspace):
        raw = json.dumps([{
            "severity": "high", "file": "src/app.py", "line": 1,
            "title": "FalsePos", "evidence": "", "impact": "none", "recommendation": "skip",
        }])
        f = ReviewFinalizer(workspace)
        f.ingest_subagent_output("security", raw)
        needs = f.get_needs_review()
        f.apply_semantic_verdicts([(needs[0][0], FindingVerdict.REJECTED)])
        result = f.finalize("test")
        assert "FalsePos" not in result.report_markdown.split("### Findings")[1].split("### Rejected")[0]


class TestFinalize:
    def test_finalize_produces_report(self, workspace):
        raw = json.dumps([{
            "severity": "critical", "file": "src/app.py", "line": 1,
            "title": "RCE", "evidence": "exec(input())", "impact": "Full compromise",
            "recommendation": "Remove exec",
        }])
        f = ReviewFinalizer(workspace)
        f.ingest_subagent_output("security", raw)
        result = f.finalize("myproject")
        assert "## Code Review Report: myproject" in result.report_markdown
        assert "RCE" in result.report_markdown
        assert result.errors == []
