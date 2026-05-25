---
name: repo-reader
description: 按照给定流程阅读代码仓库，给出意见和建议
---

# repo-reader

## When to Use
Use this skill when the user wants to understand a code repository before changing it.

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
- Keep explanations beginner-friendly when the user is learning.
- Mention uncertainty clearly.

## Output

Respond with:

1. Project type
2. Directory map
3. Entry points
4. Core call chain
5. Extension points
6. Learning path
7. Beginner practice tasks