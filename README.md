# Braeburn

A harness for Braeburn, a persistent persona agent. See [PLAN.md](PLAN.md) for the design and milestones.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Put your API key in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Talking to Braeburn

```bash
uv run braeburn chat
```

Inside the chat:

| Command | What it does |
|---|---|
| `/as NAME` | Switch who's typing. Someone new joins the conversation, and Braeburn gets its notes on them. |
| `Alex: hi` | Say one line as someone Braeburn already knows. |
| `/who` | Show who's in the conversation |
| `/memory` | List Braeburn's memory files |
| `/cost` | Show tokens and cost so far |
| `/bye` | End the session. Braeburn updates its memory first. |

If a chat is closed without `/bye`, Braeburn catches up on it the next time a chat starts.

```bash
uv run braeburn chat --resume
```

That reopens the most recent session. `braeburn sessions` lists recent sessions so you can pass an id to `--resume`.

## Memory

Braeburn's memory is plain Markdown under `state/memories/`, so you can read or edit it yourself:

- `people/<id>.md`: one file per person or agent. Ids come from `state/people.json`.
- `threads/`: running stories, projects, and AUs
- `self/`: Braeburn's notes to itself, plus `persona.md`, the live persona

Braeburn writes memory during a chat when something matters, and properly at the end of each session.

## Configuration

Settings come from environment variables (or `.env`):

| Variable | Default | Meaning |
|---|---|---|
| `BRAEBURN_PROVIDER` | `anthropic` | Which model adapter to use |
| `BRAEBURN_MODEL` | provider default (`claude-opus-5-5`) | Model id |
| `BRAEBURN_EFFORT` | `medium` | Reasoning effort for chat: `low`, `medium`, `high` |
| `BRAEBURN_MAX_TOKENS` | `64000` | Ceiling on one reply |
| `BRAEBURN_STATE_DIR` | `./state` | Where Braeburn's mind lives |

## Layout

- `prompts/`: `system.md` (fixed), `persona.md` (seed persona), `harness.md` (how Braeburn's existence works)
- `src/braeburn/`: provider-neutral core plus one adapter per model provider in `providers/`
- `state/`: transcripts, live persona, cost ledger. Gitignored, because it's Braeburn's mind, not code.

The live persona is `state/memories/self/persona.md`. It's copied from `prompts/persona.md` on first run, and edits there take effect in the next session. Braeburn can read it but can't change it yet; that comes at milestone 4, along with version history for `state/`.
