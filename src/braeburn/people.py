"""Participant records: the people and agents Braeburn talks to."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .model import stamp

KINDS = ("human", "agent")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "someone"


@dataclass
class Participant:
    id: str  # stable; names their notes file
    name: str
    kind: str = "human"
    identities: list[str] = field(default_factory=list)  # e.g. terminal:sam, discord:1234
    first_seen: str = field(default_factory=stamp)
    last_seen: str = field(default_factory=stamp)

    @property
    def notes_path(self) -> str:
        return f"/memories/people/{self.id}.md"


class People:
    """The registry in state/people.json.

    One record per person or agent. Each front-end identity links to exactly
    one record, so the same person can be recognized across front-ends once
    their identities are linked.
    """

    def __init__(self, path: Path):
        self.path = path
        self._records: dict[str, Participant] = {}
        if path.exists():
            for record in json.loads(path.read_text(encoding="utf-8")):
                self._records[record["id"]] = Participant(**record)

    def all(self) -> list[Participant]:
        return list(self._records.values())

    def get(self, participant_id: str) -> Participant | None:
        return self._records.get(participant_id)

    def find(self, identity: str) -> Participant | None:
        return next((p for p in self._records.values() if identity in p.identities), None)

    def resolve(self, identity: str, name: str, kind: str = "human") -> Participant:
        """The participant behind `identity`, created on first sight."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
        participant = self.find(identity)
        if participant is None:
            base = participant_id = slug(name)
            n = 2
            while participant_id in self._records:
                participant_id = f"{base}-{n}"
                n += 1
            participant = Participant(participant_id, name, kind, [identity])
            self._records[participant_id] = participant
        participant.last_seen = stamp()
        self._save()
        return participant

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = [asdict(p) for p in self._records.values()]
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)
