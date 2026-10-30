"""Builds the stable system prefix and renders transcripts as chat-log text.

Rendering lives here, not in the adapters, so every provider shows the model
the same words.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .model import AssistantTurn, ContextNote, Entry, ToolResult, ToolResults, UserTurn, now


def persona_path(cfg: Config) -> Path:
    return cfg.state_dir / "memories" / "self" / "persona.md"


def load_persona(cfg: Config) -> str:
    """Read the live persona, seeding it from prompts/persona.md on first run."""
    path = persona_path(cfg)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cfg.prompts_dir / "persona.md", path)
    return path.read_text(encoding="utf-8")


def build_system(cfg: Config) -> str:
    """The stable prefix: fixed frame, live persona, harness notes.

    It's snapshotted when a session starts, so nothing in here may vary
    between requests (no clocks, no ids).
    """
    system = (cfg.prompts_dir / "system.md").read_text(encoding="utf-8").strip()
    persona = load_persona(cfg).strip()
    harness = (cfg.prompts_dir / "harness.md").read_text(encoding="utf-8").strip()
    return (
        f"<system>\n{system}\n</system>\n\n"
        f"<persona>\n{persona}\n</persona>\n\n"
        f"<harness>\n{harness}\n</harness>\n"
    )


def _when(moment: datetime) -> str:
    return f"{moment:%A %Y-%m-%d %H:%M} {moment.tzname()}"


def session_start_note(frontend: str, channel: str | None, speaker: str) -> str:
    where = f"{frontend} ({channel})" if channel else frontend
    return f"A new session started {_when(now())} on {where}. Present: {speaker}."


def resume_note(speaker: str) -> str:
    return f"The session resumed {_when(now())}. Present: {speaker}."


REFUSAL_NOTE = (
    "Your previous reply was stopped by a safety filter before it finished, "
    "and it has been removed from this conversation."
)


def render_user(turn: UserTurn) -> str:
    sent = datetime.fromisoformat(turn.ts)
    return f"[{sent:%Y-%m-%d %H:%M}] {turn.speaker}: {turn.text}"


def render_context(note: ContextNote) -> str:
    return f"<context>\n{note.text}\n</context>"


@dataclass
class UserMessage:
    """Consecutive non-assistant entries, merged into one user-side message."""

    tool_results: list[ToolResult] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)


def to_messages(transcript: list[Entry]) -> list[UserMessage | AssistantTurn]:
    """Group the transcript into alternating user and assistant messages.

    Refused assistant turns are left out; the context note that follows them
    tells the model what happened.
    """
    messages: list[UserMessage | AssistantTurn] = []
    for entry in transcript:
        if isinstance(entry, AssistantTurn):
            if entry.stop != "refusal":
                messages.append(entry)
            continue
        if not messages or not isinstance(messages[-1], UserMessage):
            messages.append(UserMessage())
        message = messages[-1]
        if isinstance(entry, ToolResults):
            message.tool_results.extend(entry.results)
        elif isinstance(entry, UserTurn):
            message.texts.append(render_user(entry))
        elif isinstance(entry, ContextNote):
            message.texts.append(render_context(entry))
    return messages
