"""The agent loop. Front-ends talk to Braeburn through Engine and Session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import Config
from .model import AssistantTurn, ContextNote, ModelRequest, ToolCall, ToolResult, ToolResults, Usage, UserTurn
from .prompt import REFUSAL_NOTE, build_system, resume_note, session_start_note
from .providers import Provider, get_provider
from .providers.base import TextCallback
from .store import Ledger, SessionLog, find_session
from .tools import ToolRegistry, default_tools

# Most model calls one incoming message can trigger (tool use loops back).
MAX_STEPS = 8

ToolCallback = Callable[[ToolCall, ToolResult], None]


@dataclass
class Reply:
    text: str
    usage: Usage
    refused: bool = False


class Engine:
    def __init__(self, cfg: Config, provider: Provider | None = None, tools: ToolRegistry | None = None):
        self.cfg = cfg
        self.provider = provider or get_provider(cfg)
        self.tools = tools or default_tools()
        self.sessions_dir = cfg.state_dir / "sessions"
        self.ledger = Ledger(cfg.state_dir / "ledger.jsonl")

    def start_session(self, *, speaker: str, frontend: str, channel: str | None = None) -> Session:
        log = SessionLog.create(
            self.sessions_dir,
            system=build_system(self.cfg),
            frontend=frontend,
            channel=channel,
            provider=self.provider.name,
            model=self.provider.model,
        )
        log.append(ContextNote(session_start_note(frontend, channel, speaker)))
        return Session(self, log)

    def resume_session(self, *, speaker: str, session_id: str | None = None) -> Session:
        """Reopen a session with the system prefix it started with."""
        path = find_session(self.sessions_dir, session_id)
        if path is None:
            raise LookupError(f"No session matching {session_id!r}." if session_id else "No sessions yet.")
        log = SessionLog.load(path)
        log.append(ContextNote(resume_note(speaker)))
        return Session(self, log)


class Session:
    def __init__(self, engine: Engine, log: SessionLog):
        self.engine = engine
        self.log = log

    @property
    def id(self) -> str:
        return self.log.id

    def send(
        self,
        speaker: str,
        text: str,
        on_text: TextCallback | None = None,
        on_tool: ToolCallback | None = None,
    ) -> Reply:
        """Add a message from `speaker` and run the loop until Braeburn is done replying.

        Raises ProviderError if a model call fails. Everything up to the
        failure is already saved, so the next send carries on from there.
        """
        self.log.append(UserTurn(speaker, text))
        return self._respond(on_text, on_tool)

    def end(self) -> None:
        self.log.mark("end")

    def _respond(self, on_text: TextCallback | None, on_tool: ToolCallback | None) -> Reply:
        engine, cfg = self.engine, self.engine.cfg
        texts: list[str] = []
        total = Usage()
        for _ in range(MAX_STEPS):
            request = ModelRequest(
                system=self.log.system,
                transcript=self.log.entries,
                tools=engine.tools.specs,
                effort=cfg.effort,
                max_tokens=cfg.max_tokens,
            )
            response = engine.provider.complete(request, on_text)
            self.log.append(
                AssistantTurn(
                    text=response.text,
                    tool_calls=response.tool_calls,
                    stop=response.stop,
                    usage=response.usage,
                    model=response.model,
                    native=response.native,
                )
            )
            engine.ledger.record(
                session=self.id,
                purpose="chat",
                provider=engine.provider.name,
                model=response.model,
                usage=response.usage,
            )
            total += response.usage

            if response.stop == "refusal":
                self.log.append(ContextNote(REFUSAL_NOTE))
                return Reply("".join(texts), total, refused=True)
            if response.text:
                texts.append(response.text)

            if response.tool_calls:
                # Every tool call gets a result, even ones that never run, or
                # the next request is malformed.
                if response.stop == "tool_use":
                    results = [engine.tools.run(call) for call in response.tool_calls]
                else:
                    results = [
                        ToolResult(call.id, call.name, "Not run: the reply was cut off.", is_error=True)
                        for call in response.tool_calls
                    ]
                self.log.append(ToolResults(results))
                if on_tool:
                    for call, result in zip(response.tool_calls, results):
                        on_tool(call, result)
                if response.stop == "tool_use":
                    continue
            if response.stop == "pause":
                continue
            break
        return Reply("\n\n".join(texts), total)
