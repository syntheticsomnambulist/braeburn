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

Inside the chat, `/help` lists commands. `/bye` ends the session.

```bash
uv run braeburn chat --resume
```

That reopens the most recent session. `braeburn sessions` lists recent sessions so you can pass an id to `--resume`.

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

The live persona is `state/memories/self/persona.md`. It's copied from `prompts/persona.md` on first run, and edits there take effect in the next session.
