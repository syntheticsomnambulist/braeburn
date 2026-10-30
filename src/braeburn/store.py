"""Append-only session logs and the cost ledger, both as JSONL under state/."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .model import Entry, Usage, UserTurn, entry_from_dict, entry_to_dict, now, stamp

ENTRY_KINDS = {"user", "context", "assistant", "tool_results"}


def _write_line(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


class SessionLog:
    """One session's JSONL file. Line one is the header; the rest are entries
    and events, written the moment they happen and never rewritten.
    """

    def __init__(self, path: Path, header: dict[str, Any], entries: list[Entry], reflected_upto: int = 0):
        self.path = path
        self.header = header
        self.entries = entries
        self.reflected_upto = reflected_upto  # entries before this index are in memory

    @property
    def id(self) -> str:
        return self.header["id"]

    @property
    def system(self) -> str:
        return self.header["system"]

    @property
    def frontend(self) -> str:
        return self.header.get("frontend", "terminal")

    def unreflected_speakers(self) -> list[tuple[str, str | None]]:
        """(name, participant id) for everyone who spoke since the last reflection."""
        seen: dict[tuple[str, str | None], None] = {}
        for entry in self.entries[self.reflected_upto :]:
            if isinstance(entry, UserTurn):
                seen.setdefault((entry.speaker, entry.speaker_id), None)
        return list(seen)

    @property
    def needs_reflection(self) -> bool:
        return bool(self.unreflected_speakers())

    @classmethod
    def create(cls, sessions_dir: Path, *, system: str, **meta: Any) -> SessionLog:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_id = f"{now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        header = {"kind": "session", "id": session_id, "started": stamp(), **meta, "system": system}
        path = sessions_dir / f"{session_id}.jsonl"
        _write_line(path, header)
        return cls(path, header, [])

    @classmethod
    def load(cls, path: Path) -> SessionLog:
        lines = path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        entries: list[Entry] = []
        reflected_upto = 0
        for number, line in enumerate(lines[1:], start=2):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                if number == len(lines):
                    break  # a crash mid-write left a partial last line
                raise
            if record.get("kind") in ENTRY_KINDS:
                entries.append(entry_from_dict(record))
            elif record.get("event") == "reflected":
                reflected_upto = record["entries"]
        return cls(path, header, entries, reflected_upto)

    def append(self, entry: Entry) -> None:
        _write_line(self.path, entry_to_dict(entry))
        self.entries.append(entry)

    def mark(self, event: str) -> None:
        """Record a lifecycle event ("reflected", "end"). Not part of the transcript."""
        _write_line(self.path, {"kind": "event", "event": event, "ts": stamp(), "entries": len(self.entries)})
        if event == "reflected":
            self.reflected_upto = len(self.entries)


def list_sessions(sessions_dir: Path) -> list[Path]:
    """Session files, newest first."""
    if not sessions_dir.exists():
        return []
    return sorted(sessions_dir.glob("*.jsonl"), reverse=True)


def find_session(sessions_dir: Path, session_id: str | None = None) -> Path | None:
    """The newest session, or the one whose id starts with `session_id`."""
    for path in list_sessions(sessions_dir):
        if session_id is None or path.stem.startswith(session_id):
            return path
    return None


class Ledger:
    def __init__(self, path: Path):
        self.path = path

    def record(self, *, session: str, purpose: str, provider: str, model: str, usage: Usage) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_line(
            self.path,
            {"ts": stamp(), "session": session, "purpose": purpose, "provider": provider, "model": model, **asdict(usage)},
        )
