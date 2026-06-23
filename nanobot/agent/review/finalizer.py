"""Review finalizer: parse subagent results, validate, and render reports."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from nanobot.agent.lifecycle_hook import AgentHook, AgentHookContext
from nanobot.agent.review.judge import ReviewJudge
from nanobot.agent.review.policy import policy_for_depth
from nanobot.agent.review.report import render_review_report
from nanobot.agent.review.types import (
    FindingVerdict,
    ReviewDepth,
    ReviewDimensionResult,
    ReviewFindingCandidate,
    ReviewFindingVerdict,
    ReviewJudgedFinding,
    ReviewModePolicy,
    normalize_review_dimension,
)
from nanobot.agent.review.validator import ReviewValidator, ValidationContext


@dataclass
class ReviewFinalizerResult:
    """Output of the finalizer process."""

    report_markdown: str
    dimensions: list[ReviewDimensionResult] = field(default_factory=list)
    needs_confirmation: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = field(
        default_factory=list
    )
    errors: list[str] = field(default_factory=list)


class ReviewFinalizer:
    """Parses subagent outputs, validates findings, produces final report."""

    _INCOMPLETE_ERROR_PATTERNS = (
        "github api rate limited",
        "failed to fetch github repository context",
        "unable to fetch",
        "could not fetch",
        "context unavailable",
        "no repository context",
        "error:",
        "failed",
        "blocked",
        "disabled",
        "no structured findings",
        "invalid json",
        "无法审查",
        "无法直接拉取",
        "未找到",
    )

    def __init__(
        self,
        workspace: str,
        changed_files: list[str] | None = None,
        *,
        policy: ReviewModePolicy | None = None,
        allowed_dimensions: list[str] | set[str] | None = None,
    ) -> None:
        self._ctx = ValidationContext(
            workspace=workspace,
            changed_files=changed_files or [],
        )
        self._validator = ReviewValidator(self._ctx)
        self._dimensions: list[ReviewDimensionResult] = []
        self._errors: list[str] = []
        self._policy = policy or policy_for_depth("full")
        self._allowed_dimensions = self._normalize_allowed_dimensions(allowed_dimensions)

    def set_allowed_dimensions(self, allowed_dimensions: list[str] | set[str] | None) -> None:
        self._allowed_dimensions = self._normalize_allowed_dimensions(allowed_dimensions)

    @property
    def dimensions(self) -> list[ReviewDimensionResult]:
        return list(self._dimensions)

    def ingest_messages(self, messages: list[dict[str, Any]]) -> int:
        """Ingest structured subagent outputs from runner messages."""
        count = 0
        for message in messages:
            meta = self._subagent_metadata(message)
            if not meta:
                continue
            dimension = str(meta.get("subagent_label") or meta.get("label") or "unknown")
            raw_output = self._subagent_raw_output(message, meta)
            if not raw_output.strip():
                logger.warning("review.finalizer.skip_empty dimension={}", dimension)
                continue
            self.ingest_subagent_output(dimension, raw_output)
            count += 1
        logger.info("review.finalizer.ingest messages={} dimensions={}", len(messages), count)
        return count

    def ingest_subagent_output(self, dimension: str, raw_output: str) -> ReviewDimensionResult:
        """Parse one subagent's raw text output and validate its candidates."""
        normalized_dimension = normalize_review_dimension(dimension) or dimension.strip().lower()
        if self._allowed_dimensions is not None and normalized_dimension not in self._allowed_dimensions:
            logger.warning(
                "review.finalizer.skip_disallowed dimension={} allowed={}",
                dimension,
                sorted(self._allowed_dimensions),
            )
            self._errors.append(f"Skipped disallowed review dimension: {dimension}")
            return ReviewDimensionResult(
                dimension=normalized_dimension or "unknown",
                status="skipped_disallowed",
            )
        dimension = normalized_dimension
        incomplete_reason = self._incomplete_reason(raw_output)
        candidates = self._parse_candidates(dimension, raw_output)
        if self._policy.severities:
            before = len(candidates)
            candidates = [c for c in candidates if c.severity in self._policy.severities]
            if before != len(candidates):
                logger.info(
                    "review.finalizer.filtered_by_policy dimension={} before={} after={} severities={}",
                    dimension,
                    before,
                    len(candidates),
                    self._policy.severities,
                )
        if not candidates:
            result = ReviewDimensionResult(
                dimension=dimension,
                status="incomplete" if incomplete_reason else "no_findings",
                errors=[incomplete_reason] if incomplete_reason else [],
            )
            self._dimensions.append(result)
            return result
        result = self._validator.validate_candidates(candidates, dimension)
        self._dimensions.append(result)
        return result

    def get_needs_confirmation(self) -> list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]]:
        """Return uncertain candidates that should be shown separately in the report."""
        items: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = []
        for d in self._dimensions:
            items.extend(d.uncertain)
        return items

    async def apply_judge(self, judge: ReviewJudge | None) -> None:
        if judge is None or not self._policy.judge_enabled:
            logger.info(
                "review.finalizer.judge.skip enabled={} has_judge={}",
                self._policy.judge_enabled,
                judge is not None,
            )
            self._apply_judged_defaults()
            return
        verdicts = await judge.judge_dimensions(self._dimensions)
        if not verdicts:
            self._apply_judged_defaults()
            return
        for dimension in self._dimensions:
            judged: list[ReviewJudgedFinding] = []
            for candidate in dimension.accepted:
                hard = ReviewFindingVerdict(
                    verdict=FindingVerdict.ACCEPTED,
                    reason="hard validation accepted",
                )
                judged.append(ReviewJudgedFinding(
                    candidate=candidate,
                    hard_verdict=hard,
                    judge_verdict=verdicts.get(ReviewJudge.candidate_id(candidate)),
                ))
            for candidate, hard in dimension.uncertain:
                judged.append(ReviewJudgedFinding(
                    candidate=candidate,
                    hard_verdict=hard,
                    judge_verdict=verdicts.get(ReviewJudge.candidate_id(candidate)),
                ))
            dimension.judged = judged
        logger.info("review.finalizer.judge.applied dimensions={}", len(self._dimensions))

    def _apply_judged_defaults(self) -> None:
        for dimension in self._dimensions:
            if dimension.judged:
                continue
            judged: list[ReviewJudgedFinding] = []
            judged.extend(
                ReviewJudgedFinding(
                    candidate=candidate,
                    hard_verdict=ReviewFindingVerdict(
                        verdict=FindingVerdict.ACCEPTED,
                        reason="hard validation accepted",
                    ),
                )
                for candidate in dimension.accepted
            )
            judged.extend(
                ReviewJudgedFinding(candidate=candidate, hard_verdict=verdict)
                for candidate, verdict in dimension.uncertain
            )
            dimension.judged = judged

    def finalize(self, target_name: str) -> ReviewFinalizerResult:
        """Produce final report markdown from all ingested dimensions."""
        if not self._dimensions:
            self._errors.append("No review dimension results were produced.")
        self._apply_judged_defaults()
        needs_confirmation = self.get_needs_confirmation()
        try:
            report = render_review_report(target_name, self._dimensions, policy=self._policy)
        except Exception as exc:
            logger.error("report rendering failed: {}", exc)
            report = f"## Code Review Report: {target_name}\n\n### Error\n\nReport rendering failed: {exc}\n"
            self._errors.append(str(exc))

        return ReviewFinalizerResult(
            report_markdown=report,
            dimensions=self._dimensions,
            needs_confirmation=needs_confirmation,
            errors=self._errors,
        )

    def _parse_candidates(
        self, dimension: str, raw: str
    ) -> list[ReviewFindingCandidate]:
        """Try JSON array first, fall back to JSONL, then markdown table."""
        candidates = self._try_parse_json_array(dimension, raw)
        if candidates:
            return candidates
        candidates = self._try_parse_jsonl(dimension, raw)
        if candidates:
            return candidates
        candidates = self._try_parse_markdown_table(dimension, raw)
        return candidates

    @classmethod
    def _incomplete_reason(cls, raw: str) -> str:
        text = raw.strip()
        if not text:
            return ""
        lower = text.lower()
        if not any(pattern in lower for pattern in cls._INCOMPLETE_ERROR_PATTERNS):
            if not cls._looks_like_empty_or_structured_output(text):
                return "No structured findings were produced by this reviewer."
            return ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if any(pattern in stripped.lower() for pattern in cls._INCOMPLETE_ERROR_PATTERNS):
                return stripped[:300]
        return text[:300]

    @classmethod
    def _looks_like_empty_or_structured_output(cls, text: str) -> bool:
        stripped = text.strip()
        if stripped in {"[]", "```json\n[]\n```", "```[]```"}:
            return True
        for candidate in cls._json_candidate_texts(stripped):
            try:
                data = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, list):
                return True
        return False

    @staticmethod
    def _normalize_allowed_dimensions(
        allowed_dimensions: list[str] | set[str] | None,
    ) -> set[str] | None:
        if not allowed_dimensions:
            return None
        normalized = {
            dimension
            for item in allowed_dimensions
            if (dimension := normalize_review_dimension(str(item)))
        }
        return normalized or None

    def _try_parse_json_array(
        self, dimension: str, raw: str
    ) -> list[ReviewFindingCandidate]:
        for candidate_text in self._json_candidate_texts(raw):
            try:
                data = json.loads(candidate_text)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, list):
                return [self._dict_to_candidate(d, dimension) for d in data if isinstance(d, dict)]
        return []

    def _try_parse_jsonl(
        self, dimension: str, raw: str
    ) -> list[ReviewFindingCandidate]:
        results: list[ReviewFindingCandidate] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict) and "title" in d:
                    results.append(self._dict_to_candidate(d, dimension))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        return results

    def _try_parse_markdown_table(
        self, dimension: str, raw: str
    ) -> list[ReviewFindingCandidate]:
        """Parse markdown table rows with columns: severity | file | line | title | evidence | impact | recommendation."""
        results: list[ReviewFindingCandidate] = []
        table_row_re = re.compile(
            r"^\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]*)\s*\|\s*([^|]+)\s*\|\s*([^|]*)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|$"
        )
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("|---") or line.startswith("| ---"):
                continue
            m = table_row_re.match(line)
            if not m:
                continue
            sev, file, line_no, title, evidence, impact, rec = (
                g.strip() for g in m.groups()
            )
            if sev.lower() in ("severity", "#", "no"):
                continue
            try:
                ln = int(line_no) if line_no else None
            except ValueError:
                ln = None
            results.append(ReviewFindingCandidate(
                severity=sev.lower(),
                dimension=dimension,
                file=file,
                line=ln,
                title=title,
                evidence=evidence,
                impact=impact,
                recommendation=rec,
            ))
        return results

    def _dict_to_candidate(self, d: dict, dimension: str) -> ReviewFindingCandidate:
        return ReviewFindingCandidate(
            severity=str(d.get("severity", "medium")).lower(),
            dimension=dimension,
            file=str(d.get("file", "")),
            line=d.get("line"),
            title=str(d.get("title", "")),
            evidence=str(d.get("evidence", "")),
            impact=str(d.get("impact", "")),
            recommendation=str(d.get("recommendation", "")),
            confidence=str(d.get("confidence", "high")),
            source=str(d.get("source", "")),
        )

    @staticmethod
    def _subagent_metadata(message: dict[str, Any]) -> dict[str, Any] | None:
        meta = message.get("_metadata")
        if not isinstance(meta, dict):
            meta = message.get("metadata")
        if not isinstance(meta, dict):
            meta = {}
        if message.get("injected_event") == "subagent_result":
            meta = {
                **meta,
                **{
                    key: message[key]
                    for key in (
                        "injected_event",
                        "subagent_task_id",
                        "subagent_label",
                        "subagent_status",
                        "subagent_result",
                    )
                    if key in message
                },
            }
        if meta.get("injected_event") != "subagent_result":
            return None
        return meta

    @staticmethod
    def _subagent_raw_output(message: dict[str, Any], meta: dict[str, Any]) -> str:
        raw = meta.get("subagent_result")
        if isinstance(raw, str):
            return raw
        content = message.get("content", "")
        if not isinstance(content, str):
            return str(content)
        if "Result:" in content:
            content = content.split("Result:", 1)[1]
        if "Summarize this naturally" in content:
            content = content.split("Summarize this naturally", 1)[0]
        return content.strip()

    @classmethod
    def _json_candidate_texts(cls, raw: str) -> list[str]:
        texts: list[str] = []
        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE):
            texts.append(match.group(1).strip())
        body = raw.strip()
        if "Result:" in body:
            body = body.split("Result:", 1)[1].strip()
        texts.append(body)
        texts.extend(cls._balanced_json_arrays(body))
        return [text for text in texts if text]

    @staticmethod
    def _balanced_json_arrays(raw: str) -> list[str]:
        decoder = json.JSONDecoder()
        arrays: list[str] = []
        for idx, ch in enumerate(raw):
            if ch != "[":
                continue
            try:
                parsed, end = decoder.raw_decode(raw[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                arrays.append(raw[idx : idx + end])
        return arrays


class ReviewFinalizerHook(AgentHook):
    """Runner hook that renders a fixed review report from subagent outputs."""

    def __init__(
        self,
        *,
        workspace: str,
        target_name: str,
        changed_files: list[str] | None = None,
        depth: ReviewDepth = "full",
        judge: ReviewJudge | None = None,
        allowed_dimensions: list[str] | set[str] | None = None,
    ) -> None:
        super().__init__()
        self._target_name = target_name
        self._policy = policy_for_depth(depth)
        self._finalizer = ReviewFinalizer(
            workspace,
            changed_files,
            policy=self._policy,
            allowed_dimensions=allowed_dimensions,
        )
        self._rendered = False
        self._seen_subagent_results: set[str] = set()
        self._judged = False
        self._judge = judge

    def set_allowed_dimensions(self, allowed_dimensions: list[str] | set[str] | None) -> None:
        self._finalizer.set_allowed_dimensions(allowed_dimensions)

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._rendered:
            return
        ingested = self._ensure_ingested(context)
        if ingested <= 0:
            return
        self._judged = False
        await self._finalizer.apply_judge(self._judge)
        self._judged = True

    def _ensure_ingested(self, context: AgentHookContext) -> int:
        messages: list[dict[str, Any]] = []
        for message in context.messages:
            meta = ReviewFinalizer._subagent_metadata(message)
            if not meta:
                continue
            raw = ReviewFinalizer._subagent_raw_output(message, meta)
            key = self._subagent_result_key(meta, raw)
            if key in self._seen_subagent_results:
                continue
            self._seen_subagent_results.add(key)
            messages.append(message)
        if not messages:
            return 0
        return self._finalizer.ingest_messages(messages)

    @staticmethod
    def _subagent_result_key(meta: dict[str, Any], raw: str) -> str:
        task_id = meta.get("subagent_task_id")
        if isinstance(task_id, str) and task_id:
            return task_id
        label = str(meta.get("subagent_label") or meta.get("label") or "unknown")
        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{label}:{digest}"

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        if self._rendered:
            return content
        ingested = self._ensure_ingested(context)
        if ingested == 0 and not self._finalizer.dimensions:
            logger.warning("review.finalizer.no_subagent_results target={}", self._target_name)
            return content
        result = self._finalizer.finalize(self._target_name)
        self._rendered = True
        context.content_replaced = True
        logger.info(
            "review.finalizer.rendered target={} dimensions={} needs_confirmation={} errors={}",
            self._target_name,
            len(result.dimensions),
            len(result.needs_confirmation),
            len(result.errors),
        )
        return result.report_markdown
