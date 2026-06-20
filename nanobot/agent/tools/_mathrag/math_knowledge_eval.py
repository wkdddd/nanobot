"""LLM-based evaluation harness for MathRAG answers."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nanobot.agent.math_qa import KnowledgeHit, MathKnowledgeBase, build_math_qa_prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MathEvalSample:
    id: str
    question: str
    reference_answer: str
    expected_sources: tuple[str, ...] = ()
    answer: str = ""
    notes: str = ""


@dataclass(frozen=True)
class MathEvalScores:
    context_relevance_score: float
    faithfulness_score: float
    answer_relevance_score: float
    overall_score: float
    reason: str
    failure_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MathEvalResult:
    sample: MathEvalSample
    answer: str
    hits: tuple[KnowledgeHit, ...]
    scores: MathEvalScores | None
    error: str = ""
    generation_usage: dict[str, int] = field(default_factory=dict)
    judge_usage: dict[str, int] = field(default_factory=dict)


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _usage_dict(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def load_eval_dataset(path: Path) -> list[MathEvalSample]:
    samples: list[MathEvalSample] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL row: {exc}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"{path}:{line_no}: row must be a JSON object")
            sample_id = str(raw.get("id") or line_no)
            question = str(raw.get("question") or "").strip()
            reference_answer = str(raw.get("reference_answer") or "").strip()
            if not question:
                raise ValueError(f"{path}:{line_no}: question is required")
            if not reference_answer:
                raise ValueError(f"{path}:{line_no}: reference_answer is required")
            expected_sources = raw.get("expected_sources") or []
            if isinstance(expected_sources, str):
                expected_sources = [expected_sources]
            if not isinstance(expected_sources, list):
                raise ValueError(f"{path}:{line_no}: expected_sources must be a list or string")
            samples.append(
                MathEvalSample(
                    id=sample_id,
                    question=question,
                    reference_answer=reference_answer,
                    expected_sources=tuple(str(item) for item in expected_sources),
                    answer=str(raw.get("answer") or "").strip(),
                    notes=str(raw.get("notes") or "").strip(),
                )
            )
    return samples


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.S)
    if fenced:
        stripped = fenced.group(1)
    elif "{" in stripped and "}" in stripped:
        stripped = stripped[stripped.find("{") : stripped.rfind("}") + 1]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json

            data = json.loads(repair_json(stripped))
        except Exception as exc:
            raise ValueError("judge response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("judge response JSON must be an object")
    return data


def parse_judge_response(text: str) -> MathEvalScores:
    data = _extract_json_object(text)
    failure_tags = data.get("failure_tags") or []
    if isinstance(failure_tags, str):
        failure_tags = [failure_tags]
    if not isinstance(failure_tags, list):
        failure_tags = []
    context_score = _clamp_score(data.get("context_relevance_score"))
    faithfulness_score = _clamp_score(data.get("faithfulness_score"))
    answer_score = _clamp_score(data.get("answer_relevance_score"))
    overall_raw = data.get("overall_score")
    overall_score = (
        _clamp_score(overall_raw)
        if overall_raw is not None
        else round((context_score + faithfulness_score + answer_score) / 3, 4)
    )
    return MathEvalScores(
        context_relevance_score=context_score,
        faithfulness_score=faithfulness_score,
        answer_relevance_score=answer_score,
        overall_score=overall_score,
        reason=str(data.get("reason") or "").strip(),
        failure_tags=tuple(str(item) for item in failure_tags if str(item).strip()),
    )


def _context_from_hits(hits: list[KnowledgeHit]) -> str:
    if not hits:
        return "知识库中未检索到相关内容。"
    blocks: list[str] = []
    for i, hit in enumerate(hits, 1):
        content = hit.content.strip()
        if len(content) > 1800:
            content = content[:1800].rstrip() + "..."
        blocks.append(
            f"[{i}] {hit.title}\n"
            f"source: {hit.citation()}\n"
            f"score: {hit.score:.4f}\n"
            f"content:\n{content}"
        )
    return "\n\n".join(blocks)


def _judge_prompt(sample: MathEvalSample, hits: list[KnowledgeHit], answer: str) -> str:
    expected_sources = "\n".join(f"- {item}" for item in sample.expected_sources) or "- 无"
    return f"""你是 MathRAG 评估器。请只根据输入材料评分，不要补充外部知识。

请评估三个维度，每项分数为 0 到 1：
- context_relevance_score：检索上下文是否覆盖问题相关知识点、公式、例题或期望来源。
- faithfulness_score：答案是否被检索上下文支持，是否存在编造、错误引用或与上下文冲突。
- answer_relevance_score：答案是否直接回答问题，是否给出数学题需要的关键步骤或最终结论。

评分要严格。若答案声称来自知识库但上下文不支持，应降低 faithfulness_score。

只返回 JSON，不要返回 Markdown，不要解释 JSON 之外的内容。格式：
{{
  "context_relevance_score": 0.0,
  "faithfulness_score": 0.0,
  "answer_relevance_score": 0.0,
  "overall_score": 0.0,
  "reason": "一句话说明主要扣分点或优点",
  "failure_tags": ["可选标签"]
}}

问题：
{sample.question}

参考答案：
{sample.reference_answer}

期望来源：
{expected_sources}

检索上下文：
{_context_from_hits(hits)}

待评估答案：
{answer}
"""


async def _generate_answer(
    *,
    provider: Any,
    model: str,
    question: str,
    hits: list[KnowledgeHit],
    max_tokens: int,
) -> tuple[str, dict[str, int]]:
    response = await provider.chat_with_retry(
        messages=[
            {"role": "system", "content": build_math_qa_prompt(hits)},
            {"role": "user", "content": question},
        ],
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return (response.content or "").strip(), _usage_dict(getattr(response, "usage", {}))


async def _judge_answer(
    *,
    provider: Any,
    model: str,
    sample: MathEvalSample,
    hits: list[KnowledgeHit],
    answer: str,
    max_tokens: int,
) -> tuple[MathEvalScores, dict[str, int]]:
    response = await provider.chat_with_retry(
        messages=[
            {"role": "system", "content": "你是严格的 MathRAG 评估器，只输出 JSON。"},
            {"role": "user", "content": _judge_prompt(sample, hits, answer)},
        ],
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    scores = parse_judge_response(response.content or "")
    return scores, _usage_dict(getattr(response, "usage", {}))


async def evaluate_samples(
    samples: list[MathEvalSample],
    *,
    workspace: Path,
    provider: Any,
    model: str,
    judge_provider: Any | None = None,
    judge_model: str | None = None,
    embedding_config: Any | None = None,
    rerank_config: Any | None = None,
    qdrant_config: Any | None = None,
    top_k: int = 4,
    skip_generation: bool = False,
    max_answer_tokens: int = 2048,
    max_judge_tokens: int = 768,
) -> list[MathEvalResult]:
    kb = MathKnowledgeBase(
        workspace,
        embedding_config=embedding_config,
        rerank_config=rerank_config,
        qdrant_config=qdrant_config,
    )
    judge_provider = judge_provider or provider
    judge_model = judge_model or model
    results: list[MathEvalResult] = []
    for index, sample in enumerate(samples, 1):
        t0 = time.perf_counter()
        logger.info("🔎 MathRAG eval start sample=%s/%s id=%s", index, len(samples), sample.id)
        try:
            hits = await kb.async_search(sample.question, limit=top_k)
            logger.info("✓ MathRAG eval retrieval ok id=%s hits=%s", sample.id, len(hits))
            answer = sample.answer
            generation_usage: dict[str, int] = {}
            if skip_generation:
                if not answer:
                    raise ValueError("sample has no answer while --skip-generation is enabled")
                logger.info("✓ MathRAG eval generation skipped id=%s", sample.id)
            else:
                answer, generation_usage = await _generate_answer(
                    provider=provider,
                    model=model,
                    question=sample.question,
                    hits=hits,
                    max_tokens=max_answer_tokens,
                )
                logger.info("✓ MathRAG eval generation ok id=%s chars=%s", sample.id, len(answer))
            scores, judge_usage = await _judge_answer(
                provider=judge_provider,
                model=judge_model,
                sample=sample,
                hits=hits,
                answer=answer,
                max_tokens=max_judge_tokens,
            )
            logger.info(
                "✓ MathRAG eval judge ok id=%s overall=%.3f elapsed_ms=%s",
                sample.id,
                scores.overall_score,
                _elapsed_ms(t0),
            )
            results.append(
                MathEvalResult(
                    sample=sample,
                    answer=answer,
                    hits=tuple(hits),
                    scores=scores,
                    generation_usage=generation_usage,
                    judge_usage=judge_usage,
                )
            )
        except Exception as exc:
            logger.warning(
                "⚠ MathRAG eval sample failed id=%s reason=%s elapsed_ms=%s",
                sample.id,
                exc,
                _elapsed_ms(t0),
            )
            results.append(MathEvalResult(sample=sample, answer=sample.answer, hits=(), scores=None, error=str(exc)))
    return results


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _truncate(text: str, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _sum_usage(results: list[MathEvalResult]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for result in results:
        for usage in (result.generation_usage, result.judge_usage):
            for key, value in usage.items():
                totals[key] = totals.get(key, 0) + value
    return totals


def render_markdown_report(results: list[MathEvalResult]) -> str:
    scored = [result for result in results if result.scores is not None]
    failed = [result for result in results if result.scores is None]
    context_avg = _avg([result.scores.context_relevance_score for result in scored if result.scores])
    faithfulness_avg = _avg([result.scores.faithfulness_score for result in scored if result.scores])
    answer_avg = _avg([result.scores.answer_relevance_score for result in scored if result.scores])
    overall_avg = _avg([result.scores.overall_score for result in scored if result.scores])
    usage = _sum_usage(results)

    lines = [
        "# MathRAG Evaluation Report",
        "",
        "## Summary",
        "",
        f"- Total samples: {len(results)}",
        f"- Scored samples: {len(scored)}",
        f"- Failed samples: {len(failed)}",
        f"- Context relevance avg: {context_avg:.4f}",
        f"- Faithfulness avg: {faithfulness_avg:.4f}",
        f"- Answer relevance avg: {answer_avg:.4f}",
        f"- Overall avg: {overall_avg:.4f}",
    ]
    if usage:
        lines.append(f"- Token usage: {json.dumps(usage, ensure_ascii=False)}")

    low = [
        result for result in scored
        if result.scores and result.scores.overall_score < 0.7
    ]
    if low:
        lines.extend(["", "## Low Score Samples", ""])
        for result in low:
            assert result.scores is not None
            lines.append(
                f"- `{result.sample.id}` overall={result.scores.overall_score:.4f}: "
                f"{result.scores.reason or '无说明'}"
            )

    if failed:
        lines.extend(["", "## Failed Samples", ""])
        for result in failed:
            lines.append(f"- `{result.sample.id}`: {result.error}")

    lines.extend(["", "## Details", ""])
    for result in results:
        sample = result.sample
        lines.extend([f"### {sample.id}", ""])
        lines.append(f"- Question: {sample.question}")
        if sample.expected_sources:
            lines.append(f"- Expected sources: {', '.join(sample.expected_sources)}")
        if result.scores is None:
            lines.append(f"- Error: {result.error}")
        else:
            scores = result.scores
            lines.extend([
                f"- Context relevance: {scores.context_relevance_score:.4f}",
                f"- Faithfulness: {scores.faithfulness_score:.4f}",
                f"- Answer relevance: {scores.answer_relevance_score:.4f}",
                f"- Overall: {scores.overall_score:.4f}",
                f"- Judge reason: {scores.reason or '无'}",
            ])
            if scores.failure_tags:
                lines.append(f"- Failure tags: {', '.join(scores.failure_tags)}")
        sources = [hit.citation() for hit in result.hits]
        lines.append(f"- Retrieved sources: {', '.join(sources) if sources else '无'}")
        lines.append("")
        lines.append("Answer summary:")
        lines.append("")
        lines.append(_truncate(result.answer or "(empty)", 900))
        lines.append("")
        lines.append("Reference summary:")
        lines.append("")
        lines.append(_truncate(sample.reference_answer, 600))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_markdown_report(results: list[MathEvalResult], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "report.md"
    path.write_text(render_markdown_report(results), encoding="utf-8")
    return path


async def _amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate MathRAG answers with an LLM judge.")
    parser.add_argument("--dataset", required=True, help="Path to JSONL eval dataset.")
    parser.add_argument("--output-dir", default=".nanobot/math_eval", help="Directory for report.md.")
    parser.add_argument("--workspace", default="", help="Workspace path. Defaults to config workspace.")
    parser.add_argument("--limit", type=int, default=0, help="Only evaluate the first N samples.")
    parser.add_argument("--top-k", type=int, default=4, help="Number of RAG hits to include.")
    parser.add_argument("--model", default="", help="Answer generation model override.")
    parser.add_argument("--judge-model", default="", help="Judge model override.")
    parser.add_argument("--skip-generation", action="store_true", help="Evaluate existing answer fields.")
    parser.add_argument("--max-answer-tokens", type=int, default=2048)
    parser.add_argument("--max-judge-tokens", type=int, default=768)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from nanobot.config.loader import load_config, resolve_config_env_vars
    from nanobot.providers.factory import make_provider

    config = resolve_config_env_vars(load_config())
    workspace = Path(args.workspace).expanduser() if args.workspace else config.workspace_path
    workspace = workspace.resolve()
    samples = load_eval_dataset(Path(args.dataset).expanduser())
    if args.limit > 0:
        samples = samples[: args.limit]
    if not samples:
        raise ValueError("dataset has no samples")

    model = args.model or config.resolve_preset().model
    judge_model = args.judge_model or model
    provider = make_provider(config, model=model)
    judge_provider = provider if judge_model == model else make_provider(config, model=judge_model)
    results = await evaluate_samples(
        samples,
        workspace=workspace,
        provider=provider,
        model=model,
        judge_provider=judge_provider,
        judge_model=judge_model,
        embedding_config=config.rag.embedding,
        rerank_config=config.rag.rerank,
        qdrant_config=config.rag.qdrant,
        top_k=args.top_k,
        skip_generation=args.skip_generation,
        max_answer_tokens=args.max_answer_tokens,
        max_judge_tokens=args.max_judge_tokens,
    )
    report = write_markdown_report(results, workspace / args.output_dir)
    logger.info("✓ MathRAG eval report written path=%s", report)
    return 0 if all(result.scores is not None for result in results) else 1


def main() -> None:
    try:
        raise SystemExit(asyncio.run(_amain()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
