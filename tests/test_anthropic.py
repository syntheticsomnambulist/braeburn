from __future__ import annotations

from types import SimpleNamespace

from braeburn.model import AssistantTurn, ContextNote, ModelRequest, ToolCall, ToolResult, ToolResults, Usage, UserTurn
from braeburn.providers.anthropic import AnthropicProvider, echo_content, parse_message
from braeburn.tools import default_tools


def provider() -> AnthropicProvider:
    return AnthropicProvider(client=object())


def request(transcript) -> ModelRequest:
    return ModelRequest(
        system="SYSTEM", transcript=transcript, tools=default_tools().specs, effort="medium", max_tokens=1000
    )


def anthropic_turn(content, stop="end") -> AssistantTurn:
    return AssistantTurn(
        text="", tool_calls=[], stop=stop, usage=Usage(), model="claude-opus-5-5",
        native={"provider": "anthropic", "model": "claude-opus-5-5", "content": content},
    )


def test_params_cache_the_stable_prefix_and_enable_fallbacks():
    params = provider().build_params(request([UserTurn("Sam", "hi", ts="2026-10-29T17:40:00-04:00")]))

    assert params["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert params["cache_control"] == {"type": "ephemeral"}
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "medium"}
    assert params["tools"][0]["name"] == "roll" and params["tools"][0]["eager_input_streaming"]
    assert params["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "[2026-10-29 17:40] Sam: hi"}]}
    ]


def test_consecutive_entries_merge_with_tool_results_first():
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    tool_use = {"type": "tool_use", "id": "toolu_1", "name": "roll", "input": {"dice": "d6"}}
    transcript = [
        ContextNote("Session started.", ts="2026-10-29T17:40:00-04:00"),
        UserTurn("Sam", "roll", ts="2026-10-29T17:40:00-04:00"),
        anthropic_turn([thinking, tool_use], stop="tool_use"),
        ToolResults([ToolResult("toolu_1", "roll", "d6: [4] = 4")]),
        UserTurn("Alex", "nice", ts="2026-10-29T17:41:00-04:00"),
    ]

    messages = provider().build_params(request(transcript))["messages"]

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][0]["text"] == "<context>\nSession started.\n</context>"
    assert messages[1]["content"] == [thinking, tool_use]  # replayed unchanged
    assert messages[2]["content"][0] == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "d6: [4] = 4"}
    assert messages[2]["content"][1]["text"] == "[2026-10-29 17:41] Alex: nice"


def test_turns_from_another_provider_are_rebuilt_from_neutral_fields():
    turn = AssistantTurn(
        text="Let me roll.", tool_calls=[ToolCall("call_9", "roll", {"dice": "d20"})], stop="tool_use",
        usage=Usage(), model="other-model", native={"provider": "other", "content": "..."},
    )
    messages = provider().build_params(request([UserTurn("Sam", "hi"), turn]))["messages"]

    assert messages[1]["content"] == [
        {"type": "text", "text": "Let me roll."},
        {"type": "tool_use", "id": "call_9", "name": "roll", "input": {"dice": "d20"}},
    ]


def test_echo_drops_declined_partial_blocks_before_the_last_fallback():
    content = [
        {"type": "thinking", "thinking": "", "signature": "a"},
        {"type": "text", "text": "Part one, "},
        {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"}},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "part two."},
    ]
    assert [b["type"] for b in echo_content(content)] == ["text", "fallback", "thinking", "text"]


def block(data):
    return SimpleNamespace(to_dict=lambda mode="json": data)


def test_parse_message_reads_text_tools_usage_and_cost():
    message = SimpleNamespace(
        content=[
            block({"type": "text", "text": "Rolling. "}),
            block({"type": "tool_use", "id": "toolu_2", "name": "roll", "input": {"dice": "d20"}}),
        ],
        stop_reason="tool_use",
        model="claude-opus-5-5",
        usage=SimpleNamespace(
            input_tokens=1_000_000, output_tokens=100_000,
            cache_read_input_tokens=1_000_000, cache_creation_input_tokens=0,
        ),
    )

    response = parse_message(message)

    assert response.text == "Rolling. "
    assert response.tool_calls == [ToolCall("toolu_2", "roll", {"dice": "d20"})]
    assert response.stop == "tool_use"
    assert response.usage.cost_usd == 4.00 + 2.00 + 0.20
    assert response.native["provider"] == "anthropic"
