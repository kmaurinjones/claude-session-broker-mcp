from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

import uvicorn

from .broker_app import create_broker_app
from .runtime import SessionIdentity, SessionRuntime, discover_git_root
from .server_app import create_disabled_server, create_server, run_server_stdio


def read_env(name: str) -> str | None:
    if name in os.environ:
        return os.environ[name]
    return None


def first_env(*names: str) -> str | None:
    for name in names:
        value = read_env(name)
        if value is not None and value != "":
            return value
    return None


def local_broker_endpoint(broker_url: str) -> tuple[str, int] | None:
    parsed = urlparse(broker_url)
    if parsed.scheme != "http":
        return None
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return None
    if parsed.port is None:
        return None
    return parsed.hostname, parsed.port


def broker_healthy(broker_url: str) -> bool:
    health_url = broker_url.rstrip("/") + "/health"
    try:
        with urlopen(health_url, timeout=1) as response:
            return response.status == 200
    except (URLError, OSError):
        return False


def tty_name_for_pid(pid: int) -> str | None:
    completed = subprocess.run(
        ["ps", "-o", "tty=", "-p", str(pid)],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        return None
    tty_name = completed.stdout.strip()
    if tty_name in {"", "?", "??"}:
        return None
    return tty_name


def launch_registry_file(data_dir: Path, tty_name: str) -> Path:
    return data_dir / "launches" / f"{tty_name}.json"


def load_launch_identity_for_tty(data_dir: Path, tty_name: str, cwd: Path) -> dict[str, str] | None:
    registry_file = launch_registry_file(data_dir, tty_name)
    if not registry_file.exists():
        return None

    payload_raw = json.loads(registry_file.read_text())
    if not isinstance(payload_raw, dict):
        raise ValueError(f"Launch registry '{registry_file}' must contain a JSON object.")
    required_keys = {"session_name", "group_name", "description", "cwd", "tty", "updated_at"}
    missing_keys = required_keys.difference(payload_raw)
    if len(missing_keys) > 0:
        missing_list = ", ".join(sorted(missing_keys))
        raise ValueError(
            f"Launch registry '{registry_file}' is missing required keys: {missing_list}."
        )

    launch_cwd = payload_raw["cwd"]
    if not isinstance(launch_cwd, str):
        raise ValueError(f"Launch registry '{registry_file}' has a non-string cwd.")
    if launch_cwd != str(cwd):
        return None

    session_name = payload_raw["session_name"]
    group_name = payload_raw["group_name"]
    description = payload_raw["description"]
    tty_value = payload_raw["tty"]
    updated_at = payload_raw["updated_at"]
    if not isinstance(session_name, str) or session_name == "":
        raise ValueError(f"Launch registry '{registry_file}' has an invalid session_name.")
    if not isinstance(group_name, str) or group_name == "":
        raise ValueError(f"Launch registry '{registry_file}' has an invalid group_name.")
    if not isinstance(description, str):
        raise ValueError(f"Launch registry '{registry_file}' has an invalid description.")
    if not isinstance(tty_value, str) or tty_value != tty_name:
        raise ValueError(f"Launch registry '{registry_file}' has an invalid tty.")
    if not isinstance(updated_at, str) or updated_at == "":
        raise ValueError(f"Launch registry '{registry_file}' has an invalid updated_at.")

    return {
        "session_name": session_name,
        "group_name": group_name,
        "description": description,
    }


def load_launch_identity(data_dir: Path, cwd: Path) -> dict[str, str] | None:
    candidate_pids = [os.getpid(), os.getppid()]
    seen_ttys: set[str] = set()
    for pid in candidate_pids:
        tty_name = tty_name_for_pid(pid)
        if tty_name is None:
            continue
        if tty_name in seen_ttys:
            continue
        seen_ttys.add(tty_name)
        launch_identity = load_launch_identity_for_tty(
            data_dir=data_dir,
            tty_name=tty_name,
            cwd=cwd,
        )
        if launch_identity is not None:
            return launch_identity
    return None


def ensure_local_broker_running(broker_url: str, data_dir: Path) -> None:
    if broker_healthy(broker_url):
        return

    endpoint = local_broker_endpoint(broker_url)
    if endpoint is None:
        raise SystemExit(
            "The broker is unavailable and the configured broker URL is not a "
            "local HTTP endpoint. Start the broker manually."
        )

    host, port = endpoint
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "broker.log"

    with log_path.open("ab") as log_file:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "claude_session_broker_mcp.broker_entry",
                "--host",
                host,
                "--port",
                str(port),
                "--data-dir",
                str(data_dir),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    for _ in range(50):
        if broker_healthy(broker_url):
            return
        time.sleep(0.1)

    raise SystemExit(
        "Attempted to auto-start the local broker, but its health check never "
        f"passed. Inspect '{log_path}'."
    )


def parse_broker_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="claude-session-broker-broker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7899)
    parser.add_argument(
        "--data-dir",
        default=".session-broker",
        help="Directory that will hold broker.db.",
    )
    parser.add_argument("--session-ttl-seconds", type=int, default=120)
    return parser.parse_args()


def parse_server_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="claude-session-broker-server")
    parser.add_argument(
        "--broker-url",
        default=first_env("CLAUDE_SESSION_BROKER_URL", "CC_BROKER_URL")
        or "http://127.0.0.1:7899",
    )
    parser.add_argument(
        "--name",
        default=first_env("CLAUDE_SESSION_BROKER_NAME", "CC_NAME"),
    )
    parser.add_argument(
        "--group",
        default=first_env("CLAUDE_SESSION_BROKER_GROUP", "CC_GROUP"),
    )
    parser.add_argument(
        "--description",
        default=first_env("CLAUDE_SESSION_BROKER_DESCRIPTION", "CC_DESCRIPTION") or "",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(
            first_env("CLAUDE_SESSION_BROKER_POLL_SECONDS", "CC_POLL_SECONDS") or "1.0"
        ),
    )
    parser.add_argument(
        "--broker-data-dir",
        default=first_env("CLAUDE_SESSION_BROKER_DATA_DIR", "CC_BROKER_DATA_DIR")
        or str(Path.home() / ".claude-session-broker"),
    )
    parser.add_argument(
        "--no-auto-start-broker",
        action="store_true",
        help="Disable automatic startup of the local broker when it is missing.",
    )
    parser.add_argument("--status", choices=["idle", "busy", "blocked"], default="idle")
    parser.add_argument("--cwd", default=os.getcwd())
    return parser.parse_args()


def parse_doctor_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="claude-session-broker-doctor")
    parser.add_argument("--broker-url", default=read_env("CLAUDE_SESSION_BROKER_URL") or "http://127.0.0.1:7899")
    return parser.parse_args()


def run_broker() -> None:
    args = parse_broker_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    app = create_broker_app(
        database_path=data_dir / "broker.db",
        session_ttl_seconds=args.session_ttl_seconds,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def run_server() -> None:
    args = parse_server_args()
    broker_data_dir = Path(args.broker_data_dir).expanduser().resolve()
    cwd = Path(args.cwd).expanduser().resolve()
    launch_identity = load_launch_identity(data_dir=broker_data_dir, cwd=cwd)

    if args.name is None and launch_identity is not None:
        args.name = launch_identity["session_name"]
    if args.group is None and launch_identity is not None:
        args.group = launch_identity["group_name"]
    if args.description == "" and launch_identity is not None:
        args.description = launch_identity["description"]

    missing_environment_variables: list[str] = []
    if args.name is None or args.name == "":
        missing_environment_variables.append("CLAUDE_SESSION_BROKER_NAME or CC_NAME")
    if args.group is None or args.group == "":
        missing_environment_variables.append("CLAUDE_SESSION_BROKER_GROUP or CC_GROUP")
    if len(missing_environment_variables) > 0:
        server = create_disabled_server(missing_environment_variables)
        asyncio.run(server.run_stdio_async())
        return

    if not args.no_auto_start_broker:
        ensure_local_broker_running(
            broker_url=args.broker_url,
            data_dir=broker_data_dir,
        )

    session_name = args.name
    group_name = args.group
    identity = SessionIdentity(
        session_name=session_name,
        group_name=group_name,
        description=args.description,
        cwd=cwd,
        git_root=discover_git_root(cwd),
        pid=os.getpid(),
        hostname=socket.gethostname(),
        status=args.status,
    )
    runtime = SessionRuntime(
        broker_url=args.broker_url,
        identity=identity,
        poll_seconds=args.poll_seconds,
    )
    server = create_server(runtime)
    asyncio.run(run_server_stdio(server, runtime))


def run_doctor() -> None:
    import json
    import urllib.request

    args = parse_doctor_args()
    url = args.broker_url.rstrip("/") + "/health"
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    print(json.dumps(payload, indent=2, sort_keys=True))
