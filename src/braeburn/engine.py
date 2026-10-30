"""The agent loop. Front-ends talk to Braeburn through Engine and Session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import Config
from .memory import MemoryStore, memory_tool
from .model import AssistantTurn, ContextNote, ModelRequest, ToolCall, ToolResult, ToolResults, Usage, UserTurn
from .people import Participant, People
from .prompt import REFUSAL_NOTE, build_system, joined_note, reflection_note, resume_note, session_start_note
from .providers import Provider, get_provider
from .providers.base import TextCallback
from .store import Ledger, SessionLog, find_session, list_sessions
from .tools import ToolRegistry, roll_tool

# Most model calls one turn can trigger (tool use loops back). Reflection gets
# more, since updating several files takes several calls.
MAX_STEPS = 8
REFLECT_STEPS = 16

ToolCallback = Callable[[ToolCall, ToolResult], None]


def default_tools(memory: MemoryStore) -> ToolRegistry:
    # Append new tools at the end: the tool list is part of the cached prefix.
    return ToolRegistry([roll_tool(), memory_tool(memory)])


@dataclass
class Reply:
    text: str
    usage: Usage
    refused: bool = False


class Engine:
    def __init__(self, cfg: Config, provider: Provider | None = None, tools: ToolRegistry | None = None):
        self.cfg = cfg
        self.provider = provider or get_provider(cfg)
        self.memory = MemoryStore(cfg.state_dir / "memories")
        self.people = People(cfg.state_dir / "people.json")
        self.tools = tools or default_tools(self.memory)
        self.sessions_dir = cfg.state_dir / "sessions"
        self.ledger = Ledger(cfg.state_dir / "ledger.jsonl")

    def start_session(
        self, *, speaker: str, frontend: str, channel: str | None = None, handle: str | None = None
    ) -> Session:
        log = SessionLog.create(
            self.sessions_dir,
            system=build_system(self.cfg),
            frontend=frontend,
            channel=channel,
            provider=self.provider.name,
            model=self.provider.model,
        )
        session = Session(self, log)
        first = session.identify(speaker, handle)
        session.present[first.id] = first
        log.append(ContextNote(f"{session_start_note(frontend, channel, [first])}\n\n{self.memory.briefing([first])}"))
        return session

    def resume_session(self, *, speaker: str, session_id: str | None = None, handle: str | None = None) -> Session:
        """Reopen a session with the system prefix it started with."""
        path = find_session(self.sessions_dir, session_id)
        if path is None:
            raise LookupError(f"No session matching {session_id!r}." if session_id else "No sessions yet.")
        session = Session(self, SessionLog.load(path))
        first = session.identify(speaker, handle)
        session.present[first.id] = first
        session.log.append(ContextNote(f"{resume_note([first])}\n\n{self.memory.briefing([first])}"))
        return session

    def unreflected_sessions(self, limit: int = 5, exclude: str | None = None) -> list[Session]:
        """Recent sessions with conversation that hasn't been reflected into memory,
        usually because they were closed without saying goodbye.
        """
        sessions = []
        for path in list_sessions(self.sessions_dir)[:limit]:
            log = SessionLog.load(path)
            if log.id != exclude and log.needs_reflection:
                sessions.append(Session(self, log))
        return sessions


class Session:
    def __init__(self, engine: Engine, log: SessionLog):
        self.engine = engine
        self.log = log
        self.present: dict[str, Participant] = {}

    @property
    def id(self) -> str:
        return self.log.id

    def identify(self, name: str, handle: str | None = None) -> Participant:
        """The participant behind a name on this session's front-end. `handle`
        is the front-end's stable id for them (a Discord user id, say); the
        terminal goes by name.
        """
        identity = f"{self.log.frontend}:{handle or name.strip().lower()}"
        return self.engine.people.resolve(identity, name.strip())

    def join(self, name: str, handle: str | None = None) -> Participant:
        """Bring someone into the conversation, showing Braeburn its notes on them."""
        participant = self.identify(name, handle)
        if participant.id not in self.present:
            self.present[participant.id] = participant
            notes = self.engine.memory.participant_notes(participant)
            self.log.append(ContextNote(f"{joined_note(participant)}\n\n{notes}"))
        return participant

    def hear(self, speaker: str, text: str, handle: str | None = None) -> None:
        """Add a message without replying yet (for front-ends that batch messages)."""
        participant = self.join(speaker, handle)
        self.log.append(UserTurn(speaker.strip(), text, speaker_id=participant.id))

    def respond(self, on_text: TextCallback | None = None, on_tool: ToolCallback | None = None) -> Reply:
        """Run the loop until Braeburn is done replying.

        Raises ProviderError if a model call fails. Everything up to the
        failure is already saved, so the next call carries on from there.
        """
        return self._respond(on_text, on_tool, purpose="chat", max_steps=MAX_STEPS)

    def send(
        self,
        speaker: str,
        text: str,
        on_text: TextCallback | None = None,
        on_tool: ToolCallback | None = None,
        handle: str | None = None,
    ) -> Reply:
        self.hear(speaker, text, handle)
        return self.respond(on_text, on_tool)

    def reflect(self, on_text: TextCallback | None = None, on_tool: ToolCallback | None = None) -> Reply | None:
        """Have Braeburn update its memory from the conversation since the last reflection.

        This continues the same conversation, so the cached history is reused.
        Returns None when there's nothing new to reflect on.
        """
        speakers = self.log.unreflected_speakers()
        if not speakers:
            return None
        self.log.append(ContextNote(reflection_note(self.engine.cfg, speakers)))
        reply = self._respond(on_text, on_tool, purpose="reflect", max_steps=REFLECT_STEPS)
        self.log.mark("reflected")
        return reply

    def end(self) -> None:
        self.log.mark("end")

    def _respond(
        self, on_text: TextCallback | None, on_tool: ToolCallback | None, *, purpose: str, max_steps: int
    ) -> Reply:
        engine, cfg = self.engine, self.engine.cfg
        texts: list[str] = []
        total = Usage()
        for _ in range(max_steps):
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
                purpose=purpose,
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
