# Memory in nanobot

nanobot keeps memory scoped and explicit.

There is no automatic cross-session long-term memory layer. Context comes from
user-editable personalization files and the current conversation session.

## Layers

- `SOUL.md` defines the bot's voice and communication style.
- `USER.md` defines stable user preferences.
- `sessions/*.jsonl` stores each session's messages, metadata, tool traces, and
  compact summary.

`SOUL.md` and `USER.md` are injected into the system prompt. They are ordinary
workspace files, so users can inspect and edit them directly.

## Session Memory

Session history is persisted under `sessions/`. The agent replays a bounded
recent slice of the current session on each turn.

When a session grows beyond the replay or token budget, nanobot summarizes the
older part of that same session. The summary is written into the metadata line
of that session file as `_last_summary`.

That means the summary:

- is durable across process restarts
- is only available to the same session
- is cleared by `/new`
- is not shared with other chats, repositories, or reviews

## Code Review

Code review relies on the current ReviewPlan, current session history, session
metadata, subagent results, and repository evidence tools.

Old reviews do not become global memory. A new session starts from the target
and evidence supplied for that review.
