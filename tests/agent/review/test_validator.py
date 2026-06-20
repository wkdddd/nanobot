"""Tests for ReviewValidator hard validation logic."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from nanobot.agent.review.types import FindingVerdict, ReviewFindingCandidate
from nanobot.agent.review.validator import ReviewValidator, ValidationContext


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with sample files."""
    (tmp_path / "src").mkdir()
    src_file = tmp_path / "src" / "main.py"
    src_file.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
    return str(tmp_path)


def _make_candidate(**overrides) -> ReviewFindingCandidate:
    defaults = {
        "severity": "high",
        "dimension": "security",
        "file": "src/main.py",
        "line": 2,
        "title": "SQL Injection risk",
        "evidence": "line2",
        "impact": "Remote code execution",
        "recommendation": "Use parameterized queries",
    }
    defaults.update(overrides)
    return ReviewFindingCandidate(**defaults)


class TestValidatorAccepts:
    def test_valid_candidate_accepted(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        result = v.validate_candidates([_make_candidate()], "security")
        assert len(result.accepted) == 1
        assert len(result.rejected) == 0
        assert result.status == "validated"

    def test_windows_separator_candidate_matches_changed_file(self, workspace):
        v = ReviewValidator(ValidationContext(
            workspace=workspace,
            changed_files=["src/main.py"],
        ))
        c = _make_candidate(file="src\\main.py")

        result = v.validate_candidates([c], "security")

        assert len(result.accepted) == 1
        assert len(result.uncertain) == 0

    def test_no_line_still_accepted(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(line=None)
        result = v.validate_candidates([c], "security")
        assert len(result.accepted) == 1


class TestValidatorRejects:
    def test_invalid_severity_rejected(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(severity="extreme")
        result = v.validate_candidates([c], "security")
        assert len(result.rejected) == 1
        assert "invalid severity" in result.rejected[0][1].reason

    def test_missing_file_field_rejected(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(file="")
        result = v.validate_candidates([c], "security")
        assert len(result.rejected) == 1

    def test_file_not_found_rejected(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(file="nonexistent.py")
        result = v.validate_candidates([c], "security")
        assert len(result.rejected) == 1
        assert "not found" in result.rejected[0][1].reason

    def test_absolute_path_rejected(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(file=str(Path(workspace) / "src" / "main.py"))
        result = v.validate_candidates([c], "security")
        assert len(result.rejected) == 1
        assert "outside workspace" in result.rejected[0][1].reason

    def test_parent_traversal_rejected(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(file="../outside.py")
        result = v.validate_candidates([c], "security")
        assert len(result.rejected) == 1
        assert "outside workspace" in result.rejected[0][1].reason

    def test_line_out_of_range_rejected(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(line=999)
        result = v.validate_candidates([c], "security")
        assert len(result.rejected) == 1
        assert "out of range" in result.rejected[0][1].reason

    def test_duplicate_rejected(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c1 = _make_candidate()
        c2 = _make_candidate()
        result = v.validate_candidates([c1, c2], "security")
        assert len(result.accepted) == 1
        assert len(result.rejected) == 1
        assert "duplicate" in result.rejected[0][1].reason


class TestValidatorUncertain:
    def test_file_outside_changed_set(self, workspace):
        v = ReviewValidator(ValidationContext(
            workspace=workspace, changed_files=["other.py"]
        ))
        c = _make_candidate()
        result = v.validate_candidates([c], "security")
        assert len(result.uncertain) == 1
        assert "not in changed set" in result.uncertain[0][1].reason

    def test_empty_evidence_uncertain(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(evidence="")
        result = v.validate_candidates([c], "security")
        assert len(result.uncertain) == 1
        assert "no evidence" in result.uncertain[0][1].reason

    def test_evidence_not_found_uncertain(self, workspace):
        v = ReviewValidator(ValidationContext(workspace=workspace))
        c = _make_candidate(evidence="not present in file")
        result = v.validate_candidates([c], "security")
        assert len(result.uncertain) == 1
        assert "evidence not found" in result.uncertain[0][1].reason
