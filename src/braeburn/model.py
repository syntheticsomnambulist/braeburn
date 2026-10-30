"""Provider-neutral data types.

Everything Braeburn hears and says is stored in these shapes, so any model
provider can sit behind the same transcript. Provider adapters translate them
to and from their own wire formats.
"""

from __future__ import annotations

from dataclasses import asdict, astuple, dataclass, field
from datetime import datetime
from typing import Any, Literal, Union

# Why a model call ended, in neutral terms.
Stop = Literal["end", "tool_use", "max_tokens", "refusal", "pause"]


def now() -> datetime:
    return datetime.now().astimezone()


def stamp() -> str:
    return now().isoformat(timespec="seconds")


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    name: str
    output: str
    is_error: bool = False


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(*(a + b for a, b in zip(astuple(self), astuple(other))))


# Transcript entries. A session's transcript is an append-only list of these.


@dataclass
class UserTurn:
    """One message from a participant (a person or another agent)."""

    speaker: str  # display name, as they appeared in this conversation
    text: str
    ts: str = field(default_factory=stamp)
    speaker_id: str | None = None  # their participant record
    kind: str = field(default="user", init=False)


@dataclass
class ContextNote:
    """Harness-authored context: session start, resume, refusals."""

    text: str
    ts: str = field(default_factory=stamp)
    kind: str = field(default="context", init=False)


@dataclass
class AssistantTurn:
    """One model call's output.

    `native` holds the provider's raw output so that provider can replay it
    exactly (thinking blocks, for example). Other providers rebuild the turn
    from `text` and `tool_calls`.
    """

    text: str
    tool_calls: list[ToolCall]
    stop: Stop
    usage: Usage
    model: str
    native: dict[str, Any] | None = None
    ts: str = field(default_factory=stamp)
    kind: str = field(default="assistant", init=False)


@dataclass
class ToolResults:
    results: list[ToolResult]
    ts: str = field(default_factory=stamp)
    kind: str = field(default="tool_results", init=False)


Entry = Union[UserTurn, ContextNote, AssistantTurn, ToolResults]


def entry_to_dict(entry: Entry) -> dict[str, Any]:
    return asdict(entry)


def entry_from_dict(data: dict[str, Any]) -> Entry:
    data = dict(data)
    kind = data.pop("kind")
    if kind == "user":
        return UserTurn(**data)
    if kind == "context":
        return ContextNote(**data)
    if kind == "assistant":
        data["tool_calls"] = [ToolCall(**c) for c in data["tool_calls"]]
        data["usage"] = Usage(**data["usage"])
        return AssistantTurn(**data)
    if kind == "tool_results":
        data["results"] = [ToolResult(**r) for r in data["results"]]
        return ToolResults(**data)
    raise ValueError(f"unknown transcript entry kind {kind!r}")


# What the core hands a provider, and what it gets back.


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]  # JSON Schema for the arguments


@dataclass
class ModelRequest:
    system: str  # the stable prefix; adapters may cache it
    transcript: list[Entry]
    tools: list[ToolSpec]
    effort: str
    max_tokens: int


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]
    stop: Stop
    usage: Usage
    model: str
    native: dict[str, Any] | None = None
