from __future__ import annotations

import random

import pytest

from braeburn.model import AssistantTurn, ToolCall, ToolResult, ToolResults, Usage, UserTurn
from braeburn.store import SessionLog, find_session
from braeburn.tools import ToolRegistry, roll_tool


@pytest.fixture
def tools() -> ToolRegistry:
    return ToolRegistry([roll_tool(random.Random(7))])


@pytest.mark.parametrize("dice", ["d20", "3d6", "2d8+3", "4 d 6 - 1", "D12"])
def test_roll_accepts_standard_notation(tools, dice):
    result = tools.run(ToolCall("c", "roll", {"dice": dice}))
    assert not result.is_error and " = " in result.output


def test_roll_math_includes_the_modifier(tools):
    output = tools.run(ToolCall("c", "roll", {"dice": "2d6-1"})).output
    rolls = [int(n) for n in output.split("[")[1].split("]")[0].split(",")]
    assert output.endswith(f"= {sum(rolls) - 1}")


@pytest.mark.parametrize(
    "args, message",
    [
        ({"dice": "a fistful"}, "Can't read"),
        ({"dice": "1000d6"}, "Roll between"),
        ({}, "Invalid arguments"),
        ({"dice": "d6", "extra": 1}, "Invalid arguments"),
    ],
)
def test_bad_roll_arguments_come_back_as_errors(tools, args, message):
    result = tools.run(ToolCall("c", "roll", args))
    assert result.is_error and message in result.output


def test_unknown_tool_is_an_error(tools):
    assert tools.run(ToolCall("c", "teleport", {})).is_error


def test_session_log_round_trips(tmp_path):
    log = SessionLog.create(tmp_path, system="SYSTEM", frontend="terminal")
    entries = [
        UserTurn("Sam", "hi"),
        AssistantTurn("hey", [ToolCall("c1", "roll", {"dice": "d6"})], "tool_use", Usage(1, 2, 3, 4, 0.5), "m"),
        ToolResults([ToolResult("c1", "roll", "d6: [3] = 3")]),
    ]
    for entry in entries:
        log.append(entry)
    log.mark("end")

    loaded = SessionLog.load(log.path)

    assert loaded.entries == entries
    assert loaded.system == "SYSTEM" and loaded.header["frontend"] == "terminal"


def test_a_partial_last_line_is_ignored(tmp_path):
    log = SessionLog.create(tmp_path, system="SYSTEM")
    log.append(UserTurn("Sam", "hi"))
    with log.path.open("a", encoding="utf-8") as f:
        f.write('{"kind": "assistant", "te')

    assert len(SessionLog.load(log.path).entries) == 1


def test_find_session_by_prefix(tmp_path):
    first = SessionLog.create(tmp_path, system="S")
    assert find_session(tmp_path, first.id[:15]) == first.path
    assert find_session(tmp_path, "nope") is None
