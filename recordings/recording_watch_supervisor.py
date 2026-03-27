#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from openclaw_recording_status import is_pid_alive, load_json
from openclaw_recording_watch import WATCH_RUNTIME_DIR


PROJECT_DIR = Path("/Users/niannianshunjing/match_plan/recordings")
WATCH_PATH = PROJECT_DIR / "openclaw_recording_watch.py"
STATUS_NOTIFY_PATH = PROJECT_DIR / "openclaw_recording_watch_status_notify.py"
DEFAULT_OUTPUT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_recordings")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def supervisor_state_path(job_id: str) -> Path:
    return WATCH_RUNTIME_DIR / f"{job_id}.supervisor.json"


def ensure_runtime_dir() -> None:
    WATCH_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def default_state(args: argparse.Namespace) -> dict:
    return {
        "job_id": args.job_id,
        "config": args.config,
        "browser": args.browser,
        "output_root": args.output_root,
        "interval_minutes": int(args.interval_minutes),
        "progress_interval_minutes": int(args.progress_interval_minutes),
        "max_streams": int(args.max_streams),
        "channel": args.channel,
        "target": args.target,
        "account": args.account,
        "screenshot_scope": args.screenshot_scope,
        "stop_at": args.stop_at,
        "max_runtime_minutes": int(args.max_runtime_minutes),
        "watch_pid": 0,
        "status_notify_pid": 0,
        "started_at": "",
        "updated_at": now_iso(),
        "stopped_at": "",
        "watch_log": str(WATCH_RUNTIME_DIR / f"{args.job_id}.supervisor.watch.log"),
        "status_notify_log": str(WATCH_RUNTIME_DIR / f"{args.job_id}.supervisor.status_notify.log"),
    }


def load_state(job_id: str) -> dict:
    return load_json(supervisor_state_path(job_id)) or {}


def save_state(job_id: str, payload: dict) -> None:
    payload["updated_at"] = now_iso()
    ensure_runtime_dir()
    supervisor_state_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_watch_command(state: dict) -> list[str]:
    cmd = [
        sys.executable,
        str(WATCH_PATH),
        "--config",
        str(state["config"]),
        "--job-id",
        str(state["job_id"]),
        "--browser",
        str(state["browser"]),
        "--output-root",
        str(state["output_root"]),
        "--interval-minutes",
        str(state["interval_minutes"]),
        "--loop",
        "--max-streams",
        str(state["max_streams"]),
        "--progress-interval-minutes",
        str(state["progress_interval_minutes"]),
    ]
    if state.get("stop_at"):
        cmd.extend(["--stop-at", str(state["stop_at"])])
    if int(state.get("max_runtime_minutes") or 0) > 0:
        cmd.extend(["--max-runtime-minutes", str(int(state["max_runtime_minutes"]))])
    return cmd


def build_status_notify_command(state: dict) -> list[str]:
    return [
        sys.executable,
        str(STATUS_NOTIFY_PATH),
        "--job-id",
        str(state["job_id"]),
        "--interval-minutes",
        str(state["progress_interval_minutes"]),
        "--channel",
        str(state["channel"]),
        "--target",
        str(state["target"]),
        "--account",
        str(state["account"]),
        "--screenshot-scope",
        str(state["screenshot_scope"]),
        "--loop",
    ]


def spawn_detached(cmd: list[str], log_path: str) -> int:
    log_file = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_DIR),
        start_new_session=True,
        close_fds=True,
    )
    log_file.close()
    return proc.pid


def terminate_pid(pid: int, timeout_seconds: int = 10) -> bool:
    if not is_pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return True
    deadline = time.time() + 3
    while time.time() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.2)
    return not is_pid_alive(pid)


def normalize_state(args: argparse.Namespace) -> dict:
    existing = load_state(args.job_id)
    payload = default_state(args)
    if existing:
        payload.update(existing)
    payload.update(
        {
            "job_id": args.job_id,
            "config": args.config or payload["config"],
            "browser": args.browser or payload["browser"],
            "output_root": args.output_root or payload["output_root"],
            "interval_minutes": int(args.interval_minutes or payload["interval_minutes"]),
            "progress_interval_minutes": int(args.progress_interval_minutes if args.progress_interval_minutes is not None else payload["progress_interval_minutes"]),
            "max_streams": int(args.max_streams if args.max_streams is not None else payload["max_streams"]),
            "channel": args.channel or payload["channel"],
            "target": args.target or payload["target"],
            "account": args.account if args.account is not None else payload["account"],
            "screenshot_scope": args.screenshot_scope or payload["screenshot_scope"],
            "stop_at": args.stop_at if args.stop_at is not None else payload.get("stop_at", ""),
            "max_runtime_minutes": int(args.max_runtime_minutes if args.max_runtime_minutes is not None else payload.get("max_runtime_minutes", 0)),
        }
    )
    return payload


def ensure_watch_running(state: dict) -> tuple[dict, bool]:
    started = False
    watch_pid = int(state.get("watch_pid") or 0)
    if not is_pid_alive(watch_pid):
        state["watch_pid"] = spawn_detached(build_watch_command(state), state["watch_log"])
        started = True
    return state, started


def ensure_status_notify_running(state: dict) -> tuple[dict, bool]:
    if int(state.get("progress_interval_minutes") or 0) <= 0:
        pid = int(state.get("status_notify_pid") or 0)
        if pid > 0:
            terminate_pid(pid)
        state["status_notify_pid"] = 0
        return state, False
    started = False
    notify_pid = int(state.get("status_notify_pid") or 0)
    if not is_pid_alive(notify_pid):
        state["status_notify_pid"] = spawn_detached(build_status_notify_command(state), state["status_notify_log"])
        started = True
    return state, started


def command_start(args: argparse.Namespace) -> int:
    state = normalize_state(args)
    state["started_at"] = state.get("started_at") or now_iso()
    state["stopped_at"] = ""
    state, watch_started = ensure_watch_running(state)
    state, notify_started = ensure_status_notify_running(state)
    save_state(args.job_id, state)
    payload = {
        "job_id": args.job_id,
        "action": "start",
        "watch_pid": state.get("watch_pid", 0),
        "status_notify_pid": state.get("status_notify_pid", 0),
        "watch_started": watch_started,
        "status_notify_started": notify_started,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_ensure_running(args: argparse.Namespace) -> int:
    state = normalize_state(args)
    state["started_at"] = state.get("started_at") or now_iso()
    state["stopped_at"] = ""
    state, watch_started = ensure_watch_running(state)
    state, notify_started = ensure_status_notify_running(state)
    save_state(args.job_id, state)
    payload = {
        "job_id": args.job_id,
        "action": "ensure-running",
        "watch_pid": state.get("watch_pid", 0),
        "status_notify_pid": state.get("status_notify_pid", 0),
        "watch_started": watch_started,
        "status_notify_started": notify_started,
        "watch_alive": is_pid_alive(int(state.get("watch_pid") or 0)),
        "status_notify_alive": is_pid_alive(int(state.get("status_notify_pid") or 0)),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = load_state(args.job_id)
    if not state:
        print(json.dumps({"job_id": args.job_id, "state": "missing"}, ensure_ascii=False, indent=2))
        return 1
    payload = {
        "job_id": args.job_id,
        "state": "running" if is_pid_alive(int(state.get("watch_pid") or 0)) else "stopped",
        "watch_pid": state.get("watch_pid", 0),
        "status_notify_pid": state.get("status_notify_pid", 0),
        "watch_alive": is_pid_alive(int(state.get("watch_pid") or 0)),
        "status_notify_alive": is_pid_alive(int(state.get("status_notify_pid") or 0)),
        "config": state.get("config", ""),
        "output_root": state.get("output_root", ""),
        "interval_minutes": state.get("interval_minutes", 0),
        "progress_interval_minutes": state.get("progress_interval_minutes", 0),
        "max_streams": state.get("max_streams", 0),
        "watch_log": state.get("watch_log", ""),
        "status_notify_log": state.get("status_notify_log", ""),
        "started_at": state.get("started_at", ""),
        "updated_at": state.get("updated_at", ""),
        "stopped_at": state.get("stopped_at", ""),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_stop(args: argparse.Namespace) -> int:
    state = load_state(args.job_id)
    if not state:
        print(json.dumps({"job_id": args.job_id, "action": "stop", "stopped": False, "reason": "missing_state"}, ensure_ascii=False, indent=2))
        return 0
    watch_pid = int(state.get("watch_pid") or 0)
    notify_pid = int(state.get("status_notify_pid") or 0)
    watch_stopped = terminate_pid(watch_pid) if watch_pid > 0 else True
    notify_stopped = terminate_pid(notify_pid) if notify_pid > 0 else True
    state["watch_pid"] = 0
    state["status_notify_pid"] = 0
    state["stopped_at"] = now_iso()
    save_state(args.job_id, state)
    payload = {
        "job_id": args.job_id,
        "action": "stop",
        "watch_stopped": watch_stopped,
        "status_notify_stopped": notify_stopped,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_restart(args: argparse.Namespace) -> int:
    command_stop(args)
    return command_start(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage long-running watch/status processes for recordings.")
    parser.add_argument("command", choices=["start", "ensure-running", "status", "stop", "restart"])
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--config", default=str(PROJECT_DIR / "watch_targets_all_live_bound_only.json"))
    parser.add_argument("--browser", default="safari")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--interval-minutes", type=int, default=2)
    parser.add_argument("--progress-interval-minutes", type=int, default=30)
    parser.add_argument("--max-streams", type=int, default=0)
    parser.add_argument("--channel", default="feishu")
    parser.add_argument("--target", default="oc_d8caa357cf6943f7a0b2917a2488876a")
    parser.add_argument("--account", default="legacy")
    parser.add_argument("--screenshot-scope", default="desktop")
    parser.add_argument("--stop-at", default="")
    parser.add_argument("--max-runtime-minutes", type=int, default=0)
    return parser


def main() -> int:
    ensure_runtime_dir()
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "start":
        return command_start(args)
    if args.command == "ensure-running":
        return command_ensure_running(args)
    if args.command == "status":
        return command_status(args)
    if args.command == "stop":
        return command_stop(args)
    if args.command == "restart":
        return command_restart(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
