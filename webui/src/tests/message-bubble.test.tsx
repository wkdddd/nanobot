// ##AI修改前
// import { fireEvent, render, screen, waitFor } from "@testing-library/react";
// ######
// ##AI修改后
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
// ######
import { describe, expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import type { UIMessage } from "@/lib/types";

describe("MessageBubble", () => {
  it("renders user messages as right-aligned pills", () => {
    const message: UIMessage = {
      id: "u1",
      role: "user",
      content: "hello",
      createdAt: Date.now(),
    };

    const { container } = render(<MessageBubble message={message} />);
    const row = container.firstElementChild;
    const pill = screen.getByText("hello");

    expect(row).toHaveClass("ml-auto", "flex");
    expect(pill).toHaveClass("ml-auto", "w-fit", "rounded-[18px]");
    expect(screen.queryByRole("button", { name: "Copy reply" })).not.toBeInTheDocument();
  });

  it("renders a code review reference above user message text", () => {
    const message: UIMessage = {
      id: "u-review",
      role: "user",
      content: "please review",
      createdAt: Date.now(),
      review: {
        mode: "deep",
        target_type: "github",
        action: "pr_diff",
        target: "https://github.com/test/repo",
      },
    };

    render(<MessageBubble message={message} />);

    expect(screen.getByText("https://github.com/test/repo")).toBeInTheDocument();
    expect(screen.getByText("GITHUB")).toBeInTheDocument();
    expect(screen.getByText("PR_DIFF")).toBeInTheDocument();
    expect(screen.getByText("DEEP")).toBeInTheDocument();
    expect(screen.getByText("please review")).toBeInTheDocument();
  });

  it("copies completed assistant replies from the action row", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const message: UIMessage = {
      id: "a-copy",
      role: "assistant",
      content: "I can help with the next step.",
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} />);

    fireEvent.click(screen.getByRole("button", { name: "Copy reply" }));

    expect(writeText).toHaveBeenCalledWith("I can help with the next step.");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Copied reply" })).toBeInTheDocument(),
    );
  });

  it("does not show copy actions for streaming placeholders", () => {
    const message: UIMessage = {
      id: "a-streaming",
      role: "assistant",
      content: "",
      isStreaming: true,
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} />);

    expect(screen.queryByRole("button", { name: "Copy reply" })).not.toBeInTheDocument();
  });

  it("does not show copy when showAssistantCopyAction is false", () => {
    const message: UIMessage = {
      id: "a-mid",
      role: "assistant",
      content: "Mid-turn snippet.",
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} showAssistantCopyAction={false} />);

    expect(screen.queryByRole("button", { name: "Copy reply" })).not.toBeInTheDocument();
  });

  it("renders trace messages as collapsible tool groups", () => {
    const message: UIMessage = {
      id: "t1",
      role: "tool",
      kind: "trace",
      content: 'search "hk weather"',
      traces: ['weather("get")', 'search "hk weather"'],
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} />);
    const toggle = screen.getByRole("button", { name: /used 2 tools/i });

    expect(screen.queryByText('weather("get")')).not.toBeInTheDocument();
    expect(screen.queryByText('search "hk weather"')).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByText('weather("get")')).toBeInTheDocument();
    expect(screen.getByText('search "hk weather"')).toBeInTheDocument();
  });

  it("renders video media as an inline player", () => {
    const message: UIMessage = {
      id: "a1",
      role: "assistant",
      content: "here is the clip",
      createdAt: Date.now(),
      media: [
        {
          kind: "video",
          url: "/api/media/sig/payload",
          name: "demo.mp4",
        },
      ],
    };

    const { container } = render(<MessageBubble message={message} />);

    expect(screen.getByText("here is the clip")).toBeInTheDocument();
    const video = screen.getByLabelText(/video attachment/i);
    expect(video.tagName).toBe("VIDEO");
    expect(video).toHaveAttribute("src", "/api/media/sig/payload");
    expect(container.querySelector("video[controls]")).toBeInTheDocument();
  });

  it("auto-expands the reasoning trace while streaming with a shimmer header", () => {
    const message: UIMessage = {
      id: "a-reasoning-streaming",
      role: "assistant",
      content: "",
      createdAt: Date.now(),
      reasoning: "Step 1: parse intent. Step 2: compute.",
      reasoningStreaming: true,
    };

    const { container } = render(<MessageBubble message={message} />);

    expect(screen.getByText("Thinking…")).toBeInTheDocument();
    expect(screen.getByText(/Step 1: parse intent\./)).toBeInTheDocument();
    expect(container.querySelector(".reasoning-sheen-stripe")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /thinking/i }).parentElement).not.toHaveClass("mb-2");
  });

  // ##AI修改前
  // it("collapses the reasoning section by default once streaming ends", () => {
  //   const message: UIMessage = {
  //     id: "a-reasoning-done",
  //     role: "assistant",
  //     content: "The answer is 42.",
  //     createdAt: Date.now(),
  //     reasoning: "hidden until expanded",
  //     reasoningStreaming: false,
  //   };
  //
  //   render(<MessageBubble message={message} />);
  //
  //   expect(screen.getByText("Thinking")).toBeInTheDocument();
  //   expect(screen.getByText("The answer is 42.")).toBeInTheDocument();
  //   expect(screen.queryByText("hidden until expanded")).not.toBeInTheDocument();
  //   expect(screen.getByRole("button", { name: /thinking/i }).parentElement).toHaveClass("mb-2");
  //
  //   fireEvent.click(screen.getByRole("button", { name: /thinking/i }));
  //   expect(screen.getByText("hidden until expanded")).toBeInTheDocument();
  // });
  // ######
  // ##AI修改后
  it("collapses the reasoning section with a short debounce once streaming ends", () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <MessageBubble
          message={{
            id: "a-reasoning-done",
            role: "assistant",
            content: "",
            createdAt: Date.now(),
            reasoning: "hidden until expanded",
            reasoningStreaming: true,
          }}
        />,
      );

      // ##AI修改前
      // expect(screen.getByText("Thinking…")).toBeInTheDocument();
      // ######
      // ##AI修改后
      expect(screen.getByRole("button", { name: /thinking/i })).toBeInTheDocument();
      // ######
      expect(screen.getByText("hidden until expanded")).toBeInTheDocument();

      rerender(
        <MessageBubble
          message={{
            id: "a-reasoning-done",
            role: "assistant",
            content: "The answer is 42.",
            createdAt: Date.now(),
            reasoning: "hidden until expanded",
            reasoningStreaming: false,
          }}
        />,
      );

      expect(screen.getByText("Thinking")).toBeInTheDocument();
      expect(screen.getByText("The answer is 42.")).toBeInTheDocument();
      expect(screen.getByText("hidden until expanded")).toBeInTheDocument();

      // ##AI修改前
      // vi.advanceTimersByTime(699);
      // ######
      // ##AI修改后
      act(() => {
        vi.advanceTimersByTime(699);
      });
      // ######
      expect(screen.getByText("hidden until expanded")).toBeInTheDocument();

      // ##AI修改前
      // vi.advanceTimersByTime(1);
      // ######
      // ##AI修改后
      act(() => {
        vi.advanceTimersByTime(1);
      });
      // ######
      expect(screen.queryByText("hidden until expanded")).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: /thinking/i }));
      expect(screen.getByText("hidden until expanded")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers markdown download for completed code review reports", () => {
    const createObjectURL = vi.fn(() => "blob:report");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    const click = vi.fn();
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(click);
    const message: UIMessage = {
      id: "a-report",
      role: "assistant",
      content: "## Code Review Report: demo\n\n### Findings\n- none",
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} />);
    fireEvent.click(screen.getByRole("button", { name: "Download Markdown" }));

    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalled();
    clickSpy.mockRestore();
  });
  // ######

  it("renders reasoning body as markdown so headings are not left as raw ###", async () => {
    await import("@/components/MarkdownTextRenderer");
    const message: UIMessage = {
      id: "a-reasoning-md",
      role: "assistant",
      content: "",
      createdAt: Date.now(),
      reasoning: "### Section title\n\nBody line.",
      reasoningStreaming: false,
    };

    const { container } = render(<MessageBubble message={message} />);
    fireEvent.click(screen.getByRole("button", { name: /thinking/i }));

    await waitFor(() => {
      expect(container.querySelector("h3")?.textContent).toBe("Section title");
    });
    expect(container.textContent).not.toContain("###");
    expect(screen.getByText("Body line.")).toBeInTheDocument();
  });

  it("adds wrapping affordances for long review prose, inline code, and tables", async () => {
    await import("@/components/MarkdownTextRenderer");
    const longLambda = "lambda t, k=k: self._active_tasks.get(k, []) and (t in self._active_tasks.get(k, []) and self._active_tasks.get(k, []).remove(t) or None) or None";
    const message: UIMessage = {
      id: "a-long-review",
      role: "assistant",
      content: [
        `Impact: \`${longLambda}\``,
        "",
        "| File | Impact |",
        "|---|---|",
        `| loop.py | ${"A".repeat(160)} |`,
      ].join("\n"),
      createdAt: Date.now(),
    };

    const { container } = render(<MessageBubble message={message} />);

    await waitFor(() => {
      expect(container.querySelector(".markdown-content")).toBeInTheDocument();
    });
    expect(container.querySelector(".markdown-content")).toHaveClass(
      "break-words",
      "[overflow-wrap:anywhere]",
    );
    expect(container.querySelector("code")).toHaveClass(
      "break-words",
      "[overflow-wrap:anywhere]",
    );
    expect(container.querySelector("table")?.parentElement).toHaveClass("overflow-x-auto");
    expect(container.querySelector("td")).toHaveClass(
      "break-words",
      "[overflow-wrap:anywhere]",
    );
  });

  it("wraps long trace lines inside the tool group", () => {
    const longLine = `Impact: ${"x".repeat(180)}`;
    const message: UIMessage = {
      id: "t-long",
      role: "tool",
      kind: "trace",
      content: longLine,
      traces: [longLine],
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} />);
    fireEvent.click(screen.getByRole("button", { name: /used 1 tool/i }));

    expect(screen.getByText(longLine)).toHaveClass(
      "whitespace-pre-wrap",
      "break-words",
      "[overflow-wrap:anywhere]",
    );
  });

  it("renders assistant image media as a larger generated result", () => {
    const message: UIMessage = {
      id: "a-image",
      role: "assistant",
      content: "done",
      createdAt: Date.now(),
      media: [
        {
          kind: "image",
          url: "/api/media/sig/image",
          name: "generated.png",
        },
      ],
    };

    const { container } = render(<MessageBubble message={message} />);

    const imageButton = screen.getByRole("button", { name: /view image/i });
    expect(imageButton).toHaveClass("w-[min(100%,34rem)]", "rounded-[20px]");
    expect(imageButton).not.toHaveAttribute("title");
    expect(container.querySelector("img")).toHaveClass("h-auto", "w-full", "object-contain");
  });
});
