"""Model provider adapters. Each one translates the neutral core to one API."""

from __future__ import annotations

from ..config import Config
from .base import Provider, ProviderError

PROVIDERS = ("anthropic",)


def get_provider(cfg: Config) -> Provider:
    # Imported lazily so a provider's SDK is only needed when it's used.
    if cfg.provider == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(model=cfg.model)
    raise ValueError(f"Unknown provider {cfg.provider!r}. Available: {', '.join(PROVIDERS)}")


__all__ = ["Provider", "ProviderError", "get_provider"]
