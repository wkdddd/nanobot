import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThreadMessages } from "@/components/thread/ThreadMessages";
import type { UIMessage } from "@/lib/types";

describe("ThreadMessages", () => {
  it("renders a lone streaming reasoning row directly so the text stays visible", () => {
    const messages: UIMessage[] = [
      {
        id: "r-live",
        role: "assistant",
        content: "",
        reasoning: "live thought",
        reasoningStreaming: true,
        isStreaming: true,
        createdAt: Date.now(),
      },
    ];

    render(<ThreadMessages messages={messages} isStreaming />);

    expect(screen.getByRole("button", { name: /thinking/i })).toBeInTheDocument();
    expect(screen.getByText("live thought")).toBeInTheDocument();
    expect(screen.queryByText(/Working/)).not.toBeInTheDocument();
  });

  it("auto-expands activity clusters while reasoning is streaming", () => {
    const messages: UIMessage[] = [
      {
        id: "r-live-tool",
        role: "assistant",
        content: "",
        reasoning: "inspect before tool",
        reasoningStreaming: true,
        isStreaming: true,
        createdAt: Date.now(),
      },
      {
        id: "t-live",
        role: "tool",
        kind: "trace",
        content: "search()",
        traces: ["search()"],
        createdAt: Date.now(),
      },
    ];

    render(<ThreadMessages messages={messages} isStreaming />);

    expect(screen.getByText("inspect before tool")).toBeInTheDocument();
  });

  it("groups consecutive reasoning and tool rows into one cluster before the answer", () => {
    const messages: UIMessage[] = [
      {
        id: "r1",
        role: "assistant",
        content: "",
        reasoning: "thinking",
        reasoningStreaming: false,
        isStreaming: true,
        createdAt: Date.now(),
      },
      {
        id: "t1",
        role: "tool",
        kind: "trace",
        content: "search()",
        traces: ["search()"],
        createdAt: Date.now(),
      },
      {
        id: "r2",
        role: "assistant",
        content: "",
        reasoning: "more thinking",
        reasoningStreaming: false,
        isStreaming: true,
        createdAt: Date.now(),
      },
      {
        id: "a1",
        role: "assistant",
        content: "final answer",
        createdAt: Date.now(),
      },
    ];

    const { container } = render(
      <ThreadMessages messages={messages} isStreaming={false} />,
    );
    const rows = Array.from(container.firstElementChild?.children ?? []);

    expect(rows).toHaveLength(2);
    expect(rows[0]).not.toHaveClass("mt-2", "mt-4", "mt-5");
    expect(rows[1]).toHaveClass("mt-4");
    expect(screen.getAllByRole("button", { name: /thinking/i })).toHaveLength(1);
    expect(screen.getByText("thinking")).toBeInTheDocument();
    expect(screen.getByText("more thinking")).toBeInTheDocument();
  });

  it("shows copy only on the last assistant slice before the next user turn", () => {
    const messages: UIMessage[] = [
      {
        id: "early",
        role: "assistant",
        content: "starting…",
        createdAt: 1,
      },
      {
        id: "t1",
        role: "tool",
        kind: "trace",
        content: "search()",
        traces: ["search()"],
        createdAt: 2,
      },
      {
        id: "late",
        role: "assistant",
        content: "final reply",
        createdAt: 3,
      },
    ];

    render(<ThreadMessages messages={messages} isStreaming={false} />);

    expect(screen.getAllByRole("button", { name: "Copy reply" })).toHaveLength(1);
    expect(screen.getByText("final reply")).toBeInTheDocument();
  });

  it("shows copy only on the second assistant when two text slices appear before user", () => {
    const messages: UIMessage[] = [
      { id: "a1", role: "assistant", content: "part one", createdAt: 1 },
      { id: "a2", role: "assistant", content: "part two", createdAt: 2 },
    ];
    render(<ThreadMessages messages={messages} isStreaming={false} />);
    expect(screen.getAllByRole("button", { name: "Copy reply" })).toHaveLength(1);
  });

  it("shares one date separator across nearby user turns", () => {
    const messages: UIMessage[] = [
      { id: "u1", role: "user", content: "first", createdAt: 1_700_000_000_000 },
      {
        id: "t1",
        role: "tool",
        kind: "trace",
        content: "search()",
        traces: ["search()"],
        createdAt: 1_700_000_000_500,
      },
      { id: "a1", role: "assistant", content: "answer", createdAt: 1_700_000_001_000 },
      { id: "u2", role: "user", content: "second", createdAt: 1_700_000_002_000 },
    ];

    const { container } = render(<ThreadMessages messages={messages} isStreaming={false} />);
    const separators = Array.from(container.querySelectorAll("time"));

    expect(separators).toHaveLength(1);
    expect(separators.map((el) => el.getAttribute("datetime"))).toEqual([
      "2023-11-14T22:13:20.000Z",
    ]);
    expect(screen.getByText("answer")).toBeInTheDocument();
  });

  it("renders another date separator after a larger turn gap", () => {
    const messages: UIMessage[] = [
      { id: "u1", role: "user", content: "first", createdAt: 1_700_000_000_000 },
      { id: "a1", role: "assistant", content: "answer", createdAt: 1_700_000_001_000 },
      { id: "u2", role: "user", content: "later", createdAt: 1_700_000_301_000 },
    ];

    const { container } = render(<ThreadMessages messages={messages} isStreaming={false} />);
    const separators = Array.from(container.querySelectorAll("time"));

    expect(separators).toHaveLength(2);
    expect(separators.map((el) => el.getAttribute("datetime"))).toEqual([
      "2023-11-14T22:13:20.000Z",
      "2023-11-14T22:18:21.000Z",
    ]);
  });
});
