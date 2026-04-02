from __future__ import annotations

import os
import subprocess
from pathlib import Path

from claude_session_broker_mcp.cli import (
    first_env,
    launch_registry_file,
    load_launch_identity_for_tty,
    local_broker_endpoint,
)


def test_local_broker_endpoint_accepts_localhost() -> None:
    assert local_broker_endpoint("http://127.0.0.1:7899") == ("127.0.0.1", 7899)
    assert local_broker_endpoint("http://localhost:9999") == ("localhost", 9999)


def test_local_broker_endpoint_rejects_non_local_urls() -> None:
    assert local_broker_endpoint("https://127.0.0.1:7899") is None
    assert local_broker_endpoint("http://example.com:7899") is None
    assert local_broker_endpoint("http://localhost") is None


def test_first_env_uses_first_defined_non_empty_value() -> None:
    original = dict(os.environ)
    try:
        os.environ.pop("CC_NAME", None)
        os.environ["CLAUDE_SESSION_BROKER_NAME"] = "controller"
        assert first_env("CC_NAME", "CLAUDE_SESSION_BROKER_NAME") == "controller"
        os.environ["CC_NAME"] = "short-name"
        assert first_env("CC_NAME", "CLAUDE_SESSION_BROKER_NAME") == "short-name"
        os.environ["CC_NAME"] = ""
        assert first_env("CC_NAME", "CLAUDE_SESSION_BROKER_NAME") == "controller"
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_load_launch_identity_for_tty_reads_matching_registry_file(tmp_path: Path) -> None:
    registry_file = launch_registry_file(tmp_path, "s007")
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(
        """
{
  "session_name": "controller",
  "group_name": "main",
  "description": "coordinator",
  "cwd": "/tmp/work",
  "tty": "s007",
  "updated_at": "2026-04-02T20:00:00+00:00"
}
""".strip()
        + "\n"
    )

    identity = load_launch_identity_for_tty(
        data_dir=tmp_path,
        tty_name="s007",
        cwd=Path("/tmp/work"),
    )

    assert identity == {
        "session_name": "controller",
        "group_name": "main",
        "description": "coordinator",
    }


def test_load_launch_identity_for_tty_ignores_mismatched_cwd(tmp_path: Path) -> None:
    registry_file = launch_registry_file(tmp_path, "s007")
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_file.write_text(
        """
{
  "session_name": "controller",
  "group_name": "main",
  "description": "",
  "cwd": "/tmp/other",
  "tty": "s007",
  "updated_at": "2026-04-02T20:00:00+00:00"
}
""".strip()
        + "\n"
    )

    identity = load_launch_identity_for_tty(
        data_dir=tmp_path,
        tty_name="s007",
        cwd=Path("/tmp/work"),
    )

    assert identity is None


def test_record_launch_script_runs_with_system_python(tmp_path: Path) -> None:
    script_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "record_claude_launch.py"
    )
    launch_file = launch_registry_file(tmp_path, "ttys123")

    completed = subprocess.run(
        [
            "/usr/bin/python3",
            str(script_path),
            "--tty",
            "ttys123",
            "--cwd",
            "/tmp/work",
            "--data-dir",
            str(tmp_path),
            "--session-name",
            "controller",
            "--group-name",
            "main",
            "--description",
            "coordinator",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0
    assert launch_file.exists()
    assert "\"session_name\": \"controller\"" in launch_file.read_text()
