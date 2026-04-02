#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="record_claude_launch.py")
    parser.add_argument("--tty", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--data-dir", default=str(Path.home() / ".claude-session-broker"))
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--session-name")
    parser.add_argument("--group-name")
    parser.add_argument("--description", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    launch_dir = data_dir / "launches"
    launch_dir.mkdir(parents=True, exist_ok=True)
    launch_file = launch_dir / f"{args.tty}.json"

    if args.clear:
        launch_file.unlink(missing_ok=True)
        return 0

    if args.session_name is None or args.session_name == "":
        raise SystemExit("--session-name is required unless --clear is used.")
    if args.group_name is None or args.group_name == "":
        raise SystemExit("--group-name is required unless --clear is used.")

    payload = {
        "session_name": args.session_name,
        "group_name": args.group_name,
        "description": args.description,
        "cwd": str(Path(args.cwd).expanduser().resolve()),
        "tty": args.tty,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "pid": os.getpid(),
    }
    temporary_file = launch_file.with_suffix(".json.tmp")
    temporary_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary_file.replace(launch_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
