"""Anthropic adapter: renders the neutral transcript as Messages API calls."""

from __future__ import annotations

from typing import Any

import anthropic

from ..model import AssistantTurn, Entry, ModelRequest, ModelResponse, Stop, ToolCall, ToolSpec, Usage
from ..prompt import UserMessage, to_messages
from .base import ProviderError, TextCallback

DEFAULT_MODEL = "claude-opus-5-5"

# Server-side refusal fallback: a declined turn is re-run on a model Anthropic
# picks for the refusal category, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens: (input, output, cache read). Cache writes with the
# default 5-minute TTL cost 1.25x input. Unknown models are priced at zero.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
}

STOPS: dict[str, Stop] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "refusal": "refusal",
    "pause_turn": "pause",
}

# Blocks from a declined partial that must not be echoed back after a
# mid-output fallback.
DECLINED_PARTIAL_BLOCKS = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, client: Any = None, fallbacks: bool = True):
        self.model = model or DEFAULT_MODEL
        self.fallbacks = fallbacks
        if client is None:
            try:
                client = anthropic.Anthropic()
            except anthropic.AnthropicError as e:
                raise ProviderError(f"Couldn't set up the Anthropic client: {e}", retryable=False) from e
        self.client = client

    def build_params(self, request: ModelRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            # Explicit breakpoint on the stable prefix, plus automatic caching
            # of the conversation tail.
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "cache_control": {"type": "ephemeral"},
            "messages": render_messages(request.transcript),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": request.effort},
        }
        if request.tools:
            params["tools"] = [render_tool(spec) for spec in request.tools]
        if self.fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def complete(self, request: ModelRequest, on_text: TextCallback | None = None) -> ModelResponse:
        params = self.build_params(request)
        try:
            with self.client.beta.messages.stream(**params) as stream:
                for event in stream:
                    if event.type == "text" and on_text:
                        on_text(event.text)
                message = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise ProviderError(
                "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env.", retryable=False
            ) from e
        except anthropic.RateLimitError as e:
            raise ProviderError("Anthropic rate limit hit.", retryable=True) from e
        except anthropic.APIStatusError as e:
            retryable = e.status_code >= 500 or e.status_code in (408, 409)
            raise ProviderError(f"Anthropic API error {e.status_code}: {e.message}", retryable=retryable) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"Couldn't reach the Anthropic API: {e}", retryable=True) from e
        except ValueError as e:
            # Eager input streaming: tool input JSON the SDK couldn't parse at all.
            raise ProviderError(f"A tool call came back garbled: {e}", retryable=True) from e
        return parse_message(message)


def render_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.schema,
        # The core validates every tool input, which eager streaming requires.
        "eager_input_streaming": True,
    }


def render_messages(transcript: list[Entry]) -> list[dict[str, Any]]:
    rendered = []
    for message in to_messages(transcript):
        if isinstance(message, UserMessage):
            # tool_result blocks must come first in a user message.
            content: list[dict[str, Any]] = [
                {
                    "type": "tool_result",
                    "tool_use_id": result.call_id,
                    "content": result.output,
                    **({"is_error": True} if result.is_error else {}),
                }
                for result in message.tool_results
            ]
            content += [{"type": "text", "text": text} for text in message.texts]
            rendered.append({"role": "user", "content": content})
        else:
            rendered.append({"role": "assistant", "content": render_assistant(message)})
    return rendered


def render_assistant(turn: AssistantTurn) -> list[dict[str, Any]]:
    native = turn.native
    if native and native.get("provider") == "anthropic":
        # Replay our own output exactly; thinking blocks must come back unchanged.
        return echo_content(native["content"])
    # A turn from another provider: rebuild it from the neutral fields.
    blocks: list[dict[str, Any]] = []
    if turn.text:
        blocks.append({"type": "text", "text": turn.text})
    blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in turn.tool_calls]
    return blocks or [{"type": "text", "text": "(no reply)"}]


def _last_fallback(content: list[dict[str, Any]]) -> int | None:
    indexes = [i for i, block in enumerate(content) if block.get("type") == "fallback"]
    return indexes[-1] if indexes else None


def echo_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Content to send back for a past turn, per Anthropic's fallback echo rules.

    After a mid-output fallback, the declined partial's thinking and tool-use
    blocks are dropped; its text, and everything after the last fallback
    marker, are kept.
    """
    boundary = _last_fallback(content)
    if boundary is None:
        return content
    return [
        block
        for i, block in enumerate(content)
        if i > boundary or block.get("type") not in DECLINED_PARTIAL_BLOCKS
    ]


def parse_message(message: Any) -> ModelResponse:
    content = [block.to_dict(mode="json") for block in message.content]
    boundary = _last_fallback(content)
    live = content if boundary is None else content[boundary + 1 :]
    return ModelResponse(
        text="".join(block["text"] for block in content if block.get("type") == "text"),
        tool_calls=[
            ToolCall(block["id"], block["name"], block["input"])
            for block in live
            if block.get("type") == "tool_use"
        ],
        stop=STOPS.get(message.stop_reason or "", "end"),
        usage=usage_of(message),
        model=message.model,
        native={"provider": "anthropic", "model": message.model, "content": content},
    )


def usage_of(message: Any) -> Usage:
    usage = message.usage
    read = usage.cache_read_input_tokens or 0
    write = usage.cache_creation_input_tokens or 0
    # Top-level usage covers the attempt that produced the message; declined
    # attempts before a fallback aren't counted here.
    input_price, output_price, read_price = PRICES.get(message.model, (0.0, 0.0, 0.0))
    cost = (
        usage.input_tokens * input_price
        + usage.output_tokens * output_price
        + read * read_price
        + write * input_price * 1.25
    ) / 1_000_000
    return Usage(usage.input_tokens, usage.output_tokens, read, write, cost)
