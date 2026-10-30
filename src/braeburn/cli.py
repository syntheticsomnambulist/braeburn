"""Terminal front-end: `braeburn chat` and `braeburn sessions`."""

from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .engine import Engine, Session
from .model import AssistantTurn, ToolCall, ToolResult, Usage, UserTurn
from .providers import ProviderError
from .store import list_sessions

HELP = """\
Commands:
  /cost   tokens and cost so far this session
  /help   this list
  /bye    end the session (also /quit, /exit, Ctrl+C at the prompt)"""


def fmt_usage(usage: Usage) -> str:
    return (
        f"[in {usage.input_tokens:,} | cache read {usage.cache_read_tokens:,} | "
        f"cache write {usage.cache_write_tokens:,} | out {usage.output_tokens:,} | ${usage.cost_usd:.4f}]"
    )


def show_text(text: str) -> None:
    print(text, end="", flush=True)


def show_tool(call: ToolCall, result: ToolResult) -> None:
    status = "failed" if result.is_error else "ok"
    print(f"\n  ({call.name} {status}: {result.output})\n", flush=True)


def show_recent(session: Session, count: int = 2) -> None:
    """Print the last few lines of a resumed session for orientation."""
    lines = []
    for entry in session.log.entries:
        if isinstance(entry, UserTurn):
            lines.append(f"{entry.speaker}> {entry.text}")
        elif isinstance(entry, AssistantTurn) and entry.text and entry.stop != "refusal":
            lines.append(f"Braeburn: {entry.text}")
    for line in lines[-count:]:
        print(line)


def ask_name() -> str:
    while True:
        name = input("Who's here? ").strip()
        if name:
            return name


def run_chat(args: argparse.Namespace) -> int:
    cfg = load_config()
    try:
        engine = Engine(cfg)
    except ProviderError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    try:
        speaker = args.speaker or ask_name()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0

    if args.resume:
        try:
            session = engine.resume_session(
                speaker=speaker, session_id=None if args.resume == "latest" else args.resume
            )
        except LookupError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1
        print(f"Resumed session {session.id}.")
        show_recent(session)
    else:
        session = engine.start_session(speaker=speaker, frontend="terminal")
        print(f"Session {session.id}. Type /help for commands.")

    total = Usage()
    while True:
        try:
            line = input(f"{speaker}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            line = "/bye"
        if not line:
            continue
        if line.startswith("/"):
            command = line.split()[0].lower()
            if command in ("/bye", "/quit", "/exit"):
                session.end()
                print(f"Bye. This session cost ${total.cost_usd:.4f}.")
                return 0
            if command == "/help":
                print(HELP)
            elif command == "/cost":
                print(fmt_usage(total))
            else:
                print(f"Unknown command {command}. Type /help.")
            continue

        print("Braeburn: ", end="", flush=True)
        try:
            reply = session.send(speaker, line, on_text=show_text, on_tool=show_tool)
        except ProviderError as e:
            hint = " Try again in a moment." if e.retryable else ""
            print(f"\n[error] {e}{hint}")
            continue
        except KeyboardInterrupt:
            print("\n[interrupted; that reply wasn't saved]")
            continue
        print()
        if reply.refused:
            print("[that reply was stopped by a safety filter]")
        total += reply.usage
        if args.debug:
            print(fmt_usage(reply.usage))


def run_sessions(args: argparse.Namespace) -> int:
    cfg = load_config()
    paths = list_sessions(cfg.state_dir / "sessions")[: args.limit]
    if not paths:
        print("No sessions yet.")
        return 0
    for path in paths:
        with path.open(encoding="utf-8") as f:
            header = json.loads(f.readline())
            first = next((json.loads(line) for line in f if '"kind": "user"' in line), None)
        preview = f"{first['speaker']}: {first['text'][:60]}" if first else "(no messages)"
        print(f"{header['id']}  {header.get('frontend', '?'):<9} {preview}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="braeburn", description="Talk to Braeburn, a persistent persona agent.")
    commands = parser.add_subparsers(dest="command", required=True)

    chat = commands.add_parser("chat", help="start or resume a terminal chat")
    chat.add_argument("--as", dest="speaker", metavar="NAME", help="your name (asked for if omitted)")
    chat.add_argument(
        "--resume", nargs="?", const="latest", metavar="ID", help="resume the latest session, or the one with this id"
    )
    chat.add_argument("--debug", action="store_true", help="show tokens and cost after each reply")
    chat.set_defaults(run=run_chat)

    sessions = commands.add_parser("sessions", help="list recent sessions")
    sessions.add_argument("--limit", type=int, default=10, help="how many to show (default 10)")
    sessions.set_defaults(run=run_sessions)

    args = parser.parse_args(argv)
    return args.run(args)
