from __future__ import annotations

import pytest

from nanobot.agent.math_qa import KnowledgeHit
from nanobot.agent.mathrag.eval import (
    MathEvalResult,
    MathEvalSample,
    load_eval_dataset,
    parse_judge_response,
    render_markdown_report,
)
from nanobot.providers.base import LLMResponse


def test_load_eval_dataset_reads_jsonl_utf8(tmp_path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        '{"id":"limit-1","question":"求极限","reference_answer":"答案为 1",'
        '"expected_sources":["calculus.md"],"answer":"已有答案"}\n',
        encoding="utf-8",
    )

    samples = load_eval_dataset(path)

    assert samples == [
        MathEvalSample(
            id="limit-1",
            question="求极限",
            reference_answer="答案为 1",
            expected_sources=("calculus.md",),
            answer="已有答案",
        )
    ]


def test_parse_judge_response_accepts_fenced_json() -> None:
    scores = parse_judge_response(
        """```json
        {
          "context_relevance_score": 0.8,
          "faithfulness_score": 0.7,
          "answer_relevance_score": 0.9,
          "overall_score": 0.8,
          "reason": "上下文和答案基本匹配",
          "failure_tags": ["minor_gap"]
        }
        ```"""
    )

    assert scores.context_relevance_score == 0.8
    assert scores.faithfulness_score == 0.7
    assert scores.answer_relevance_score == 0.9
    assert scores.overall_score == 0.8
    assert scores.failure_tags == ("minor_gap",)


def test_render_markdown_report_contains_scores_and_sources() -> None:
    scores = parse_judge_response(
        '{"context_relevance_score":1,"faithfulness_score":0.5,'
        '"answer_relevance_score":0.75,"reason":"部分推导缺少上下文支持"}'
    )
    sample = MathEvalSample(
        id="sample-1",
        question="洛必达法则什么时候能用？",
        reference_answer="0/0 或无穷/无穷型，并验证条件。",
        expected_sources=("calculus.md",),
    )
    hit = KnowledgeHit(
        title="洛必达法则",
        content="适用于 0/0 或无穷/无穷型极限，需要验证条件。",
        source=".nanobot/math_knowledge/calculus.md",
        score=1.0,
    )
    report = render_markdown_report([
        MathEvalResult(
            sample=sample,
            answer="洛必达法则可用于 0/0 型。",
            hits=(hit,),
            scores=scores,
            generation_usage={"prompt_tokens": 10},
            judge_usage={"completion_tokens": 5},
        )
    ])

    assert "# MathRAG Evaluation Report" in report
    assert "Faithfulness avg: 0.5000" in report
    assert "calculus.md" in report
    assert "Token usage" in report


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat_with_retry(self, *, messages, **kwargs):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return LLMResponse(content="答案为 1", usage={"prompt_tokens": 3})
        return LLMResponse(
            content=(
                '{"context_relevance_score":1,"faithfulness_score":1,'
                '"answer_relevance_score":1,"overall_score":1,"reason":"ok"}'
            ),
            usage={"completion_tokens": 7},
        )


@pytest.mark.asyncio
async def test_evaluate_samples_uses_llm_judge(monkeypatch, tmp_path) -> None:
    from nanobot.agent.mathrag import eval as mathrag_eval

    async def fake_search(self, query: str, *, limit: int = 4):
        return [
            KnowledgeHit(
                title="重要极限",
                content="lim sin x / x = 1",
                source="limit.md",
                score=1.0,
            )
        ]

    monkeypatch.setattr(mathrag_eval.MathKnowledgeBase, "async_search", fake_search)
    provider = _FakeProvider()
    results = await mathrag_eval.evaluate_samples(
        [
            MathEvalSample(
                id="limit",
                question="求 lim sin x / x",
                reference_answer="1",
            )
        ],
        workspace=tmp_path,
        provider=provider,
        model="test-model",
    )

    assert len(provider.calls) == 2
    assert results[0].answer == "答案为 1"
    assert results[0].scores is not None
    assert results[0].scores.overall_score == 1.0
