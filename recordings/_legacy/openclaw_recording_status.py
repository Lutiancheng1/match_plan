#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


PROJECT_DIR = Path("/Users/niannianshunjing/match_plan/recordings")
LATEST_JOB_PATH = PROJECT_DIR / "last_openclaw_recording.json"
RECORDING_PROCESS_MARKERS = (
    "run_auto_capture.py",
    "ffmpeg",
    "window_capture_helper",
    "window_capture.swift",
    "screencapture ",
)


def is_pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def find_session_processes(session_dir: Path, recording_only: bool = False) -> list[str]:
    current_pid = os.getpid()
    try:
        completed = subprocess.run(
            ["ps", "ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    needle = str(session_dir)
    lines = []
    for line in completed.stdout.splitlines():
        text = line.strip()
        if not text or needle not in text:
            continue
        pid_text = text.split(None, 1)[0] if text else ""
        try:
            pid = int(pid_text)
        except Exception:
            pid = None
        if pid == current_pid:
            continue
        if "openclaw_recording_status.py" in text:
            continue
        if recording_only and not any(marker in text for marker in RECORDING_PROCESS_MARKERS):
            continue
        lines.append(text)
    return lines


def read_recent_log_lines(path: Path, limit: int = 10) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(errors="ignore").splitlines()[-limit:]


def build_completed_summary(session_dir: Path, result: dict) -> dict:
    streams = result.get("streams") or result.get("results") or []
    completed = sum(1 for stream in streams if str(stream.get("status", "")).lower() in {"completed", "ok", "success"})
    rows = []
    for stream in streams:
        rows.append(
            {
                "name": stream.get("match_display") or stream.get("match") or stream.get("title") or stream.get("match_id"),
                "status": stream.get("status"),
                "duration_sec": stream.get("total_duration_sec"),
                "data_binding_status": stream.get("data_binding_status"),
                "matched_rows": stream.get("matched_rows"),
                "recording_note": stream.get("recording_note"),
            }
        )
    return {
        "state": "completed",
        "session_dir": str(session_dir),
        "completed_streams": completed,
        "total_streams": len(streams),
        "streams": rows,
        "watch": result.get("watch") or {},
        "progress": result.get("progress") or {},
    }


def get_session_status(session_dir: Path, launch: dict | None = None) -> dict:
    launch = launch or load_json(session_dir / "openclaw_launch.json") or {}
    result = load_json(session_dir / "session_result.json")
    if result:
        return build_completed_summary(session_dir, result)

    session_processes = find_session_processes(session_dir)
    recording_processes = find_session_processes(session_dir, recording_only=True)
    inferred_running = bool(recording_processes)
    payload = {
        "state": "running" if is_pid_alive(launch.get("run_pid")) or inferred_running else "unknown",
        "session_dir": str(session_dir),
        "run_pid": launch.get("run_pid"),
        "notify_pid": launch.get("notify_pid"),
        "progress_pid": launch.get("progress_pid"),
        "session_processes": session_processes[-8:],
        "recording_processes": recording_processes[-8:],
        "recording_log_tail": read_recent_log_lines(session_dir / "recording.log"),
        "process_log_tail": read_recent_log_lines(session_dir / "openclaw_process.log"),
        "watch": load_json(session_dir / "watch_runtime.json") or launch.get("watch") or {},
        "selected_matches": load_json(Path(launch.get("selected_matches_file", ""))) if launch.get("selected_matches_file") else None,
    }
    if payload["selected_matches"] is None:
        payload.pop("selected_matches")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the status of an OpenClaw-started recording job.")
    parser.add_argument("--session-dir", default="")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    session_dir: Path | None = None
    launch = None

    if args.session_dir:
        session_dir = Path(args.session_dir)
    else:
        launch = load_json(LATEST_JOB_PATH)
        if launch:
            session_dir = Path(launch["session_dir"])

    if session_dir is None:
        print(json.dumps({"state": "missing", "error": "No session information available."}, ensure_ascii=False, indent=2))
        return 1

    launch = launch or load_json(session_dir / "openclaw_launch.json") or {}
    payload = get_session_status(session_dir, launch=launch)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"state: {payload.get('state')}")
        print(f"session_dir: {payload.get('session_dir')}")
        if payload.get("state") == "completed":
            print(f"streams: {payload.get('completed_streams')}/{payload.get('total_streams')} completed")
            for row in payload.get("streams", []):
                print(
                    f"- {row.get('name')} | status={row.get('status')} | duration_sec={row.get('duration_sec')} "
                    f"| data={row.get('data_binding_status')} | matched={row.get('matched_rows')}"
                )
        else:
            print("recording_log_tail:")
            for line in payload.get("recording_log_tail", []):
                print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
