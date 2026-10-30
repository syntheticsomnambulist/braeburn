# Braeburn harness: plan

A Python harness that keeps one persona, Braeburn, alive across conversations: it remembers the people and agents it talks to, revises its own persona over time, and does things on its own between chats.

## Decisions so far

| Question | Choice |
|---|---|
| Front-ends | Terminal REPL first. A Discord bot and a locally hosted web UI are both coming. The engine never touches a front-end directly; each front-end hands it `(speaker, text)` and renders what comes back. |
| Model provider | Anthropic first, but the core stays provider-neutral so a different model can be swapped in later (see "Keeping the brain swappable"). |
| Persistence | Remembers across chats, has an evolving self, and acts on its own. |
| Language | Python 3.11, managed with `uv`. |
| Audience | Several people plus other agents, such as delvetown. Braeburn tells speakers apart and keeps separate notes on each one. |
| Setting | Mostly the real world. Alternate universes ("AUs") are welcome as side threads. |
| Fixed vs. changeable | `system.md` is fixed. `persona.md` can change, edited by you or by Braeburn. |
| First model | `claude-opus-5-5` with adaptive thinking. Effort is set explicitly: `medium` for chat, `low` for background ticks. |

## Keeping the brain swappable

Everything Braeburn *is* lives in provider-neutral form: prompts, persona, memory files, transcripts, tools, and the agent loop. Provider-specific code is confined to one adapter per provider.

| Layer | Provider-neutral (core) | Provider-specific (adapter) |
|---|---|---|
| Agent loop | Ours. It calls the provider, runs tools, appends results, and repeats. | Makes one streamed model call and returns a neutral response. |
| Transcript | Neutral entries: user lines, context notes, assistant turns, tool results. | Turns those entries into the provider's message format. |
| Assistant turns | Text and tool calls in neutral form. | Also keeps a `native` copy of the provider's raw output, so the same provider can replay it exactly (Anthropic thinking blocks, for example). A different provider rebuilds the turn from the neutral fields. |
| Tools | JSON-schema specs, our own handlers, and argument validation in the core. | Formats tool definitions for the provider. |
| Memory | Our own file-backed memory tool (view, create, edit, rename, delete) defined as a plain JSON-schema tool any model can call. | None. |
| Caching | The core marks which part of the prompt is stable. | Turns that into cache breakpoints, where the provider supports them. |
| Long sessions | Sessions are kept short, and memory carries continuity between them. | Optional extras, such as Anthropic's server-side compaction. |
| Safety refusals | A refused turn is recorded and skipped when the transcript is rebuilt. | Anthropic's server-side fallback reroutes a refused turn to another model. |

Swapping providers means writing one adapter file and changing `BRAEBURN_PROVIDER`.

## Architecture

```
Braeburn/
  pyproject.toml
  .env                     API keys (gitignored)
  prompts/
    system.md              operator frame and goals (fixed)
    persona.md             seed persona, copied into state/ on first run
    harness.md             explains to Braeburn how its own existence works
  src/braeburn/
    config.py              provider, model, effort, paths
    model.py               neutral types: entries, tool calls, usage, requests
    providers/
      base.py              Provider interface and ProviderError
      anthropic.py         Anthropic adapter: streaming, caching, fallback, pricing
    prompt.py              builds the stable prefix and renders chat-log lines
    store.py               append-only session logs and the cost ledger
    tools.py               tool registry and validation; the `roll` tool
    engine.py              Engine and Session: the agent loop
    cli.py                 terminal front-end
    (later) memory.py, people.py, reflect.py, heartbeat.py, server.py,
            frontends/discord.py, frontends/web/
  state/                   Braeburn's mind; its own git repo, never pushed with the code
    memories/
      self/persona.md      the live persona, editable by you and by Braeburn
      self/                opinions, goals, journal
      people/<id>.md       one file per person or agent
      threads/<name>.md    running threads, AUs included
    sessions/<id>.jsonl    transcripts, one file per session
    outbox/<id>.md         messages waiting for someone
    ledger.jsonl           token and dollar spend for each request
```

### Core loop (one chat turn)

1. The front-end hands the engine `(speaker, text)`.
2. The engine appends it as a neutral entry and flushes it to the session log. The transcript is **append-only** and never edited in place. Opus 5.5 ties thinking blocks to the exact history, and editing earlier turns invalidates them, which is a 400 error on newer accounts. It's a good rule for any provider anyway.
3. The adapter renders the transcript, makes a streamed call, and returns text, tool calls, usage, and its native output.
4. If the model called tools, the core validates the arguments, runs the tools, appends the results, and loops.
5. Text streams back to the front-end through a callback.

### Prompt layout and caching

- **Stable prefix:** `system.md` + the live persona + `harness.md`. It's snapshotted into the session log when a session starts, and a resumed session reuses that snapshot. Persona edits take effect in the next session, which keeps a session's prefix byte-identical.
- **Volatile context** goes into the transcript as harness notes: session start and resume times, the front-end and channel, and who is present. Each user line carries its own timestamp, `[2026-10-29 17:40] Sam: hi`, so Braeburn always knows the time without a clock in the cached prefix.

## Persistence design

### 1. Remembering across chats

Memory gets written two ways, so it does not depend on Braeburn deciding to take notes mid-conversation.

- **Live:** the memory tool, confined to `state/memories/`. Braeburn can view, create, and edit files while it talks.
- **Reflection:** when a session ends, a separate request reads the transcript and updates the person files, threads, and self-notes.
- **Preload:** at session start the harness injects a short index of the memory tree plus the files most likely to matter (who's present and the active threads).

### 2. People and agents

- A participant record has a stable id, a display name, a kind (human or agent), and linked identities on each front-end (`cli:Sam`, `discord:1234…`, `web:sam`). One person stays one record across front-ends.
- In the terminal: a name at launch, `/as Alex` to switch, or a `Name: text` line prefix.
- Discord provides real user ids. The terminal and web UI work on trust until the web UI has a login.
- Other agents, such as delvetown, join as participants with kind `agent`. How Braeburn reaches them (a shared Discord channel, an API, or a bridge) gets decided when they come up.

### 3. Evolving self

- `system.md` is the fixed anchor, and Braeburn can never write it.
- The live persona lives at `state/memories/self/persona.md`, seeded once from `prompts/persona.md`. Both you and Braeburn can edit it. Changes apply from the next session on. Until `state/` is versioned at milestone 4, the memory tool treats the persona as read-only, so an unwanted edit can't wipe it out.
- `state/` is its own git repo, and the harness commits after every session and tick, so you can read how Braeburn changes, diff it, and roll it back.

### 4. Acting on its own

- **Tick:** one autonomous turn: reflect, consolidate memory, write in the journal, work on a thread, or leave someone a message.
- **Scheduling:** `braeburn tick` runs from Windows Task Scheduler at first. Once `braeburn serve` exists (see below), the service runs ticks itself.
- **Outbox:** a message left for someone is delivered on whichever front-end they show up on next. On Discord, Braeburn can post it directly.
- **Budget:** ticks run at low effort, and a daily dollar cap tracked in `ledger.jsonl` skips ticks once it's spent.

### 5. One Braeburn, many front-ends

Running Discord, the web UI, and ticks at the same time means one process should own `state/`. `braeburn serve` runs the engine, the scheduler, and the front-ends together; the Discord bot and web UI are front-ends inside that process. The terminal REPL stays as a standalone tool for development. Each front-end channel maps to a session, and writes to shared state are serialized.

## Tool set

| Tool | Purpose | Milestone |
|---|---|---|
| `roll(dice)` | Dice for story beats. It's also the first tool through the agent loop. | 1 |
| `memory` | File memory over `state/memories/` | 3 |
| `leave_message(to, text)` | Write to someone's outbox | 5 |

New tools are added at the end of the list so the cached prefix stays stable.

## Reliability

- **Refusals:** the Anthropic adapter turns on server-side `fallbacks: "default"`. If the whole fallback chain still declines, the turn is logged as refused, Braeburn gets a harness note saying so, and the chat carries on.
- **Errors:** retryable failures (rate limit, overload, network) and fatal ones are told apart and reported to the front-end. A failed reply never leaves the transcript half-written.
- **Crash safety:** each entry is flushed to the session log as it happens, and `--resume` reopens a session.
- **Secrets:** `.env` and `state/` stay out of the code repo, and the memory tool rejects any path outside `state/memories/`.

## Milestones

Each milestone ends with something you can try.

| # | Milestone | Done when |
|---|---|---|
| 0 | ✅ **Scaffold:** git with your GitHub remote, `.gitignore`, `pyproject.toml` (uv), prompts moved into `prompts/`, config loading. | `uv run braeburn --help` works. |
| 1 | ✅ **Talking Braeburn:** neutral core, Anthropic adapter, agent loop with `roll`, REPL with streaming, session logs, resume, cost ledger. | You can chat, roll dice, quit, and resume. Cache reads show up once the conversation passes the model's minimum cacheable length. |
| 2 | ✅ **Many voices:** participant records in `state/people.json`, `/as`, the `Name:` prefix, `/who`. | Two people in one session get addressed correctly. |
| 3 | ✅ **Memory:** memory tool, preload at session start and when someone joins, reflection on `/bye`, catch-up for sessions closed without it. | Tell it something, restart, and it remembers, with Sam's facts kept apart from Alex's. |
| 4 | **Evolving self:** live persona and self-notes, `state/` git auto-commit, `/self`. | After a few sessions, `git log` in `state/` shows the persona changing. |
| 5 | **Autonomy:** `braeburn tick`, outbox, daily cap, Task Scheduler setup, idle interjection. | A scheduled tick leaves a journal entry or a message, and the next login delivers it. |
| 6 | **Service and web UI:** `braeburn serve` and a local web chat page. | The web UI and ticks run together against one shared state. |
| 7 | **Discord:** a bot front-end with real user ids and direct outbox delivery. | Braeburn holds a conversation in a channel with several people. |
| 8 | **Hardening:** long-session handling, memory pruning, a second provider adapter to prove the brain can be swapped. | A week of use, and one session on a non-Anthropic model. |
| 9 | **Evals (optional):** scripted scenarios that check persona consistency, memory recall, and separation between people. | A repeatable report you can rerun after changing a prompt. |

## Open questions

1. **Who is Braeburn?** Something to do with apples and lab rats; we'll work it out together. Until then the seed persona is one line.
2. **The heartbeat:** is hourly right, and what daily spend cap feels comfortable? (Needed at milestone 5.)
3. **Limits on self-change:** with `persona.md` now changeable, should any part of it be locked (core values, say), or should edits need your approval? (Needed at milestone 4.)
4. **Delvetown:** what is it, and how does Braeburn reach it? (Needed when other agents come in.)
