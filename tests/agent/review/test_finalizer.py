"""Tests for ReviewFinalizer parsing and orchestration."""
from __future__ import annotations

import json
import os

import pytest

from nanobot.agent.lifecycle_hook import AgentHookContext
from nanobot.agent.review.finalizer import ReviewFinalizer
from nanobot.agent.review.finalizer import ReviewFinalizerHook
from nanobot.agent.review.types import ReviewFindingCandidate


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
            "evidence": "line1",
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
            json.dumps({
                "severity": "medium",
                "file": "src/app.py",
                "line": 2,
                "title": "Issue1",
                "evidence": "line2",
                "impact": "y",
                "recommendation": "z",
            }),
            "some non-json text",
            json.dumps({
                "severity": "low",
                "file": "src/app.py",
                "line": 3,
                "title": "Issue2",
                "evidence": "line3",
                "impact": "b",
                "recommendation": "c",
            }),
        ]
        f = ReviewFinalizer(workspace)
        result = f.ingest_subagent_output("tests", "\n".join(lines))
        assert len(result.accepted) == 2


class TestSemanticVerdicts:
    def test_no_confirmation_needed_when_no_uncertain(self, workspace):
        raw = json.dumps([{
            "severity": "high", "file": "src/app.py", "line": 1,
            "title": "Clear bug", "evidence": "line1", "impact": "bad", "recommendation": "fix",
        }])
        f = ReviewFinalizer(workspace)
        f.ingest_subagent_output("security", raw)
        assert f.get_needs_confirmation() == []

    def test_uncertain_items_remain_in_needs_confirmation(self, workspace):
        raw = json.dumps([{
            "severity": "high", "file": "src/app.py", "line": 1,
            "title": "Maybe", "evidence": "", "impact": "unknown", "recommendation": "check",
        }])
        f = ReviewFinalizer(workspace)
        f.ingest_subagent_output("security", raw)
        needs = f.get_needs_confirmation()
        assert len(needs) == 1
        result = f.finalize("test")
        assert "Maybe" in result.report_markdown
        assert "### Needs Confirmation" in result.report_markdown


class TestFinalize:
    def test_finalize_produces_report(self, workspace):
        raw = json.dumps([{
            "severity": "critical", "file": "src/app.py", "line": 1,
            "title": "RCE", "evidence": "line1", "impact": "Full compromise",
            "recommendation": "Remove exec",
        }])
        f = ReviewFinalizer(workspace)
        f.ingest_subagent_output("security", raw)
        result = f.finalize("myproject")
        assert "## Code Review Report: myproject" in result.report_markdown
        assert "RCE" in result.report_markdown
        assert result.errors == []

    def test_ingest_runner_message_metadata_prefers_raw_result(self, workspace):
        raw_result = json.dumps([{
            "severity": "high",
            "file": "src/app.py",
            "line": 2,
            "title": "Wrapped finding",
            "evidence": "line2",
            "impact": "bad",
            "recommendation": "fix",
        }])
        message = {
            "role": "user",
            "content": "[Subagent 'security' completed]\n\nResult:\n[]",
            "_metadata": {
                "injected_event": "subagent_result",
                "subagent_label": "security",
                "subagent_result": raw_result,
            },
        }
        f = ReviewFinalizer(workspace)
        assert f.ingest_messages([message]) == 1
        result = f.finalize("myproject")
        assert "Wrapped finding" in result.report_markdown

    def test_ingest_persisted_subagent_message_top_level_metadata(self, workspace):
        raw_result = json.dumps([{
            "severity": "high",
            "file": "src/app.py",
            "line": 2,
            "title": "Persisted finding",
            "evidence": "line2",
            "impact": "bad",
            "recommendation": "fix",
        }])
        message = {
            "role": "assistant",
            "content": "[Subagent 'security' completed]\n\nResult:\n[]",
            "injected_event": "subagent_result",
            "subagent_label": "security",
            "subagent_result": raw_result,
        }
        f = ReviewFinalizer(workspace)

        assert f.ingest_messages([message]) == 1
        result = f.finalize("myproject")

        assert "Persisted finding" in result.report_markdown

    def test_ingest_runner_message_parses_wrapped_announce_content(self, workspace):
        raw_result = json.dumps([{
            "severity": "medium",
            "file": "src/app.py",
            "line": 3,
            "title": "Announced finding",
            "evidence": "line3",
            "impact": "bad",
            "recommendation": "fix",
        }])
        message = {
            "role": "user",
            "content": (
                "[Subagent 'tests' completed]\n\n"
                "Result:\n"
                "```json\n"
                f"{raw_result}\n"
                "```\n\n"
                "Summarize this naturally for the user."
            ),
            "_metadata": {
                "injected_event": "subagent_result",
                "subagent_label": "tests",
            },
        }
        f = ReviewFinalizer(workspace)

        assert f.ingest_messages([message]) == 1
        result = f.finalize("myproject")

        assert "Announced finding" in result.report_markdown

    def test_hook_finalizes_content_from_subagent_messages(self, workspace):
        raw_result = json.dumps([{
            "severity": "critical",
            "file": "src/app.py",
            "line": 1,
            "title": "Hook finding",
            "evidence": "line1",
            "impact": "bad",
            "recommendation": "fix",
        }])
        context = AgentHookContext(
            iteration=1,
            messages=[{
                "role": "user",
                "content": "wrapper",
                "_metadata": {
                    "injected_event": "subagent_result",
                    "subagent_label": "security",
                    "subagent_result": raw_result,
                },
            }],
        )
        hook = ReviewFinalizerHook(workspace=workspace, target_name="myproject")

        content = hook.finalize_content(context, "raw assistant prose")

        assert "## Code Review Report: myproject" in content
        assert "Hook finding" in content
