#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
RECORDINGS_DIR = SCRIPT_DIR.parent
WATCH_RUNTIME_DIR = RECORDINGS_DIR / "watch_runtime"
DEFAULT_RUNTIME_DIR = WATCH_RUNTIME_DIR / "pion_gst_dispatcher"
DEFAULT_JOB_ID = "pion_gst_ft_bound"
DEFAULT_RECORDINGS_ROOT = Path(os.environ.get("MATCH_RECORDINGS_ROOT", "/Volumes/990 PRO PCIe 4T/match_plan_recordings"))
TEST_SESSION_PREFIXES = [
    "session_pgstapp_test_",
    "session_pgstall_",
    "session_pgst_probe_",
    "session_lkall_",
    "session_lkd_",
    "session_lkdw_",
]
TEST_RUNTIME_NAMES = [
    "pgstapp_test_dispatcher",
    "pion_gst_all_unbound_test.supervisor.json",
    "pion_gst_all_unbound_test.dispatcher.log",
    "pion_gst_mac_app_test.supervisor.json",
    "pion_gst_mac_app_test.dispatcher.log",
]
FORMAL_SESSION_PREFIXES = [
    "session_pgstapp_",
]
FORMAL_RUNTIME_NAMES = [
    "pgstapp_dispatcher",
    "pion_gst_mac_app_formal.supervisor.json",
    "pion_gst_mac_app_formal.dispatcher.log",
]
ACTIVE_WORKER_STATES = {
    "",
    "initializing",
    "polling",
    "starting",
    "connected",
    "recording",
    "retrying",
    "stopping",
}

DISPATCHER_PATH = SCRIPT_DIR / "pion_gst_dispatcher.py"
DEFAULT_PATH_PREFIX = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def state_path(job_id: str) -> Path:
    return WATCH_RUNTIME_DIR / f"{job_id}.supervisor.json"


def ensure_runtime_dir() -> None:
    WATCH_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def default_state(args: argparse.Namespace) -> dict:
    return {
        "job_id": args.job_id,
        "browser": args.browser,
        "gtypes": args.gtypes,
        "max_streams": int(args.max_streams),
        "discover_interval_seconds": int(args.discover_interval_seconds),
        "loop_interval_seconds": int(args.loop_interval_seconds),
        "segment_minutes": int(args.segment_minutes),
        "max_duration_minutes": int(args.max_duration_minutes),
        "archive_width": int(getattr(args, "archive_width", 960)),
        "archive_height": int(getattr(args, "archive_height", 540)),
        "archive_bitrate_kbps": int(getattr(args, "archive_bitrate_kbps", 5000)),
        "hls_width": int(getattr(args, "hls_width", 960)),
        "hls_height": int(getattr(args, "hls_height", 540)),
        "hls_bitrate_kbps": int(getattr(args, "hls_bitrate_kbps", 3500)),
        "skip_data_binding": bool(args.skip_data_binding),
        "allow_unbound": bool(args.allow_unbound),
        "chain_tag": str(args.chain_tag),
        "runtime_dir": str(Path(args.runtime_dir).resolve()),
        "notify_channel": str(getattr(args, "notify_channel", "") or ""),
        "notify_account": str(getattr(args, "notify_account", "") or ""),
        "notify_target": str(getattr(args, "notify_target", "") or ""),
        "notify_on_new_live": bool(getattr(args, "notify_on_new_live", False)),
        "notify_on_recording_started": bool(getattr(args, "notify_on_recording_started", False)),
        "notify_on_recording_completed": bool(getattr(args, "notify_on_recording_completed", False)),
        "notify_on_recording_failed": bool(getattr(args, "notify_on_recording_failed", False)),
        "dispatcher_pid": 0,
        "started_at": "",
        "updated_at": now_iso(),
        "stopped_at": "",
        "dispatcher_log": str(WATCH_RUNTIME_DIR / f"{args.job_id}.dispatcher.log"),
    }


def load_state(job_id: str) -> dict:
    return load_json(state_path(job_id)) or {}


def save_state(job_id: str, payload: dict) -> None:
    payload["updated_at"] = now_iso()
    ensure_runtime_dir()
    state_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_state(args: argparse.Namespace) -> dict:
    payload = default_state(args)
    existing = load_state(args.job_id)
    if existing:
        payload.update(existing)
    payload.update(
        {
            "job_id": args.job_id,
            "browser": args.browser or payload["browser"],
            "gtypes": args.gtypes or payload["gtypes"],
            "max_streams": int(args.max_streams if args.max_streams is not None else payload["max_streams"]),
            "discover_interval_seconds": int(
                args.discover_interval_seconds
                if args.discover_interval_seconds is not None
                else payload["discover_interval_seconds"]
            ),
            "loop_interval_seconds": int(
                args.loop_interval_seconds
                if args.loop_interval_seconds is not None
                else payload["loop_interval_seconds"]
            ),
            "segment_minutes": int(
                args.segment_minutes if args.segment_minutes is not None else payload["segment_minutes"]
            ),
            "max_duration_minutes": int(
                args.max_duration_minutes
                if args.max_duration_minutes is not None
                else payload["max_duration_minutes"]
            ),
            "archive_width": int(
                args.archive_width if getattr(args, "archive_width", None) is not None else payload.get("archive_width", 960)
            ),
            "archive_height": int(
                args.archive_height if getattr(args, "archive_height", None) is not None else payload.get("archive_height", 540)
            ),
            "archive_bitrate_kbps": int(
                args.archive_bitrate_kbps if getattr(args, "archive_bitrate_kbps", None) is not None else payload.get("archive_bitrate_kbps", 5000)
            ),
            "hls_width": int(
                args.hls_width if getattr(args, "hls_width", None) is not None else payload.get("hls_width", 960)
            ),
            "hls_height": int(
                args.hls_height if getattr(args, "hls_height", None) is not None else payload.get("hls_height", 540)
            ),
            "hls_bitrate_kbps": int(
                args.hls_bitrate_kbps if getattr(args, "hls_bitrate_kbps", None) is not None else payload.get("hls_bitrate_kbps", 3500)
            ),
            "skip_data_binding": bool(args.skip_data_binding),
            "allow_unbound": bool(args.allow_unbound),
            "chain_tag": str(args.chain_tag or payload.get("chain_tag", "pgst")),
            "runtime_dir": str(Path(args.runtime_dir or payload["runtime_dir"]).resolve()),
            "notify_channel": str(args.notify_channel or payload.get("notify_channel", "")),
            "notify_account": str(args.notify_account or payload.get("notify_account", "")),
            "notify_target": str(args.notify_target or payload.get("notify_target", "")),
            "notify_on_new_live": bool(args.notify_on_new_live),
            "notify_on_recording_started": bool(args.notify_on_recording_started),
            "notify_on_recording_completed": bool(args.notify_on_recording_completed),
            "notify_on_recording_failed": bool(args.notify_on_recording_failed),
        }
    )
    return payload


def build_dispatcher_command(state: dict) -> list[str]:
    cmd = [
        sys.executable,
        str(DISPATCHER_PATH),
        "--browser",
        str(state["browser"]),
        "--gtypes",
        str(state["gtypes"]),
        "--max-streams",
        str(int(state["max_streams"])),
        "--discover-interval-seconds",
        str(int(state["discover_interval_seconds"])),
        "--loop-interval-seconds",
        str(int(state["loop_interval_seconds"])),
        "--segment-minutes",
        str(int(state["segment_minutes"])),
        "--max-duration-minutes",
        str(int(state["max_duration_minutes"])),
        "--archive-width",
        str(int(state.get("archive_width", 960))),
        "--archive-height",
        str(int(state.get("archive_height", 540))),
        "--archive-bitrate-kbps",
        str(int(state.get("archive_bitrate_kbps", 5000))),
        "--hls-width",
        str(int(state.get("hls_width", 960))),
        "--hls-height",
        str(int(state.get("hls_height", 540))),
        "--hls-bitrate-kbps",
        str(int(state.get("hls_bitrate_kbps", 3500))),
        "--chain-tag",
        str(state.get("chain_tag", "pgst")),
        "--runtime-dir",
        str(state["runtime_dir"]),
    ]
    if state.get("skip_data_binding"):
        cmd.append("--skip-data-binding")
    if state.get("allow_unbound"):
        cmd.append("--allow-unbound")
    if state.get("notify_channel"):
        cmd.extend(["--notify-channel", str(state.get("notify_channel", ""))])
    if state.get("notify_account"):
        cmd.extend(["--notify-account", str(state.get("notify_account", ""))])
    if state.get("notify_target"):
        cmd.extend(["--notify-target", str(state.get("notify_target", ""))])
    if state.get("notify_on_new_live"):
        cmd.append("--notify-on-new-live")
    if state.get("notify_on_recording_started"):
        cmd.append("--notify-on-recording-started")
    if state.get("notify_on_recording_completed"):
        cmd.append("--notify-on-recording-completed")
    if state.get("notify_on_recording_failed"):
        cmd.append("--notify-on-recording-failed")
    return cmd


def spawn_detached(cmd: list[str], log_path: str) -> int:
    log_file = open(log_path, "a", encoding="utf-8")
    env = os.environ.copy()
    path_items = list(DEFAULT_PATH_PREFIX)
    current_path = str(env.get("PATH", "") or "")
    for item in current_path.split(":"):
        if item and item not in path_items:
            path_items.append(item)
    env["PATH"] = ":".join(path_items)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(RECORDINGS_DIR.parent),
        env=env,
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
    return not is_pid_alive(pid)


def terminate_many_pids(pids: list[int], timeout_seconds: int = 6) -> list[dict]:
    targets = [int(pid) for pid in pids if isinstance(pid, int) and pid > 0 and is_pid_alive(int(pid))]
    if not targets:
        return []
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + timeout_seconds
    alive = set(pid for pid in targets if is_pid_alive(pid))
    while alive and time.time() < deadline:
        time.sleep(0.25)
        alive = set(pid for pid in alive if is_pid_alive(pid))
    for pid in list(alive):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.2)
    return [{"pid": pid, "stopped": not is_pid_alive(pid)} for pid in targets]


def remove_path(target: Path) -> dict:
    existed = target.exists()
    kind = "dir" if target.is_dir() else "file"
    if not existed:
        return {"path": str(target), "removed": False, "kind": kind, "missing": True}
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"path": str(target), "removed": True, "kind": kind, "missing": False}
    except Exception as exc:
        return {"path": str(target), "removed": False, "kind": kind, "missing": False, "error": str(exc)}


def recordings_root() -> Path:
    return DEFAULT_RECORDINGS_ROOT


def cleanup_test_sessions() -> list[dict]:
    root = recordings_root()
    results: list[dict] = []
    if not root.exists():
        return results
    for date_dir in root.iterdir():
        if not date_dir.is_dir():
            continue
        for child in date_dir.iterdir():
            if not child.is_dir():
                continue
            if any(child.name.startswith(prefix) for prefix in TEST_SESSION_PREFIXES):
                results.append(remove_path(child))
    return results


def collect_test_sessions_preview() -> dict:
    root = recordings_root()
    total = 0
    by_date: dict[str, int] = {}
    samples: list[str] = []
    if not root.exists():
        return {
            "recordings_root": str(root),
            "session_count": 0,
            "date_counts": {},
            "sample_sessions": [],
        }
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir():
            continue
        count = 0
        for child in sorted(date_dir.iterdir()):
            if not child.is_dir():
                continue
            if any(child.name.startswith(prefix) for prefix in TEST_SESSION_PREFIXES):
                total += 1
                count += 1
                if len(samples) < 20:
                    samples.append(str(child))
        if count:
            by_date[date_dir.name] = count
    return {
        "recordings_root": str(root),
        "session_count": total,
        "date_counts": by_date,
        "sample_sessions": samples,
    }


def cleanup_test_runtime_files() -> list[dict]:
    results: list[dict] = []
    for name in TEST_RUNTIME_NAMES:
        results.append(remove_path(WATCH_RUNTIME_DIR / name))
    return results


def formal_runtime_files() -> list[dict]:
    results: list[dict] = []
    for name in FORMAL_RUNTIME_NAMES:
        results.append(remove_path(WATCH_RUNTIME_DIR / name))
    return results


def cleanup_test_jobs() -> list[dict]:
    cleanup_args = argparse.Namespace(
        job_id="pion_gst_mac_app_test",
        browser="app",
        gtypes="FT",
        max_streams=0,
        discover_interval_seconds=20,
        loop_interval_seconds=1,
        segment_minutes=1,
        max_duration_minutes=0,
        skip_data_binding=True,
        allow_unbound=True,
        chain_tag="pgstapp_test",
        runtime_dir=str(WATCH_RUNTIME_DIR / "pgstapp_test_dispatcher"),
        notify_channel="",
        notify_account="",
        notify_target="",
        notify_on_new_live=False,
        notify_on_recording_started=False,
        notify_on_recording_completed=False,
        notify_on_recording_failed=False,
    )
    command_stop(cleanup_args)
    return [
        {
            "job_id": "pion_gst_mac_app_test",
            "state_file": str(state_path("pion_gst_mac_app_test")),
        }
    ]


def cleanup_formal_jobs() -> list[dict]:
    cleanup_args = argparse.Namespace(job_id="pion_gst_mac_app_formal")
    command_stop(cleanup_args)
    return [
        {
            "job_id": "pion_gst_mac_app_formal",
            "state_file": str(state_path("pion_gst_mac_app_formal")),
        }
    ]


def dispatcher_runtime_summary(runtime_dir: str) -> dict:
    runtime = Path(runtime_dir)
    dispatcher_state = load_json(runtime / "dispatcher_state.json") or {}
    workers = dispatcher_state.get("workers", []) or []
    alive_workers = 0
    recording_workers = 0
    for item in workers:
        pid = int(item.get("pid") or 0)
        alive = is_pid_alive(pid)
        if alive:
            alive_workers += 1
            if str(item.get("worker_state", "")) == "recording":
                recording_workers += 1
    return {
        "runtime_dir": str(runtime),
        "worker_count": len(workers),
        "alive_worker_count": alive_workers,
        "recording_worker_count": recording_workers,
        "recent_finished_count": len(dispatcher_state.get("recent_finished", []) or []),
    }


def reset_runtime_state(runtime_dir: str) -> None:
    runtime = Path(runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    state = load_json(runtime / "dispatcher_state.json") or {}
    payload = {
        "updated_at": now_iso(),
        "workers": [],
        "pending_queue": [],
        "recent_finished": state.get("recent_finished", []) or [],
    }
    (runtime / "dispatcher_state.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def session_mode_for_name(name: str) -> str:
    if any(name.startswith(prefix) for prefix in TEST_SESSION_PREFIXES):
        return "test"
    if any(name.startswith(prefix) for prefix in FORMAL_SESSION_PREFIXES):
        return "formal"
    return "unknown"


def runtime_dir_for_mode(mode: str) -> Path | None:
    if mode == "test":
        return WATCH_RUNTIME_DIR / "pgstapp_test_dispatcher"
    if mode == "formal":
        return WATCH_RUNTIME_DIR / "pgstapp_dispatcher"
    return None


def job_id_for_mode(mode: str) -> str | None:
    if mode == "test":
        return "pion_gst_mac_app_test"
    if mode == "formal":
        return "pion_gst_mac_app_formal"
    return None


def collect_active_session_map() -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for mode in ("formal", "test"):
        runtime = runtime_dir_for_mode(mode)
        if runtime is None:
            continue
        status_dir = runtime / "worker_status"
        if not status_dir.exists():
            continue
        for path in sorted(status_dir.glob("*.json")):
            payload = load_json(path) or {}
            session_dir = str(payload.get("sessionDir", "")).strip()
            worker_state = str(payload.get("state", "")).strip().lower()
            if not session_dir or worker_state not in ACTIVE_WORKER_STATES:
                continue
            mapping[str(Path(session_dir).resolve())] = {
                "mode": mode,
                "job_id": job_id_for_mode(mode) or "",
                "worker_state": worker_state,
                "title": str(payload.get("teams", "")).strip(),
                "status_path": str(path),
            }
    return mapping


def summarize_session_dir(session_dir: Path, active_map: dict[str, dict]) -> dict:
    session_dir = session_dir.resolve()
    mode = session_mode_for_name(session_dir.name)
    child_dirs = sorted([p for p in session_dir.iterdir() if p.is_dir()]) if session_dir.exists() else []
    match_dir = child_dirs[0] if child_dirs else None
    full_videos = sorted(session_dir.glob("**/*__full.mp4"))
    segment_files = sorted(session_dir.glob("**/*__seg_*.mkv"))
    hls_playlists = sorted(session_dir.glob("**/hls/playlist.m3u8"))
    active = active_map.get(str(session_dir), {})
    return {
        "id": str(session_dir),
        "session_dir": str(session_dir),
        "session_name": session_dir.name,
        "mode": mode,
        "date": session_dir.parent.name,
        "match_dir_name": match_dir.name if match_dir else "",
        "active": bool(active),
        "active_state": str(active.get("worker_state", "")),
        "active_job_id": str(active.get("job_id", "")),
        "title": str(active.get("title", "")),
        "full_video_count": len(full_videos),
        "segment_count": len(segment_files),
        "has_hls": bool(hls_playlists),
        "can_delete_directly": not bool(active),
    }


def list_app_sessions() -> list[dict]:
    root = recordings_root()
    active_map = collect_active_session_map()
    sessions: list[dict] = []
    if not root.exists():
        return sessions
    prefixes = tuple(TEST_SESSION_PREFIXES + FORMAL_SESSION_PREFIXES)
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir():
            continue
        for child in sorted(date_dir.iterdir()):
            if not child.is_dir():
                continue
            if not child.name.startswith(prefixes):
                continue
            sessions.append(summarize_session_dir(child, active_map))
    sessions.sort(key=lambda item: (item.get("date", ""), item.get("session_name", "")), reverse=True)
    return sessions


def prune_runtime_for_deleted_sessions(session_dirs: set[str]) -> None:
    for mode in ("formal", "test"):
        runtime = runtime_dir_for_mode(mode)
        if runtime is None:
            continue
        status_dir = runtime / "worker_status"
        if status_dir.exists():
            for path in status_dir.glob("*.json"):
                payload = load_json(path) or {}
                session_dir = str(payload.get("sessionDir", "")).strip()
                if session_dir and str(Path(session_dir).resolve()) in session_dirs:
                    try:
                        path.unlink()
                    except Exception:
                        pass
        state_path_runtime = runtime / "dispatcher_state.json"
        payload = load_json(state_path_runtime) or {}
        workers = payload.get("workers", []) or []
        payload["workers"] = [
            item for item in workers
            if str(Path(str(item.get("worker_session_dir", "") or item.get("sessionDir", ""))).resolve()) not in session_dirs
        ]
        payload["updated_at"] = now_iso()
        try:
            runtime.mkdir(parents=True, exist_ok=True)
            state_path_runtime.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def mark_worker_statuses_stopped(runtime_dir: str) -> None:
    status_dir = Path(runtime_dir) / "worker_status"
    if not status_dir.exists():
        return
    for path in status_dir.glob("*.json"):
        payload = load_json(path) or {}
        state = str(payload.get("state", ""))
        if state in {"completed", "failed", "skipped", "stopped"}:
            continue
        payload["state"] = "stopped"
        payload["stopReason"] = "manual_stop"
        payload["updatedAt"] = now_iso()
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            continue


def list_alive_worker_pids(runtime_dir: str) -> list[int]:
    runtime = Path(runtime_dir)
    dispatcher_state = load_json(runtime / "dispatcher_state.json") or {}
    pids: set[int] = set()
    # From dispatcher state
    for item in dispatcher_state.get("workers", []) or []:
        pid = int(item.get("pid") or 0)
        if pid > 0 and is_pid_alive(pid):
            pids.add(pid)
    # Also find any orphan worker/recorder processes via pgrep
    for pattern in ("run_pion_gst_direct_capture.py", "pion_livekit_gst_recorder"):
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                pid = int(line.strip())
                if pid > 0 and is_pid_alive(pid):
                    pids.add(pid)
        except Exception:
            pass
    return list(pids)


def ensure_dispatcher_running(state: dict) -> tuple[dict, bool]:
    started = False
    dispatcher_pid = int(state.get("dispatcher_pid") or 0)
    if not is_pid_alive(dispatcher_pid):
        state["dispatcher_pid"] = spawn_detached(build_dispatcher_command(state), state["dispatcher_log"])
        started = True
    return state, started


def command_start(args: argparse.Namespace) -> int:
    state = normalize_state(args)
    state["started_at"] = state.get("started_at") or now_iso()
    state["stopped_at"] = ""
    state, started = ensure_dispatcher_running(state)
    save_state(args.job_id, state)
    payload = {
        "job_id": args.job_id,
        "action": "start",
        "dispatcher_pid": state.get("dispatcher_pid", 0),
        "dispatcher_started": started,
        **dispatcher_runtime_summary(state["runtime_dir"]),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_ensure_running(args: argparse.Namespace) -> int:
    state = normalize_state(args)
    state["started_at"] = state.get("started_at") or now_iso()
    state["stopped_at"] = ""
    state, started = ensure_dispatcher_running(state)
    save_state(args.job_id, state)
    payload = {
        "job_id": args.job_id,
        "action": "ensure-running",
        "dispatcher_pid": state.get("dispatcher_pid", 0),
        "dispatcher_started": started,
        "dispatcher_alive": is_pid_alive(int(state.get("dispatcher_pid") or 0)),
        **dispatcher_runtime_summary(state["runtime_dir"]),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = load_state(args.job_id)
    if not state:
        print(json.dumps({"job_id": args.job_id, "state": "missing"}, ensure_ascii=False, indent=2))
        return 0
    payload = {
        "job_id": args.job_id,
        "state": "running" if is_pid_alive(int(state.get("dispatcher_pid") or 0)) else "stopped",
        "dispatcher_pid": state.get("dispatcher_pid", 0),
        "dispatcher_alive": is_pid_alive(int(state.get("dispatcher_pid") or 0)),
        "browser": state.get("browser", ""),
        "gtypes": state.get("gtypes", ""),
        "max_streams": state.get("max_streams", 0),
        "discover_interval_seconds": state.get("discover_interval_seconds", 0),
        "loop_interval_seconds": state.get("loop_interval_seconds", 0),
        "segment_minutes": state.get("segment_minutes", 0),
        "max_duration_minutes": state.get("max_duration_minutes", 0),
        "archive_width": state.get("archive_width", 960),
        "archive_height": state.get("archive_height", 540),
        "archive_bitrate_kbps": state.get("archive_bitrate_kbps", 5000),
        "hls_width": state.get("hls_width", 960),
        "hls_height": state.get("hls_height", 540),
        "hls_bitrate_kbps": state.get("hls_bitrate_kbps", 3500),
        "skip_data_binding": state.get("skip_data_binding", False),
        "allow_unbound": state.get("allow_unbound", False),
        "chain_tag": state.get("chain_tag", ""),
        "notify_channel": state.get("notify_channel", ""),
        "notify_account": state.get("notify_account", ""),
        "notify_target": state.get("notify_target", ""),
        "notify_on_new_live": state.get("notify_on_new_live", False),
        "notify_on_recording_started": state.get("notify_on_recording_started", False),
        "notify_on_recording_completed": state.get("notify_on_recording_completed", False),
        "notify_on_recording_failed": state.get("notify_on_recording_failed", False),
        "dispatcher_log": state.get("dispatcher_log", ""),
        "started_at": state.get("started_at", ""),
        "updated_at": state.get("updated_at", ""),
        "stopped_at": state.get("stopped_at", ""),
        **dispatcher_runtime_summary(state.get("runtime_dir", str(DEFAULT_RUNTIME_DIR))),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_stop(args: argparse.Namespace) -> int:
    state = load_state(args.job_id)
    if not state:
        print(json.dumps({"job_id": args.job_id, "action": "stop", "stopped": False, "reason": "missing_state"}, ensure_ascii=False, indent=2))
        return 0
    runtime_dir = state.get("runtime_dir", str(DEFAULT_RUNTIME_DIR))
    worker_pids = list_alive_worker_pids(runtime_dir)
    dispatcher_pid = int(state.get("dispatcher_pid") or 0)
    stopped_workers = terminate_many_pids(worker_pids)
    stopped = terminate_pid(dispatcher_pid) if dispatcher_pid > 0 else True
    state["dispatcher_pid"] = 0
    state["stopped_at"] = now_iso()
    mark_worker_statuses_stopped(runtime_dir)
    reset_runtime_state(runtime_dir)
    save_state(args.job_id, state)
    payload = {
        "job_id": args.job_id,
        "action": "stop",
        "dispatcher_stopped": stopped,
        "worker_stop_count": len(stopped_workers),
        "worker_stops": stopped_workers,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_restart(args: argparse.Namespace) -> int:
    command_stop(args)
    time.sleep(1)
    return command_start(args)


def command_cleanup_test_artifacts(args: argparse.Namespace) -> int:
    cleanup_test_jobs()
    runtime_results = cleanup_test_runtime_files()
    session_results = cleanup_test_sessions()
    payload = {
        "action": "cleanup-test-artifacts",
        "runtime_removed": [item for item in runtime_results if item.get("removed")],
        "runtime_errors": [item for item in runtime_results if item.get("error")],
        "sessions_removed_count": sum(1 for item in session_results if item.get("removed")),
        "sessions_removed": [item["path"] for item in session_results if item.get("removed")][:200],
        "session_errors": [item for item in session_results if item.get("error")][:50],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_preview_test_artifacts(args: argparse.Namespace) -> int:
    runtime_targets = [str(WATCH_RUNTIME_DIR / name) for name in TEST_RUNTIME_NAMES]
    existing_runtime_targets = [path for path in runtime_targets if Path(path).exists()]
    payload = {
        "action": "preview-test-artifacts",
        "runtime_paths": existing_runtime_targets,
        "runtime_count": len(existing_runtime_targets),
        **collect_test_sessions_preview(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_list_artifacts(args: argparse.Namespace) -> int:
    sessions = list_app_sessions()
    payload = {
        "action": "list-artifacts",
        "session_count": len(sessions),
        "sessions": sessions,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_delete_artifacts(args: argparse.Namespace) -> int:
    selected = [str(Path(item).resolve()) for item in (args.session or []) if str(item).strip()]
    active_map = collect_active_session_map()
    active_selected = [path for path in selected if path in active_map]
    stopped_jobs: list[str] = []
    if active_selected and args.stop_active:
        for job_id in sorted({str(active_map[path].get("job_id", "")) for path in active_selected if str(active_map[path].get("job_id", ""))}):
            command_stop(argparse.Namespace(job_id=job_id))
            stopped_jobs.append(job_id)
        time.sleep(1.0)
        active_map = collect_active_session_map()
    deleted: list[str] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    selected_set = set(selected)
    for session_dir in selected:
        path = Path(session_dir)
        if session_dir in active_map:
            skipped.append(
                {
                    "path": session_dir,
                    "reason": "active_requires_stop",
                    "state": str(active_map[session_dir].get("worker_state", "")),
                    "job_id": str(active_map[session_dir].get("job_id", "")),
                }
            )
            continue
        result = remove_path(path)
        if result.get("removed"):
            deleted.append(session_dir)
        elif result.get("error"):
            errors.append(result)
        else:
            skipped.append({"path": session_dir, "reason": "missing"})
    if deleted:
        prune_runtime_for_deleted_sessions(set(deleted))
    payload = {
        "action": "delete-artifacts",
        "selected_count": len(selected),
        "deleted_count": len(deleted),
        "deleted": deleted[:500],
        "stopped_jobs": stopped_jobs,
        "skipped": skipped[:200],
        "errors": errors[:100],
        "requested_stop_active": bool(args.stop_active),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pion + GStreamer dispatcher supervisor")
    parser.add_argument("command", choices=["start", "stop", "status", "restart", "ensure-running", "cleanup-test-artifacts", "preview-test-artifacts", "list-artifacts", "delete-artifacts"])
    parser.add_argument("--job-id", default=DEFAULT_JOB_ID)
    parser.add_argument("--browser", default="safari")
    parser.add_argument("--gtypes", default="FT")
    parser.add_argument("--max-streams", type=int, default=0)
    parser.add_argument("--discover-interval-seconds", type=int, default=900)
    parser.add_argument("--loop-interval-seconds", type=int, default=1)
    parser.add_argument("--segment-minutes", type=int, default=5)
    parser.add_argument("--max-duration-minutes", type=int, default=0)
    parser.add_argument("--archive-width", type=int, default=960)
    parser.add_argument("--archive-height", type=int, default=540)
    parser.add_argument("--archive-bitrate-kbps", type=int, default=5000)
    parser.add_argument("--hls-width", type=int, default=960)
    parser.add_argument("--hls-height", type=int, default=540)
    parser.add_argument("--hls-bitrate-kbps", type=int, default=3500)
    parser.add_argument("--skip-data-binding", action="store_true")
    parser.add_argument("--allow-unbound", action="store_true")
    parser.add_argument("--chain-tag", default="pgst")
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR))
    parser.add_argument("--notify-channel", default="")
    parser.add_argument("--notify-account", default="")
    parser.add_argument("--notify-target", default="")
    parser.add_argument("--notify-on-new-live", action="store_true")
    parser.add_argument("--notify-on-recording-started", action="store_true")
    parser.add_argument("--notify-on-recording-completed", action="store_true")
    parser.add_argument("--notify-on-recording-failed", action="store_true")
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument("--stop-active", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_runtime_dir()
    if args.command == "start":
        return command_start(args)
    if args.command == "stop":
        return command_stop(args)
    if args.command == "restart":
        return command_restart(args)
    if args.command == "ensure-running":
        return command_ensure_running(args)
    if args.command == "cleanup-test-artifacts":
        return command_cleanup_test_artifacts(args)
    if args.command == "preview-test-artifacts":
        return command_preview_test_artifacts(args)
    if args.command == "list-artifacts":
        return command_list_artifacts(args)
    if args.command == "delete-artifacts":
        return command_delete_artifacts(args)
    return command_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
