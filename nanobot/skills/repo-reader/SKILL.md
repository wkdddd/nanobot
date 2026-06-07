---
name: repo-reader
description: 当用户提供 GitHub/GitLab 仓库链接，或要求整体理解、分析、review 一个代码仓库时，使用此技能。它负责系统性读仓库结构和代码。
---

# repo-reader

## When to Use
Use this skill when the user wants repository-level understanding:
- The user provides a GitHub, GitLab, or other git hosting URL (e.g. https://github.com/user/repo)
- The user asks to look at, analyze, review, or understand a code repository
- The user asks "帮我看看这个仓库", "分析一下这个项目" or similar phrases
- The user wants to understand a code repository before changing it

Do not use this skill when:
- The user asks a narrow question about files already in the current workspace; use `repo_context` directly.
- The user only needs online documentation, API references, or external facts; use `web_context`.
- The user only asks for a small code edit in a known area; inspect the relevant local files directly.

## workflow
1. Inspect top-level files and directories.
2. Read README and project config files.
3. Identify entry points for CLI, API, backend, frontend, or package exports.
4. Identify core modules and supporting modules.
5. Find tests that show expected behavior.
6. Summarize the main call chain.
7. Recommend a learning path and low-risk practice tasks.

## Rules

- Do not modify code unless the user explicitly asks.
- Prefer facts from files over assumptions.
- Do not summarize a repository from README alone.
- Do not summarize a repository from `repo_context` snippets alone.
- Keep explanations beginner-friendly when the user is learning.
- Mention uncertainty clearly.
- If the repository URL has not been cloned or fetched yet, first get access to the source files before applying this workflow.

## Output

Respond with:

1. Project type
2. Directory map
3. Entry points
4. Core call chain
5. Extension points
6. Learning path
7. Beginner practice tasks
