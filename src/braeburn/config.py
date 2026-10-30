"""Settings, read from the environment and .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Config:
    root: Path
    state_dir: Path
    provider: str
    model: str | None  # None means the provider's default
    effort: str
    max_tokens: int

    @property
    def prompts_dir(self) -> Path:
        return self.root / "prompts"


def load_config(root: Path = ROOT) -> Config:
    load_dotenv(root / ".env")
    effort = os.environ.get("BRAEBURN_EFFORT", "medium")
    if effort not in EFFORTS:
        raise ValueError(f"BRAEBURN_EFFORT must be one of {', '.join(EFFORTS)}, not {effort!r}")
    state_dir = os.environ.get("BRAEBURN_STATE_DIR")
    return Config(
        root=root,
        state_dir=Path(state_dir) if state_dir else root / "state",
        provider=os.environ.get("BRAEBURN_PROVIDER", "anthropic"),
        model=os.environ.get("BRAEBURN_MODEL") or None,
        effort=effort,
        max_tokens=int(os.environ.get("BRAEBURN_MAX_TOKENS", "64000")),
    )
