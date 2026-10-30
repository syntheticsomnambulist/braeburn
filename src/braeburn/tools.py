"""Tools Braeburn can call, and the registry that validates and runs them."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Callable

import jsonschema

from .model import ToolCall, ToolResult, ToolSpec


class ToolError(Exception):
    """A tool failed in a way the model should hear about and can fix."""


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    handler: Callable[[dict[str, Any]], str]


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {tool.spec.name: tool for tool in tools}

    @property
    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def run(self, call: ToolCall) -> ToolResult:
        def error(message: str) -> ToolResult:
            return ToolResult(call.id, call.name, message, is_error=True)

        tool = self._tools.get(call.name)
        if tool is None:
            return error(f"There is no tool named {call.name!r}.")
        # Providers don't all validate arguments (Anthropic's eager input
        # streaming doesn't), so the core always does.
        try:
            jsonschema.validate(call.args, tool.spec.schema)
        except jsonschema.ValidationError as e:
            return error(f"Invalid arguments: {e.message}")
        try:
            return ToolResult(call.id, call.name, tool.handler(call.args))
        except ToolError as e:
            return error(str(e))
        except Exception as e:  # a broken tool shouldn't end the conversation
            return error(f"The tool crashed: {type(e).__name__}: {e}")


DICE = re.compile(r"^\s*(\d*)\s*d\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*$", re.IGNORECASE)


def roll_tool(rng: random.Random | None = None) -> Tool:
    rng = rng or random.Random()

    def roll(args: dict[str, Any]) -> str:
        notation = args["dice"].strip()
        match = DICE.match(notation)
        if not match:
            raise ToolError(f"Can't read {notation!r}. Use dice notation like d20, 3d6, or 2d8+3.")
        count = int(match[1] or 1)
        sides = int(match[2])
        modifier = int(match[4] or 0) * (-1 if match[3] == "-" else 1)
        if not (1 <= count <= 100 and 2 <= sides <= 1000):
            raise ToolError("Roll between 1 and 100 dice, each with 2 to 1000 sides.")
        rolls = [rng.randint(1, sides) for _ in range(count)]
        total = sum(rolls) + modifier
        shown = f"{rolls} {'+' if modifier >= 0 else '-'} {abs(modifier)}" if modifier else str(rolls)
        return f"{notation}: {shown} = {total}"

    spec = ToolSpec(
        name="roll",
        description=(
            "Roll dice. Use it when chance should decide something: a story beat, a contested "
            "action, a choice between options. Takes standard notation such as d20, 3d6, or 2d8+3."
        ),
        schema={
            "type": "object",
            "properties": {
                "dice": {"type": "string", "description": "Dice notation, e.g. d20, 3d6, 2d8+3"},
            },
            "required": ["dice"],
            "additionalProperties": False,
        },
    )
    return Tool(spec, roll)


def default_tools() -> ToolRegistry:
    # Append new tools at the end: the tool list is part of the cached prefix.
    return ToolRegistry([roll_tool()])
