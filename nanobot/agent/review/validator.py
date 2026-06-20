"""Hard validation for review finding candidates."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from loguru import logger

from nanobot.agent.review.types import (
    SEVERITY_ORDER,
    FindingVerdict,
    ReviewDimensionResult,
    ReviewFindingCandidate,
    ReviewFindingVerdict,
)


@dataclass
class ValidationContext:
    """Context for validating candidates against a workspace."""

    workspace: str
    changed_files: list[str] = field(default_factory=list)
    max_line_lookup: bool = True


class ReviewValidator:
    """Validates candidate findings with hard checks (no LLM)."""

    def __init__(self, ctx: ValidationContext) -> None:
        self._ctx = ctx
        self._seen_fingerprints: set[str] = set()
        self.stats = {"accepted": 0, "rejected": 0, "needs_review": 0}

    def validate_candidates(
        self, candidates: list[ReviewFindingCandidate], dimension: str
    ) -> ReviewDimensionResult:
        result = ReviewDimensionResult(dimension=dimension, status="validated")
        for candidate in candidates:
            verdict = self._validate_one(candidate)
            if verdict.verdict == FindingVerdict.ACCEPTED:
                result.accepted.append(candidate)
                self.stats["accepted"] += 1
            elif verdict.verdict == FindingVerdict.REJECTED:
                result.rejected.append((candidate, verdict))
                self.stats["rejected"] += 1
            else:
                result.uncertain.append((candidate, verdict))
                self.stats["needs_review"] += 1
        result.candidates = candidates
        logger.debug(
            "validator dimension={} accepted={} rejected={} needs_review={}",
            dimension,
            len(result.accepted),
            len(result.rejected),
            len(result.uncertain),
        )
        return result

    def _validate_one(self, c: ReviewFindingCandidate) -> ReviewFindingVerdict:
        if c.severity not in SEVERITY_ORDER:
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED,
                reason=f"invalid severity: {c.severity}",
            )
        if not c.file or not c.title:
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED,
                reason="missing required field (file or title)",
            )
        fp = self._fingerprint(c)
        if fp in self._seen_fingerprints:
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED, reason="duplicate finding"
            )
        self._seen_fingerprints.add(fp)

        file_path = os.path.join(self._ctx.workspace, c.file)
        if not os.path.isfile(file_path):
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED,
                reason=f"file not found: {c.file}",
            )
        if c.line is not None and self._ctx.max_line_lookup:
            if not self._line_in_range(file_path, c.line):
                return ReviewFindingVerdict(
                    verdict=FindingVerdict.REJECTED,
                    reason=f"line {c.line} out of range for {c.file}",
                )
        if self._ctx.changed_files and c.file not in self._ctx.changed_files:
            return ReviewFindingVerdict(
                verdict=FindingVerdict.UNCERTAIN,
                reason="file not in changed set",
                missing_evidence="file is outside PR/diff scope",
            )
        if not c.evidence.strip():
            return ReviewFindingVerdict(
                verdict=FindingVerdict.UNCERTAIN,
                reason="no evidence provided",
                missing_evidence="candidate lacks supporting evidence snippet",
            )
        return ReviewFindingVerdict(verdict=FindingVerdict.ACCEPTED)

    def _fingerprint(self, c: ReviewFindingCandidate) -> str:
        key = f"{c.file}:{c.line or 0}:{c.title.lower().strip()}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _line_in_range(self, path: str, line: int) -> bool:
        if line < 1:
            return False
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                count = sum(1 for _ in f)
            return line <= count
        except OSError:
            return False
