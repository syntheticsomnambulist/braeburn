"""The interface every model provider adapter implements."""

from __future__ import annotations

from typing import Callable, Protocol

from ..model import ModelRequest, ModelResponse

TextCallback = Callable[[str], None]


class ProviderError(Exception):
    """A model call failed. `retryable` says whether trying again might work."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class Provider(Protocol):
    name: str
    model: str

    def complete(self, request: ModelRequest, on_text: TextCallback | None = None) -> ModelResponse:
        """Make one streamed model call. Text deltas go to `on_text` as they arrive."""
        ...
