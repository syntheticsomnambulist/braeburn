from __future__ import annotations

from pathlib import Path

import pytest

from braeburn.config import ROOT, Config
from braeburn.model import ModelRequest, ModelResponse, Usage


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        root=ROOT,
        state_dir=tmp_path / "state",
        provider="fake",
        model=None,
        effort="medium",
        max_tokens=1000,
    )


class FakeProvider:
    """Replays scripted responses and records every request it gets."""

    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list[ModelResponse]):
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request, on_text=None):
        # Copy the transcript: the engine keeps appending to the same list.
        self.requests.append(ModelRequest(**{**request.__dict__, "transcript": list(request.transcript)}))
        response = self.responses.pop(0)
        if on_text and response.text:
            on_text(response.text)
        return response


def reply(text="", tool_calls=None, stop="end", cost=0.01) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=tool_calls or [],
        stop=stop,
        usage=Usage(input_tokens=100, output_tokens=10, cost_usd=cost),
        model="fake-model",
        native={"provider": "fake", "content": text},
    )
