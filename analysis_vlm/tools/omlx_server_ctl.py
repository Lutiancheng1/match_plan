#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_API_KEY = "sk-1234"
DEFAULT_MODEL_DIR = Path("/Users/niannianshunjing/.omlx/models")
DEFAULT_LOG_PATH = Path("/Users/niannianshunjing/Library/Application Support/oMLX/logs/manual_serve_controlled.log")
OMLX_CLI = Path("/Applications/oMLX.app/Contents/MacOS/omlx-cli")


def list_serve_processes() -> list[dict]:
    out = subprocess.check_output(["ps", "-axo", "pid=,ppid=,command="], text=True)
    rows: list[dict] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_txt, ppid_txt, command = line.split(None, 2)
            pid = int(pid_txt)
            ppid = int(ppid_txt)
        except ValueError:
            continue
        command_norm = f" {command} "
        if ("omlx-cli" in command or " -m omlx.cli serve " in command_norm) and " serve " in command_norm:
            rows.append({"pid": pid, "ppid": ppid, "command": command})
    return rows


def probe_models(host: str, port: int, api_key: str, timeout: int = 5) -> dict | None:
    req = urllib.request.Request(
        f"http://{host}:{port}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def wait_healthy(host: str, port: int, api_key: str, deadline_sec: int) -> bool:
    deadline = time.time() + deadline_sec
    while time.time() < deadline:
        if probe_models(host, port, api_key, timeout=3):
            return True
        time.sleep(1)
    return False


def start_server(host: str, port: int, api_key: str, model_dir: Path, log_path: Path) -> int:
    if probe_models(host, port, api_key, timeout=3):
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as fh:
        process = subprocess.Popen(
            [
                str(OMLX_CLI),
                "serve",
                "--model-dir",
                str(model_dir),
                "--host",
                host,
                "--port",
                str(port),
                "--log-level",
                "info",
            ],
            stdout=fh,
            stderr=fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    if not wait_healthy(host, port, api_key, deadline_sec=30):
        return 1
    print(json.dumps({"status": "running", "pid": process.pid, "log_path": str(log_path)}, ensure_ascii=False))
    return 0


def stop_server() -> int:
    procs = list_serve_processes()
    for proc in procs:
        try:
            os.kill(proc["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 10
    while time.time() < deadline:
        if not list_serve_processes():
            print(json.dumps({"status": "stopped"}, ensure_ascii=False))
            return 0
        time.sleep(0.5)
    for proc in list_serve_processes():
        try:
            os.kill(proc["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
    print(json.dumps({"status": "stopped_force"}, ensure_ascii=False))
    return 0


def status_server(host: str, port: int, api_key: str) -> int:
    payload = {
        "healthy": bool(probe_models(host, port, api_key, timeout=3)),
        "processes": list_serve_processes(),
        "host": host,
        "port": port,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Control local oMLX server for analysis_vlm benchmarks.")
    sub = parser.add_subparsers(dest="action", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=DEFAULT_HOST)
    common.add_argument("--port", type=int, default=DEFAULT_PORT)
    common.add_argument("--api-key", default=DEFAULT_API_KEY)

    start_p = sub.add_parser("start", parents=[common])
    start_p.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    start_p.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)

    sub.add_parser("stop")
    sub.add_parser("status", parents=[common])

    args = parser.parse_args()
    if args.action == "start":
        return start_server(args.host, args.port, args.api_key, args.model_dir, args.log_path)
    if args.action == "stop":
        return stop_server()
    if args.action == "status":
        return status_server(args.host, args.port, args.api_key)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
